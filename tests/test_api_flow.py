"""Full HTTP smoke test over the real app.

The schema comes from real Alembic migrations (see conftest), so this also
proves the migration path produces a working database.
"""
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402


def j(resp, expect=200):
    assert resp.status_code == expect, (
        f"{resp.request.url} -> {resp.status_code}: {resp.text[:400]}"
    )
    return resp.json() if resp.text and resp.status_code != 204 else None


def test_migrated_schema_boots_and_seeds():
    """init_db() should now find the migrated schema and seed reference data."""
    with TestClient(main.app) as c:
        assert len(j(c.get("/api/persons"))) == 2
        assert len(j(c.get("/api/categories/tree"))) == 4


def test_full_flow():
    with TestClient(main.app) as c:
        persons = j(c.get("/api/persons"))
        assert len(persons) == 2
        assert {p["prefix"] for p in persons} == {"AP-", "AS-"}
        akshay = next(p for p in persons if p["prefix"] == "AP-")

        tree = j(c.get("/api/categories/tree"))
        assert len(tree) == 4
        assert {t["name"] for t in tree} == {"EXPENSE", "INCOME", "TRANSFER", "IGNORE"}
        shopping = next(
            ch for root in tree if root["name"] == "EXPENSE"
            for ch in root["children"] if ch["name"] == "Shopping"
        )

        # --- accounts ---
        acc = j(c.post("/api/accounts", json={
            "person_id": akshay["id"],
            "bank_name": "ICICI",
            "account_type": "credit_card",
            "last_four": "4321",
            "nickname": "Akshay ICICI",
            "default_cashback_pct": "5.00",
            "default_cashback_type": "wallet",
            "default_cashback_wallet": "Amazon Pay",
        }), 201)
        assert float(acc["default_cashback_pct"]) == 5.0

        j(c.post("/api/accounts", json={
            "person_id": akshay["id"], "bank_name": "X",
            "account_type": "debit_card", "last_four": "4321",
        }), 400)

        # --- ingest via webhook (prefix = who paid) ---
        amazon_sms = ("ICICI: Rs. 2,499.00 spent on Amazon Seller Services VISA Card XX4321 "
                      "on 05-08-2025 21:10")
        w = j(c.post("/webhook/telegram", json={
            "update_id": 1,
            "message": {"message_id": 101, "chat": {"id": 1}, "text": f"AP- {amazon_sms}"},
        }))
        assert w["ok"] and w["transaction_id"]

        again = j(c.post("/webhook/telegram", json={
            "update_id": 2,
            "message": {"message_id": 101, "chat": {"id": 1}, "text": f"AP- {amazon_sms}"},
        }))
        assert again.get("duplicate") is True

        assert j(c.post("/webhook/telegram", json={
            "message": {"message_id": 102, "chat": {"id": 1}, "text": "ZZ- hello"},
        })).get("skipped")

        # --- the transaction picked up the card and default cashback ---
        txns = j(c.get("/api/transactions"))
        assert txns["total"] == 1
        t = txns["items"][0]
        assert t["account_nickname"] == "Akshay ICICI"
        assert t["category_name"] == "Amazon"
        assert float(t["cashback_amount"]) == 124.95  # 5% of 2499
        assert t["cashback_wallet"] == "Amazon Pay"
        assert t["txn_mode"] == "card"

        kinds = sorted(a["allocation_type"] for a in t["allocations"])
        assert kinds == ["expense_for", "paid_by"]

        # --- split: Akshay / Ashka ---
        ashka = next(p for p in persons if p["prefix"] == "AS-")
        j(c.patch(f"/api/transactions/{t['id']}", json={
            "allocations": [
                {"person_id": akshay["id"], "amount": "1500.00",
                 "allocation_type": "expense_for", "notes": "his part"},
                {"person_id": ashka["id"], "amount": "999.00",
                 "allocation_type": "expense_for", "notes": "her part"},
            ]
        }))
        split = j(c.get(f"/api/transactions/{t['id']}"))
        assert split["is_split_parent"] is True
        alloc = [a for a in split["allocations"] if a["allocation_type"] == "expense_for"]
        assert {float(a["amount"]) for a in alloc} == {1500.0, 999.0}
        assert {a["person_name"] for a in alloc} == {"Akshay Patel", "Ashka Shah"}

        # --- "Home (Shared)" allocation with no person ---
        j(c.patch(f"/api/transactions/{t['id']}", json={
            "allocations": [
                {"person_id": None, "amount": "1249.50",
                 "allocation_type": "expense_for", "notes": "shared"},
                {"person_id": ashka["id"], "amount": "1249.50",
                 "allocation_type": "expense_for", "notes": "half hers"},
            ]
        }))
        home = j(c.get(f"/api/transactions/{t['id']}"))
        shared = [a for a in home["allocations"]
                  if a["allocation_type"] == "expense_for" and a["person_id"] is None]
        assert len(shared) == 1 and shared[0]["person_name"] is None

        # --- cashback status update ---
        j(c.patch(f"/api/transactions/{t['id']}",
                  json={"cashback_status": "received", "cashback_amount": "124.95"}))
        assert j(c.get(f"/api/transactions/{t['id']}"))["cashback_status"] == "received"

        learned = j(c.get("/api/mappings"))
        assert any("amazon" in m["pattern"].lower() for m in learned)

        # --- custom category inherits its parent's type ---
        custom = j(c.post("/api/categories", json={
            "name": "Pet Care", "type": "expense",
            "parent_id": shopping["parent_id"],
        }), 201)
        assert custom["type"] == "expense"
        j(c.post("/api/categories", json={
            "name": "Pet Care", "type": "expense", "parent_id": shopping["parent_id"],
        }), 400)
        exp_root = next(t2 for t2 in tree if t2["name"] == "EXPENSE")
        j(c.delete(f"/api/categories/{exp_root['id']}"), 400)

        # --- bulk categorise clears the review queue ---
        j(c.post("/webhook/telegram", json={
            "message": {"message_id": 201, "chat": {"id": 1},
                        "text": "AP- Rs 777.77 debited via UPI to Qzxwv Unknown Store on 12-08-2025"},
        }))
        queue = j(c.get("/api/transactions/uncategorized"))
        assert len(queue) == 1 and queue[0]["needs_review"] is True

        j(c.post("/api/transactions/bulk-categorize",
                 json={"transaction_ids": [queue[0]["id"]], "category_id": custom["id"]}))
        assert j(c.get("/api/transactions/uncategorized")) == []

        # --- filters ---
        assert j(c.get("/api/transactions", params={"txn_type": "debit"}))["total"] == 2
        assert j(c.get("/api/transactions", params={"txn_type": "credit"}))["total"] == 0
        assert j(c.get("/api/transactions", params={"person_id": akshay["id"]}))["total"] == 2
        assert j(c.get("/api/transactions", params={"person_id": ashka["id"]}))["total"] == 0
        assert j(c.get("/api/transactions", params={"merchant": "amazon"}))["total"] == 1
        assert j(c.get("/api/transactions", params={"min_amount": 2000}))["total"] == 1

        # --- OTPs are rejected outright, never stored ---
        otp = j(c.post("/webhook/telegram", json={
            "message": {"message_id": 301, "chat": {"id": 1},
                        "text": "AP- Your OTP for Rs 45000 is 883421. Do not share."},
        }))
        assert otp.get("skipped") and "otp" in otp["skipped"]
        assert j(c.get("/api/transactions"))["total"] == 2
        assert j(c.get("/api/transactions", params={"is_spam": True}))["total"] == 0

        # --- CSV export ---
        csv_resp = c.get("/api/transactions/export/csv")
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        body = csv_resp.text
        assert "Paid By" in body.splitlines()[0]
        assert "Amazon Seller Services" in body

        # --- alternate forwarding formats arrive over HTTP too ---
        base_before = j(c.get("/api/transactions"))["total"]

        # "AS: New SMS: ..." -> Ashka
        j(c.post("/webhook/telegram", json={
            "message": {"message_id": 401, "chat": {"id": 1},
                        "text": "AS: New SMS: Sent Rs.89.00 from Kotak Bank AC X0359 to "
                                "credpay.zepto@axisb on 27-07-24.UPI Ref 420088733192"},
        }))
        # "From:/Time: ..." -> Akshay, and the header supplies the clock time
        j(c.post("/webhook/telegram", json={
            "message": {"message_id": 402, "chat": {"id": 1},
                        "text": "From: +919812345678\nTime: 14:32\n"
                                "INR 588.82 spent on Kotak Bank Card x4365 on 12-07-24 "
                                "at ZEPTONOW"},
        }))

        after = j(c.get("/api/transactions"))["items"]
        assert len(after) == base_before + 2

        ashka_row = next(t for t in after if t["message_id"] == 401)
        assert ashka_row["person_name"] == "Ashka Shah"
        assert "New SMS" not in ashka_row["raw_text"]

        akshay_row = next(t for t in after if t["message_id"] == 402)
        assert akshay_row["person_name"] == "Akshay Patel"
        assert akshay_row["txn_timestamp"][11:16] == "14:32"
        assert "+919812345678" not in akshay_row["raw_text"]

        # no attribution at all -> rejected
        rejected = j(c.post("/webhook/telegram", json={
            "message": {"message_id": 403, "chat": {"id": 1},
                        "text": "New SMS: Rs.500 debited via UPI"},
        }))
        assert "no recognised prefix" in rejected["skipped"]

        # --- pages render ---
        for path in ["/", "/transactions", "/uncategorized", "/settings"]:
            assert c.get(path).status_code == 200, path

        # --- dashboard endpoints respond ---
        assert j(c.get("/api/dashboard/summary"))["period"] == "month"
        assert "avg_per_day" in j(c.get("/api/dashboard/averages"))
        assert "total_expected" in j(c.get("/api/dashboard/cashback"))["totals"]
        assert "recurring" in j(c.get("/api/dashboard/recurring"))