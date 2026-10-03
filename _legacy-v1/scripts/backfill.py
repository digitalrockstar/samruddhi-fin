"""Backfill historical SMS exports into the database.

Reads SMS Backup & Restore XML exports, runs them through the same pipeline the
Telegram webhook uses, and inserts transactions.

Idempotent via `message_id`, which is derived from the SMS epoch timestamp plus
a hash of the body - so re-running never duplicates.

Usage:
    # dry run, report only
    python -m scripts.backfill "C:/path/to/sms/*.xml" --dry-run

    # import, prefixing every message as Ashka (adjust AP-/AS- per file)
    python -m scripts.backfill "C:/path/to/sms/*.xml" --prefix AS-

    # control the ceiling
    python -m scripts.backfill "*.xml" --prefix AP- --skip-non-transactions
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "backfill")

from app.database import async_session_maker, init_db  # noqa: E402
from app.services.categorizer import Categorizer  # noqa: E402
from app.services.parser import NOT_POSTED, POSTED, parse_sms  # noqa: E402


def message_id(date_ms: str, body: str) -> int:
    """Stable synthetic id: timestamp in ms shifted, plus a body hash."""
    try:
        ts = int(date_ms)
    except (TypeError, ValueError):
        ts = 0
    h = int(hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:6], 16)
    # Keep well under a 64-bit signed int and away from real Telegram ids.
    return (ts << 8) % (2 ** 56) + h


def iter_sms(paths):
    for p in paths:
        for _, el in ET.iterparse(p, events=("end",)):
            if el.tag != "sms":
                continue
            body = el.get("body") or ""
            if body.strip():
                yield {
                    "body": body,
                    "date_ms": el.get("date") or "",
                    "address": el.get("address") or "",
                    "id": message_id(el.get("date") or "", body),
                }
            el.clear()


async def run(args) -> int:
    from app.models import Person, Transaction
    from app.services.telegram import TelegramProcessor
    from sqlalchemy import select

    paths = sorted(glob.glob(args.source))
    if not paths:
        print(f"No XML matched {args.source}")
        return 1

    print(f"files: {len(paths)}")
    for p in paths:
        print(f"  {os.path.basename(p)}")

    processor = None
    stats = Counter()
    reasons = Counter()
    sample = []

    async with async_session_maker() as db:
        await init_db()

        # Optional: import mappings from a JSON file of merchant -> category name
        if args.mappings:
            import json

            categorizer = Categorizer(db)
            from app.models import Category
            from app.services.seed import ROOTS

            root_ids = {}
            for r in ROOTS:
                row = (await db.execute(
                    select(Category).where(
                        Category.name == r["name"], Category.parent_id.is_(None)
                    )
                )).scalar_one_or_none()
                if row:
                    root_ids[r["name"]] = row.id

            with open(args.mappings, encoding="utf-8") as fh:
                data = json.load(fh)
            for pattern, spec in data.items():
                root = spec.get("root", "EXPENSE")
                leaf = spec.get("category")
                if not leaf or root not in root_ids:
                    continue
                cat = (await db.execute(
                    select(Category).where(
                        Category.name == leaf,
                        Category.parent_id == root_ids[root],
                    )
                )).scalar_one_or_none()
                if not cat:
                    continue
                await categorizer.learn(pattern, cat.id, confidence=100)
                stats["mappings"] += 1
            await db.commit()
            print(f"loaded {stats['mappings']} merchant mapping(s) from {args.mappings}")

        person = await db.get(Person, 1)
        if args.prefix:
            person = (await db.execute(
                select(Person).where(Person.prefix == args.prefix)
            )).scalar_one_or_none()
            if not person:
                person = (await db.execute(
                    select(Person).where(Person.prefix == args.prefix)
                )).scalars().first()
            if not person:
                print(f"No person with prefix {args.prefix}")
                return 1

        for row in iter_sms(paths):
            stats["seen"] += 1

            exists = (await db.execute(
                select(Transaction.id).where(Transaction.message_id == row["id"])
            )).scalar_one_or_none()
            if exists:
                stats["duplicate"] += 1
                continue

            try:
                received = datetime.fromtimestamp(int(row["date_ms"]) / 1000) \
                    if row["date_ms"] else datetime.now()
            except (ValueError, OSError, OverflowError):
                received = datetime.now()

            parsed = parse_sms(row["body"], received_at=received)

            if args.dry_run:
                if parsed.verdict != POSTED:
                    stats["skipped"] += 1
                    reasons[parsed.reason or "?"] += 1
                    continue
                if parsed.amount <= 0:
                    stats["no_amount"] += 1
                    continue
                if args.skip_needs_review and not parsed.merchant:
                    stats["no_merchant"] += 1
                    continue
                stats["would_import"] += 1
                if len(sample) < 25:
                    sample.append((
                        parsed.rule, str(parsed.kind), str(parsed.amount),
                        parsed.account_last_four, parsed.merchant,
                        parsed.txn_timestamp, row["body"][:70],
                    ))
                continue

            if processor is None:
                processor = TelegramProcessor(db)

            # Archive first, exactly like the webhook. Non-transactions are
            # stored too (so parser improvements can be re-derived later);
            # only the transaction count is affected by skipping them.
            raw = await processor.ingest_raw(
                f"{person.prefix} {row['body']}",
                external_id=row["id"],
                source="sms_backup",
                meta={"received_at": received, "chat_id": row["address"]},
            )
            if raw is None:
                stats["duplicate"] += 1
                continue

            txn, note = await processor.derive(raw)
            if txn is not None:
                stats["imported"] += 1
            else:
                stats["skipped"] += 1
                reason = raw.reason or note
                reasons[str(reason).replace("not posted (", "").rstrip(")")] += 1

        if args.dry_run:
            await db.rollback()

    print()
    print("=" * 78)
    print("BACKFILL SUMMARY")
    print("=" * 78)
    for k, v in stats.most_common():
        print(f"  {k:<18} {v}")
    if reasons:
        print("\n  skipped because:")
        for k, v in reasons.most_common():
            print(f"    {k:<16} {v}")

    if args.dry_run and sample:
        print()
        print("  sample of what would be imported:")
        for r, kind, amt, acct, merch, ts, body in sample:
            print(f"    [{r}] {kind:<8} {amt:>10} ..{acct or '----':<4} "
                  f"{str(merch)[:24]:<26} {ts} | {body}")

    if args.dry_run:
        print("\n  (dry run - nothing was written)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="glob, e.g. 'C:/Users/me/sms/*.xml'")
    ap.add_argument("--prefix", default="AS-", help="person prefix to attribute (AP-/AS-)")
    ap.add_argument("--mappings", help="JSON file: {'swiggy': {'root':'EXPENSE','category':'Food Delivery'}}")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-needs-review", action="store_true",
                    help="only import messages where a merchant was identified")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())