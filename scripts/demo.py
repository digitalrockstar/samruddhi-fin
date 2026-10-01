"""Manual end-to-end demo: ingest sample SMS, then print the resulting data.

Usage:
    python -m scripts.demo
    python -m scripts.demo --db sqlite+aiosqlite:///./demo.db --keep
"""
import argparse
import os
from pathlib import Path

SAMPLES = [
    "AP- ICICI: Rs. 2,499.00 spent on Amazon Seller Services VISA Card XX4321 on 05-08-2025 21:10",
    "AS- HDFC Bank: Rs. 1,250.50 debited from A/C XXXXXX8899 via UPI to SWIGGY on 12-08-2025 14:32, "
    "UPI Ref No 523418972651. Avl bal Rs. 45,230.75",
    "AP- Rs. 85,000.00 credited to your SBI A/C XXXXXX2211 on 01-09-2025 towards salary",
    "AS- Rs 500 withdrawn from SBI ATM XXXXXX2231 on 11-08-2025 22:40",
    "AP- Your OTP for the transaction of Rs. 45,000 is 883421. Do not share with anyone.",
    "AS- Rs 640 debited via UPI to Qzxwv Unknown Store on 12-08-2025 12:00",
    "AP- Rs 649 paid to Netflix on 02-08-2025 08:00",
    "AP- Rs 32,000 debited towards rent on 01-08-2025 09:00",
    "AP- Rs 1,299 debited to Uber India on 06-08-2025 19:22, UPI Ref 411122233344",
    "AS- Rs 3,200 paid at Reliance Fresh on 07-08-2025 11:00",
    "XX- this should be ignored (no prefix)",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite+aiosqlite:///:memory:")
    ap.add_argument("--keep", action="store_true", help="write to a file db")
    args = ap.parse_args()

    if args.keep and args.db.endswith(":memory:"):
        args.db = "sqlite+aiosqlite:///./demo.db"

    os.environ["DATABASE_URL"] = args.db
    os.environ.setdefault("SECRET_KEY", "demo")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")

    # Build the schema with the real migrations (the app never uses create_all).
    # An in-memory SQLite DB only exists for the life of one connection, so
    # fall back to a temp file when no :memory: is usable.
    sync_url = args.db.replace("+aiosqlite", "")
    if ":memory:" in sync_url:
        from pathlib import Path
        import tempfile

        tmp = Path(tempfile.gettempdir()) / "samruddhi_demo.db"
        if tmp.exists():
            tmp.unlink()
        sync_url = f"sqlite:///{tmp}"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}"

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")

    from fastapi.testclient import TestClient
    import app.main as main_app

    with TestClient(main_app.app) as c:
        print("=" * 78)
        print("INGEST")
        print("=" * 78)
        for i, text in enumerate(SAMPLES, 1):
            res = c.post("/webhook/telegram", json={
                "message": {"message_id": i, "chat": {"id": 1}, "text": text}
            }).json()
            status = res.get("note") or res.get("skipped") or res.get("duplicate")
            print(f"  {i:2}. {status:<18} {text[:56]}")

        print()
        print("=" * 78)
        print("TRANSACTIONS")
        print("=" * 78)
        rows = c.get("/api/transactions", params={"page_size": 50}).json()["items"]
        print(f"  {'PAID BY':<14} {'CATEGORY':<18} {'AMOUNT':>11}  {'MODE':<9} FLAGS")
        for t in rows:
            flags = []
            if t["needs_review"]:
                flags.append("review")
            if t["is_spam"]:
                flags.append("spam")
            if float(t["cashback_amount"]) > 0:
                flags.append(f"cb={t['cashback_amount']}")
            print(f"  {t['person_name']:<14} {str(t['category_name']):<18} "
                  f"{float(t['parsed_amount']):>11,.2f}  {str(t['txn_mode']):<9} "
                  f"{' '.join(flags)}")

        print()
        print("=" * 78)
        print("DASHBOARD (all time)")
        print("=" * 78)
        d = c.get("/api/dashboard/summary", params={"period": "all"}).json()
        print(f"  expense      : {float(d['total_expense']):>12,.2f}")
        print(f"  income       : {float(d['total_income']):>12,.2f}")
        print(f"  net          : {float(d['net']):>12,.2f}")
        print(f"  transactions : {d['transaction_count']:>12}")

        print("\n  by category:")
        for x in d["by_category"]:
            print(f"    {x['parent_category'] or '-':<16} {x['category_name']:<18} "
                  f"{x['total_amount']:>10,.2f}  {x['percentage']:>5.1f}%")

        print("\n  by mode:")
        for x in d["by_mode"]:
            print(f"    {x['mode']:<12} {x['total_amount']:>12,.2f}  {x['percentage']:>5.1f}%")

        print("\n  by person (expense_for / income_from):")
        for x in d["by_person"]:
            print(f"    {x['name']:<16} {x['expense_for']:>11,.2f} / {x['income_from']:>11,.2f}"
                  f"   net {x['net']:>11,.2f}")

        queue = c.get("/api/transactions/uncategorized").json()
        print(f"\n  review queue: {len(queue)} item(s)")
        for q in queue:
            print(f"    - {q['raw_text'][:60]}")

    if args.keep:
        print(f"\nDatabase written to {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())