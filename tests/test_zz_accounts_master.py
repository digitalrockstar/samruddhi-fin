# Named test_zz_ so it runs last: other flow tests assert exact account counts in the shared test DB.
import base64
from decimal import Decimal

from fastapi.testclient import TestClient

import app.main as m


import pytest


@pytest.fixture
def c():
    with TestClient(m.app) as client:   # runs lifespan so reference data is seeded
        yield client


def _person(c):
    r = c.get("/api/persons").json()
    return r[0]["id"]


def test_create_investment_and_summary_totals(c):
    pid = _person(c)
    mf = c.post("/api/accounts", json={
        "person_id": pid, "bank_name": "HDFC MF", "account_type": "mutual_fund",
        "nickname": "Flexi Cap", "invested_amount": "100000", "current_value": "123456.50"})
    assert mf.status_code == 201, mf.text
    assert mf.json()["valuation_as_of"] is not None            # stamped automatically
    fd = c.post("/api/accounts", json={
        "person_id": pid, "bank_name": "Kotak", "account_type": "fixed_deposit",
        "invested_amount": "50000", "current_value": "52000"})
    assert fd.status_code == 201
    loan = c.post("/api/accounts", json={
        "person_id": pid, "bank_name": "HDFC", "account_type": "loan",
        "nickname": "Home loan", "balance": "2000000"})
    assert loan.status_code == 201
    t = c.get("/api/accounts/summary").json()["totals"]
    assert Decimal(str(t["invested"])) == Decimal("150000")
    assert Decimal(str(t["investment_value"])) == Decimal("175456.50")
    assert Decimal(str(t["investment_gain"])) == Decimal("25456.50")
    assert Decimal(str(t["liabilities"])) >= Decimal("2000000")


def test_last_six_derives_last_four_and_validates(c):
    pid = _person(c)
    ok = c.post("/api/accounts", json={"person_id": pid, "bank_name": "Axis",
                "account_type": "credit_card", "last_six": "123456", "credit_limit": "300000"})
    assert ok.status_code == 201 and ok.json()["last_four"] == "3456"
    bad = c.post("/api/accounts", json={"person_id": pid, "bank_name": "Axis",
                 "account_type": "credit_card", "last_six": "12345"})
    assert bad.status_code == 400
    dup = c.post("/api/accounts", json={"person_id": pid, "bank_name": "Axis",
                 "account_type": "debit_card", "last_six": "993456"})
    assert dup.status_code == 400                               # same last four, same holder


def test_card_outstanding_from_sms_available_limit(c):
    pid = _person(c)
    acc = c.post("/api/accounts", json={"person_id": pid, "bank_name": "YES", "account_type": "credit_card",
                 "last_four": "5003", "credit_limit": "300000"}).json()
    c.post("/webhook/telegram", json={"message": {"message_id": 777001, "date": 1760000000,
           "chat": {"id": 0}, "from": {"id": 1, "first_name": "t"},
           "text": "AP- INR 4,365.00 spent on YES BANK Card X5003 @ZOMATO LIMITED 15-08-2025 02:12:12 pm. Avl Lmt INR 292,489.39. SMS BLKCC 5003 to 9840909000 if not you"}})
    item = next(a for a in c.get("/api/accounts/summary").json()["accounts"] if a["id"] == acc["id"])
    assert item["balance_source"] == "sms", item
    assert Decimal(str(item["balance"])) == Decimal("300000") - Decimal("292489.39")


def test_reprocess_keeps_manually_edited_transactions(c):
    msg = {"message_id": 777002, "date": 1760000500, "chat": {"id": 0}, "from": {"id": 1, "first_name": "t"},
           "text": "AP- Rs.250.00 spent thru Kotak Bank Debit Card XX6951 at TESTSHOP on 19/10/2025 Avl bal 1000.00 Not you?"}
    assert c.post("/webhook/telegram", json={"message": msg}).status_code == 200
    rows = c.get("/api/transactions?search=TESTSHOP").json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert rows, "transaction should exist"
    tid = rows[0]["id"]
    assert c.patch(f"/api/transactions/{tid}", json={"merchant": "MY EDIT"}).status_code == 200
    r = c.post("/api/raw/reprocess", json={"stale_only": False, "replace": True, "dry_run": False}).json()
    assert r["kept_manual"] >= 1
    assert c.get(f"/api/transactions/{tid}").json()["merchant"] == "MY EDIT"


def test_formats_queue_ignore_learns_the_format(c):
    def hook(mid, text):
        return c.post("/webhook/telegram", json={"message": {
            "message_id": mid, "date": 1760001000 + mid, "chat": {"id": 0},
            "from": {"id": 1, "first_name": "t"}, "text": text}})

    def total():
        r = c.get("/api/transactions").json()
        return r["total"] if isinstance(r, dict) else len(r)

    before = total()
    promo = "AP- Instant Cash Alert! Rs.{} is ready to be credited to your Bank A/C within 2 minutes. Tap now"
    assert hook(777101, promo.format("50000")).status_code == 200
    assert total() == before                                        # not booked as a credit
    item = next(i for i in c.get("/api/formats/queue").json()["items"] if "instant cash alert" in i["example"].lower())
    assert c.post("/api/formats/decide", json={"skeleton": item["skeleton"], "decision": "ignore"}).status_code == 200
    assert hook(777102, promo.format("75000")).status_code == 200   # same format, new amount
    assert total() == before
    assert not [i for i in c.get("/api/formats/queue").json()["items"] if "instant cash alert" in i["example"].lower()]
    # undo puts the format back in the queue
    did = next(d["id"] for d in c.get("/api/formats/decided").json() if d["skeleton"] == item["skeleton"])
    assert c.delete(f"/api/formats/{did}").status_code == 200
    assert [i for i in c.get("/api/formats/queue").json()["items"] if "instant cash alert" in i["example"].lower()]


def test_ingest_log_explains_skips_and_channel_posts_are_ingested(c):
    c.post("/webhook/telegram", json={"update_id": 1, "my_chat_member": {"x": 1}})
    c.post("/webhook/telegram", json={"update_id": 2, "channel_post": {
        "message_id": 777301, "date": 1760002000, "chat": {"id": -100123},
        "text": "AP- Rs.99.00 spent thru Kotak Bank Debit Card XX6951 at CHANNELSHOP on 19/10/2025 Avl bal 500.00"}})
    log = c.get("/api/raw/ingest-log").json()
    assert log[0]["outcome"] == "stored" and "channel_post" in log[0]["update_keys"]
    assert log[1]["outcome"].startswith("skipped: no message") and log[1]["update_keys"] == ["my_chat_member"]
    assert "text" not in str(log)
