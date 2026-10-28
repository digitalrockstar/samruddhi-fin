"""Analyse SMS Backup & Restore XML exports.

Reports which senders actually carry financial transactions and what message
shapes each sender uses, so the parser regexes can be tuned against real data.

Usage:
    python -m scripts.analyze_sms "<glob-or-dir>" [--sample N] [--out report.txt]
"""
from __future__ import annotations

import argparse
import glob
import html
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

AMOUNT = re.compile(
    r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    r"|([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:rs\.?|inr|₹)",
    re.IGNORECASE,
)

# Words that suggest money actually moved
MONEY_WORDS = re.compile(
    r"\b(debit|credited|credit|paid|sent|received|spent|charged|purchase|"
    r"withdraw|refund|cashback|emi|repay|billed|invoice|transfer|txn|"
    r"transaction|payment|balance|bal|avl)\b",
    re.IGNORECASE,
)


def iter_sms(paths):
    import xml.etree.ElementTree as ET

    for p in paths:
        for event, elem in ET.iterparse(p, events=("end",)):
            if elem.tag != "sms":
                continue
            body = elem.get("body") or ""
            yield {
                "file": Path(p).name,
                "address": elem.get("address") or "",
                "body": body,
                "readable_date": elem.get("readable_date") or "",
                "date_ms": elem.get("date"),
                "type": elem.get("type"),
                "contact_name": elem.get("contact_name") or "",
                "service_center": elem.get("service_center") or "",
            }
            elem.clear()


def has_amount(text: str) -> bool:
    return bool(AMOUNT.search(text))


def normalise(body: str) -> str:
    return " ".join(body.split())


def shape(body: str) -> str:
    """Collapse a message into a structural signature."""
    t = normalise(body)
    t = AMOUNT.sub("<AMT>", t)
    t = re.sub(r"\b\d+\b", "<N>", t)
    t = re.sub(r"\b[A-Za-z0-9]{12,}\b", "<ID>", t)
    t = re.sub(r"https?://\S+", "<URL>", t)
    t = re.sub(r"\s+", " ", t)
    return t[:150]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="glob or directory containing sms-*.xml")
    ap.add_argument("--sample", type=int, default=3, help="samples per sender")
    ap.add_argument("--min-amount-hits", type=int, default=1)
    ap.add_argument("--out", default="sms_report.txt")
    args = ap.parse_args()

    src = args.source
    if os.path.isdir(src):
        paths = sorted(glob.glob(os.path.join(src, "sms-*.xml")))
    else:
        paths = sorted(glob.glob(src))
    if not paths:
        print(f"No XML found at {src}")
        return 1

    total = 0
    by_address = defaultdict(list)
    dates = []
    for row in iter_sms(paths):
        total += 1
        by_address[row["address"]].append(row)
        if row["date_ms"]:
            try:
                dates.append(int(row["date_ms"]))
            except ValueError:
                pass

    out = []
    out.append("=" * 100)
    out.append("SMS EXPORT OVERVIEW")
    out.append("=" * 100)
    out.append(f"files      : {len(paths)}")
    for p in paths:
        out.append(f"  - {Path(p).name}")
    out.append(f"messages   : {total}")
    out.append(f"senders    : {len(by_address)}")
    if dates:
        lo = datetime.fromtimestamp(min(dates) / 1000, tz=timezone.utc)
        hi = datetime.fromtimestamp(max(dates) / 1000, tz=timezone.utc)
        out.append(f"date range : {lo:%Y-%m-%d} .. {hi:%Y-%m-%d}")

    # Which senders look financial?
    out.append("")
    out.append("=" * 100)
    out.append("SENDERS CARRYING MONEY-SHAPED MESSAGES (ranked by amount hits)")
    out.append("=" * 100)

    ranked = []
    for addr, rows in by_address.items():
        hits = [r for r in rows if has_amount(r["body"]) and MONEY_WORDS.search(r["body"])]
        if len(hits) >= args.min_amount_hits:
            ranked.append((len(hits), addr, hits))
    ranked.sort(reverse=True, key=lambda x: x[0])

    out.append(f"{'AMT MSGS':>9} {'TOTAL':>7}  {'ADDRESS':<22} {'CONTACT':<18}")
    for n, addr, hits in ranked[:60]:
        contact = Counter(r["contact_name"] for r in hits).most_common(1)[0][0]
        out.append(f"{n:>9} {len(by_address[addr]):>7}  {addr:<22} {contact[:18]:<18}")

    # Detailed shapes for the top financial senders
    out.append("")
    out.append("=" * 100)
    out.append("MESSAGE SHAPES PER FINANCIAL SENDER")
    out.append("=" * 100)

    for n, addr, hits in ranked[:40]:
        out.append("")
        out.append("#" * 100)
        out.append(f"# {addr}   ({n} money messages / {len(by_address[addr])} total)")
        out.append(f"# contact: {Counter(r['contact_name'] for r in hits).most_common(2)}")
        out.append("#" * 100)
        shapes = Counter(shape(r["body"]) for r in hits)
        for sig, cnt in shapes.most_common(12):
            out.append(f"  [{cnt:>4}x] {sig}")
        out.append(f"  --- {args.sample} raw sample(s) ---")
        for r in hits[: args.sample]:
            body = r["body"].replace("\n", " | ")
            out.append(f"   * ({r['readable_date']}) {body[:400]}")

    # Non-financial noise, so we know what to ignore
    out.append("")
    out.append("=" * 100)
    out.append("SENDERS WITH NO AMOUNT+MONEY KEYWORD (likely noise)")
    out.append("=" * 100)
    noise = []
    for addr, rows in by_address.items():
        hits = [r for r in rows if has_amount(r["body"]) and MONEY_WORDS.search(r["body"])]
        if len(hits) < args.min_amount_hits:
            noise.append((len(rows), addr, rows))
    noise.sort(reverse=True)
    out.append(f"{'TOTAL':>7}  {'ADDRESS':<22} EXAMPLE")
    for total_n, addr, rows in noise[:40]:
        sample = normalise(rows[0]["body"])[:110]
        out.append(f"{total_n:>7}  {addr:<22} {sample}")

    # Encoding sanity: replacement chars mean the export mangled the text
    mangled = sum(1 for rows in by_address.values() for r in rows if "\ufffd" in r["body"])
    out.append("")
    out.append(f"messages containing U+FFFD (mangled encoding): {mangled}")

    text = "\n".join(out)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Wrote {args.out} ({len(text)} chars)")
    print(f"messages={total} senders={len(by_address)} financial_senders={len(ranked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())