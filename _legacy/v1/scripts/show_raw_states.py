"""Show every 'was it parsed correctly?' state the raw table can hold."""
import os
from pathlib import Path

DB = Path(r"X:\Code\samruddhi-fin\states.db")
if DB.exists():
    DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["SECRET_KEY"] = "x"

from alembic import command
from alembic.config import Config

ROOT = Path(r"X:\Code\samruddhi-fin")
cfg = Config(str(ROOT / "alembic.ini"))
cfg.set_main_option("script_location", str(ROOT / "alembic"))
cfg.set_main_option("sqlalchemy.url", f"sqlite:///{DB}")
command.upgrade(cfg, "head")

from fastapi.testclient import TestClient

import app.main as main

MSGS = [
    # a normal transaction
    ("AP- INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at TORRENTPOWER", "normal txn"),
    # a credit-card payment -> transfer
    ("AP- DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 15798.00 RECEIVED TOWARDS YOUR CREDIT CARD "
     "ENDING 0030 THROUGH IMPS ON 4-7-2024.", "cc payment"),
    # parser ran, decided NOT a transaction
    ("AP- Your OTP for the transaction of Rs. 45,000 is 883421.", "otp"),
    ("AP- AutoPay for Vi bill of INR 588.82 is scheduled on 07-07-2024.", "scheduled"),
    ("AP- Avl bal in your Kotak A/c XXXX1832 as on 01-07-2024 is INR 480807.69", "balance"),
    # parsed as POSTED but still no transaction (the case that is easy to miss)
    ("AP- Hello there", "no amount"),
    # no attribution at all
    ("XX- unattributed Rs 500 debit", "no prefix"),
]

with TestClient(main.app) as c:
    for i, (text, label) in enumerate(MSGS, 1):
        c.post("/webhook/telegram", json={"message": {
            "message_id": i, "chat": {"id": -1}, "text": text}})

    rows = c.get("/api/raw", params={"page_size": 50}).json()

    print()
    print("=" * 128)
    print("WHAT EACH 'parsed OK?' QUESTION NEEDS")
    print("=" * 128)
    hdr = f"{'msg':<12} {'verdict':<11} {'reason':<10} {'txn_id':<8} {'parsed_at':<8} {'up-to-date':<11} {'note'}"
    print(hdr)
    print("-" * 128)
    for r in sorted(rows, key=lambda x: x["id"]):
        up_to_date = "yes" if r["parser_version"] == "2026-10-01.1" else "STALE"
        print(f"{MSGS[r['id']-1][1]:<12} {str(r['verdict']):<11} {str(r['reason'] or '-'):<10} "
              f"{str(r['transaction_id'] if r['transaction_id'] else '-'):<8} "
              f"{'yes' if r['parsed_at'] else 'NO':<8} {up_to_date:<11} {str(r['note'] or '')[:34]}")

    print()
    print("=" * 128)
    print("THE FOUR SEPARATE QUESTIONS")
    print("=" * 128)
    parsed_at_all = [r for r in rows if r["parsed_at"]]
    posted = [r for r in rows if r["verdict"] == "posted"]
    rejected = [r for r in rows if r["verdict"] == "not_posted"]
    with_txn = [r for r in rows if r["transaction_id"]]
    posted_no_txn = [r for r in posted if not r["transaction_id"]]
    stale = [r for r in rows if r["parser_version"] != "2026-10-01.1"]

    print(f"  1. Did the parser RUN at all?            parsed_at IS NOT NULL        -> {len(parsed_at_all)}/{len(rows)}")
    print(f"  2. Did the parser say 'this is money'?   verdict = 'posted'            -> {len(posted)}")
    print(f"     Did it say 'this is NOT money'?       verdict = 'not_posted'        -> {len(rejected)}")
    print(f"  3. Was a TRANSACTION actually created?   transaction_id IS NOT NULL   -> {len(with_txn)}")
    print(f"  4. Is the stored verdict current?        parser_version = current      -> {len(rows)-len(stale)} stale={len(stale)}")
    print()
    print(f"  >> POSTED but NO transaction created: {len(posted_no_txn)}")
    for r in posted_no_txn:
        print(f"       raw#{r['id']}  note={r['note']}")
    print("     (this is why 'verdict = posted' alone is NOT proof a transaction exists)")

    print()
    print("=" * 128)
    print("SQL FOR EACH")
    print("=" * 128)
    print("  -- never parsed:")
    print("    SELECT * FROM raw_messages WHERE parsed_at IS NULL;")
    print("  -- parser ran but decided this is not money (why is in `reason`):")
    print("    SELECT reason, count(*) FROM raw_messages")
    print("    WHERE verdict = 'not_posted' GROUP BY reason ORDER BY 2 DESC;")
    print("  -- looked like money but produced no transaction:")
    print("    SELECT * FROM raw_messages")
    print("    WHERE verdict = 'posted' AND transaction_id IS NULL;")
    print("  -- verdict is out of date with the rules:")
    print("    SELECT * FROM raw_messages WHERE parser_version <> '2026-10-01.1';")

DB.unlink(missing_ok=True)