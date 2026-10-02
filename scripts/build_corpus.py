"""Build a labelled SMS corpus from SMS Backup & Restore exports.

Writes JSONL where each line has the raw body plus a *heuristic* label
(posted_debit / posted_credit / posted_transfer / not_posted) derived from
phrases that are unambiguous in real bank SMS. The label is only a starting
point - it exists so parser changes can be measured, not trusted blindly.

Usage:
    python -m scripts.build_corpus "<dir-or-glob>" --out corpus.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# --- Phrases that unambiguously mean "money did not move here" -----------
NOT_POSTED = [
    r"\bis the OTP\b", r"\bOTP is\b", r"\bOTP for\b", r"\bone time password\b",
    r"\bdo not share (?:the )?OTP\b", r"\bvalid for \d+ mins?\b",
    r"could not be approved", r"\bdeclined\b", r"wasn.t validated",
    r"Auto-?Pay \(E-mandate\) Failed", r"\bfailed as\b",
    r"is scheduled", r"will be auto ?debited", r"scheduled for AutoPay",
    r"scheduled on", r"\bwill be auto\b",
        r"Total due amt", r"Min due amt", r"Payment is due",
    r"Amt Due", r"Total Due:", r"Min Due:",
    r"\bpre-?approved\b", r"\bloan up to\b", r"eligib", r"Statement:",
    r"statement is ready", r"\bDue by:", r"\bDue Date:",
                r"insurance renewal", r"\bEMI options\b", r"convert into easy EMIs",
]

# Weak signals: real debit alerts also carry "Avl Bal", "if not you", "5GB" etc.,
# so these only mean not-posted when no posted phrase matched.
WEAK_NOT_POSTED = [
    r"\bAvl [Bb]al\b", r"available credit limit is", r"Combined Bal",
    r"cashback! Get", r"Get \d+% off", r"\buse code\b", r"offer ends", r"T&C", r"Claim now",
    r"click here to know more", r"\brecharge\b.*\bplan\b", r"data validity",
    r"\d\s?GB\b", r"\bunlimited\b", r"\bfor free\b", r"\bfree plan\b", r"Set-Top Box",
    r"\bhome loan\b.*\bapplication\b", r"APPLICATION RECEIVED", r"\bloan\b.*\b(?:offer|apply|approved)",
]

# --- Phrases that mean money actually posted ------------------------------
POSTED_DEBIT = re.compile(
    r"\bspent (?:on|from|Rs|INR|thru)\b|\byou.ve spent\b|\bAmt Sent\b|\bSent\b.*\bfrom\b.*\bto\b|\bTxn\b.*\bOn\b.*\bCard\b|"
    r"\bdebited\b|\bpayment of\b.*\bAuto-?Pay\b|\bwithdrawn\b",
    re.IGNORECASE,
)
POSTED_CREDIT = re.compile(
    r"\bcredited to your\b|\breceived .* in your\b|\bhas been credited to\b|"
    r"\bRefunded\b|\brefund\b",
    re.IGNORECASE,
)
PAYMENT_TOWARDS_CC = re.compile(
    r"payment of .*received towards your credit card|"
    r"credited to your (?:kotak )?(?:bank )?credit card|"
    r"payment of .*is credited to your",
    re.IGNORECASE,
)


def label(body: str) -> str:
    low = body.lower()
    for pat in NOT_POSTED:
        if re.search(pat, body, re.IGNORECASE):
            return "not_posted"
    has_posted = bool(POSTED_DEBIT.search(body) or POSTED_CREDIT.search(body) or PAYMENT_TOWARDS_CC.search(body))
    if not has_posted:
        for pat in WEAK_NOT_POSTED:
            if re.search(pat, body, re.IGNORECASE):
                return "not_posted"
    if PAYMENT_TOWARDS_CC.search(body):
        return "posted_transfer"
    if POSTED_DEBIT.search(body):
        return "posted_debit"
    if POSTED_CREDIT.search(body):
        return "posted_credit"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", default="corpus.jsonl")
    args = ap.parse_args()

    paths = ([os.path.join(args.source, p) for p in sorted(os.listdir(args.source))
              if p.endswith(".xml")] if os.path.isdir(args.source)
             else sorted(glob.glob(args.source)))
    if not paths:
        print(f"No XML at {args.source}")
        return 1

    counts = Counter()
    rows = []
    for p in paths:
        for _, el in ET.iterparse(p, events=("end",)):
            if el.tag != "sms":
                continue
            body = el.get("body") or ""
            if not body.strip():
                el.clear()
                continue
            lab = label(body)
            counts[lab] += 1
            if lab != "unknown":
                rows.append({
                    "address": el.get("address"),
                    "body": body,
                    "readable_date": el.get("readable_date"),
                    "label": lab,
                })
            el.clear()

    with open(args.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"wrote {len(rows)} labelled messages -> {args.out}")
    for k, v in counts.most_common():
        print(f"  {k:<18} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())