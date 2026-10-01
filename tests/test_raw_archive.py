"""Raw message archive and re-derivation.

The archive is what makes the parser improvable after the fact: change a rule,
re-derive every stored message, get corrected transactions - without anyone
re-forwarding a single SMS.

Shares the migrated database from conftest, and asserts on deltas rather than
absolute counts so it stays order-independent.
"""
from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

import app.main as main  # noqa: E402


@pytest.fixture(scope="module")
def c():
    """One client (and therefore one app lifespan) shared by this module."""
    with TestClient(main.app) as client:
        yield client


def j(resp, expect=200):
    assert resp.status_code == expect, f"{resp.request.url} -> {resp.status_code}: {resp.text[:300]}"
    return resp.json() if resp.text and resp.status_code != 204 else None


def send(c, text, message_id):
    return j(c.post("/webhook/telegram", json={
        "message": {
            "message_id": message_id, "chat": {"id": -100123},
            "from": {"id": 42, "first_name": "Akshay"},
            "date": 1720000000, "text": text,
        }
    }))


def test_archive_stores_everything_including_rejections(c):
    """Transactions are a derived view; the archive is the source of truth."""
    base_raw = j(c.get("/api/raw/stats"))["total_raw"]

    send(c, "AP- INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at TORRENTPOWER", 1)
    send(c, "AP- Your OTP for the transaction of Rs. 45,000 is 883421.", 2)
    send(c, "AP- AutoPay for Vi bill of INR 588.82 is scheduled on 07-07-2024.", 3)
    send(c, "XX- no attribution at all", 4)

    mine = [r for r in j(c.get("/api/raw", params={"page_size": 500}))
            if r["external_id"] in {"1", "2", "3", "4"}]
    assert len(mine) == 4

    by_id = {r["external_id"]: r for r in mine}

    # a real transaction
    card = by_id["1"]
    assert card["verdict"] == "posted"
    assert card["transaction_id"] is not None
    assert card["account_last_four"] == "4365"
    assert float(card["amount"]) == 370.0
    assert card["rule"], "every parsed row records which rule handled it"

    # rejected, but still archived with a reason
    otp = by_id["2"]
    assert otp["verdict"] == "not_posted"
    assert otp["reason"] == "otp"
    assert otp["transaction_id"] is None
    assert "45,000" in otp["body"]

    scheduled = by_id["3"]
    assert scheduled["verdict"] == "not_posted"
    assert scheduled["reason"] == "scheduled"

    unattributed = by_id["4"]
    assert unattributed["prefix_detected"] is None
    assert unattributed["transaction_id"] is None

    stats = j(c.get("/api/raw/stats"))
    assert stats["total_raw"] == base_raw + 4


def test_raw_keeps_sender_metadata(c):
    send(c, "AP- Rs. 250.00 debited via UPI to SWIGGY@paytm on 04-07-24", 10)
    row = next(r for r in j(c.get("/api/raw", params={"page_size": 500}))
               if r["external_id"] == "10")
    assert row["source"] == "telegram"
    assert row["external_id"] == "10"
    assert row["prefix_detected"] == "AP-"
    assert row["chat_id"] == "-100123"
    assert row["sender_name"] == "Akshay"
    assert row["parser_version"]


def test_duplicate_delivery_is_a_noop(c):
    before = j(c.get("/api/transactions"))["total"]
    before_raw = j(c.get("/api/raw/stats"))["total_raw"]
    again = send(c, "AP- INR 370 spent on Kotak Bank Card x4365 on 01-JUL-2024 at TORRENTPOWER", 1)
    assert again.get("duplicate") is True
    assert j(c.get("/api/transactions"))["total"] == before
    assert j(c.get("/api/raw/stats"))["total_raw"] == before_raw


def test_reprocess_is_read_only_when_dry_run(c):
    """dry_run must not mutate anything - not even a rollback that a nested
    commit would have already defeated."""
    before_txn = j(c.get("/api/transactions"))["total"]
    before_raw = j(c.get("/api/raw/stats"))

    out = j(c.post("/api/raw/reprocess", json={
        "stale_only": False, "dry_run": True}))

    assert out["dry_run"] is True
    assert out["examined"] >= 1
    assert j(c.get("/api/transactions"))["total"] == before_txn
    assert j(c.get("/api/raw/stats")) == before_raw


def test_reprocess_rebuilds_without_duplicating(c):
    before_txn = j(c.get("/api/transactions"))["total"]
    before_raw = j(c.get("/api/raw/stats"))["total_raw"]

    out = j(c.post("/api/raw/reprocess", json={"stale_only": False}))

    after_txn = j(c.get("/api/transactions"))["total"]
    after_raw = j(c.get("/api/raw/stats"))["total_raw"]

    assert out["rebuilt"] >= 1
    assert after_txn == before_txn, "re-derivation must replace, not duplicate"
    assert after_raw == before_raw, "the archive is never rewritten by reprocess"


def test_reprocess_marks_rows_current(c):
    """After reprocessing, nothing should still look stale."""
    j(c.post("/api/raw/reprocess", json={"stale_only": False}))
    stats = j(c.get("/api/raw/stats"))
    assert stats["parsed_by_older_version"] == 0


def test_manual_ingest_bypasses_telegram(c):
    """For the SMS you forget to forward."""
    before = j(c.get("/api/transactions"))["total"]
    row = j(c.post("/api/raw", json={
        "text": "INR 512.00 spent on HDFC Bank Card x2060 at SWIGGY on 12-07-24",
        "prefix": "AS-",
    }), 201)
    assert row["prefix_detected"] == "AS-"
    assert row["transaction_id"] is not None
    assert j(c.get("/api/transactions"))["total"] == before + 1

    txn = j(c.get(f"/api/transactions/{row['transaction_id']}"))
    assert txn["person_name"] == "Ashka Shah"
    assert float(txn["parsed_amount"]) == 512.00


def test_stats_shape(c):
    stats = j(c.get("/api/raw/stats"))
    for key in ("parser_version", "total_raw", "by_verdict",
                "by_rejection_reason", "parsed_by_older_version",
                "stored_without_transaction", "total_transactions"):
        assert key in stats