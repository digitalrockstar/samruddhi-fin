"""Score the parser against a labelled corpus.

Usage:
    python -m scripts.eval_parser corpus.jsonl [--show 8]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

from app.services.parser import AMT, AMT_CORE, NUM, parse_sms

_AMT_RX = re.compile(AMT, re.IGNORECASE)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def to_dt(s: str):
    for fmt in ("%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def one_line(s: str, width: int = 130) -> str:
    """Collapse newlines so multi-line SMS stay readable in one row."""
    t = " ".join(str(s).split())
    return t[:width]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--show", type=int, default=6)
    ap.add_argument(
        "--date-tolerance", type=int, default=1,
        help="days between SMS timestamp and stated txn date that still counts as correct",
    )
    args = ap.parse_args()

    rows = load(args.corpus)
    stats = Counter()
    failures = {"amount": [], "date": [], "fp": [], "merchant": [], "account": []}

    for r in rows:
        body, want_label = r["body"], r["label"]
        tm = _AMT_RX.search(body)
        truth_amt = Decimal(
            (tm.group(1) or tm.group(2)).replace(",", "")
        ) if tm else Decimal("0")
        truth_dt = to_dt(r.get("readable_date") or "")

        p = parse_sms(body, received_at=truth_dt)
        is_posted = want_label.startswith("posted")

        if is_posted:
            stats["posted_total"] += 1
            if p.amount and p.amount == truth_amt:
                stats["amount_ok"] += 1
            else:
                stats["amount_bad"] += 1
                failures["amount"].append((want_label, str(truth_amt), str(p.amount), body[:90]))

            if truth_dt:
                if p.txn_timestamp:
                    # The SMS can land after midnight relative to the stated
                    # transaction date, so allow a small tolerance.
                    delta = abs((p.txn_timestamp.date() - truth_dt.date()).days)
                    ok = delta <= args.date_tolerance
                    stats["date_ok" if ok else "date_wrongday"] += 1
                    if not ok:
                        failures["date"].append(
                            (str(truth_dt), str(p.txn_timestamp), one_line(body)))
                else:
                    stats["date_none"] += 1
                    failures["date"].append((str(truth_dt), "None", one_line(body)))

            if p.merchant:
                stats["merchant_ok"] += 1
            else:
                stats["merchant_none"] += 1
                failures["merchant"].append(body[:90])

            if p.account_last_four:
                stats["account_ok"] += 1
            else:
                stats["account_none"] += 1
                failures["account"].append(body[:90])

            if p.is_noise:
                stats["posted_flagged_noise"] += 1
        else:
            stats["notposted_total"] += 1
            # A non-posted message must not become a transaction
            if p.amount > 0 and not p.is_noise:
                stats["notposted_leak"] += 1
                failures["fp"].append((str(p.amount), body[:90]))

    def pct(a, b):
        return f"{(100.0 * a / b):.1f}%" if b else "n/a"

    pt = stats["posted_total"]
    print("=" * 78)
    print("PARSER SCORING")
    print("=" * 78)
    print(f"posted messages      : {pt}")
    print(f"  amount correct     : {stats['amount_ok']:>6}  ({pct(stats['amount_ok'], pt)})")
    print(f"  amount wrong       : {stats['amount_bad']:>6}")
    print(f"  date same day      : {stats['date_ok']:>6}  ({pct(stats['date_ok'], pt)})")
    print(f"  date wrong day     : {stats['date_wrongday']:>6}")
    print(f"  date missing       : {stats['date_none']:>6}")
    print(f"  merchant found     : {stats['merchant_ok']:>6}  ({pct(stats['merchant_ok'], pt)})")
    print(f"  merchant missing   : {stats['merchant_none']:>6}")
    print(f"  account found      : {stats['account_ok']:>6}  ({pct(stats['account_ok'], pt)})")
    print(f"  account missing    : {stats['account_none']:>6}")
    print()
    npt = stats["notposted_total"]
    print(f"non-posted messages  : {npt}")
    print(f"  LEAKED as txn      : {stats['notposted_leak']:>6}  ({pct(stats['notposted_leak'], npt)})")
    print(f"  correctly rejected : {npt - stats['notposted_leak']:>6}")

    for key in ("amount", "date", "fp", "merchant"):
        items = failures[key]
        if not items:
            continue
        print()
        print("-" * 78)
        print(f"FAILURES: {key}  (showing {args.show})")
        print("-" * 78)
        for row in items[: args.show]:
            print("  " + " | ".join(one_line(x) for x in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())