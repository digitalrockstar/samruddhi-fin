"""Auto-generated items (subscriptions, cashback, reminders), budgets, dashboard, resets, CSV."""
from datetime import date, timedelta

import pytest

from samruddhi import ledger
from samruddhi.ledger import LedgerError, balances
from samruddhi.models import (Account, Budget, CashbackRule, Goal, GoalEntry, GoalRule, Reminder, Transaction,
                              TxnBeneficiary, User, db)

from .conftest import P, cat, mk_account, tx


def bal(hh, acc, as_of=None):
    return balances(hh.id, as_of)[acc.id]


# ---------------------------------------------------------------- subscriptions
def test_month_end_clamping_and_leap_year():
    assert ledger.next_occurrence(date(2026, 1, 31), "monthly", 31) == date(2026, 2, 28)
    assert ledger.next_occurrence(date(2026, 2, 28), "monthly", 31) == date(2026, 3, 31)
    assert ledger.next_occurrence(date(2024, 2, 29), "yearly", 29) == date(2025, 2, 28)
    assert ledger.next_occurrence(date(2026, 3, 2), "weekly", 2) == date(2026, 3, 9)


def test_subscription_creates_pending_children_only_after_approval_count(hh):
    bank = mk_account(hh, "HDFC", "bank", start=5000)
    start = date.today() - timedelta(days=65)
    t = tx(hh, "expense", 199, bank, cat(hh, "Subscriptions"), on=start, recurrence="monthly", note="Netflix")
    made = ledger.run_due(hh.id)
    assert made == 2                                           # two monthly occurrences have come due
    kids = Transaction.query.filter_by(template_id=t.id).all()
    assert len(kids) == 2 and all(k.status == "pending" for k in kids)
    assert bal(hh, bank) == P(5000 - 199)                      # only the original is counted
    assert ledger.run_due(hh.id) == 0                          # idempotent
    ledger.approve_transaction(kids[0], hh.admin.id)
    assert bal(hh, bank) == P(5000 - 199 * 2)


def test_pausing_subscription_stops_generation(hh):
    bank = mk_account(hh, "HDFC", "bank", start=5000)
    t = tx(hh, "expense", 10, bank, cat(hh, "Subscriptions"), on=date.today() - timedelta(days=30), recurrence="weekly")
    ledger.set_subscription_active(t, False)
    assert ledger.run_due(hh.id) == 0
    ledger.set_subscription_active(t, True)
    assert t.next_run > date.today()


def test_goal_rule_creates_pending_entries_that_need_approval(hh):
    bank = mk_account(hh, "HDFC", "bank", start=5000)
    goal = Goal(household_id=hh.id, name="Car", target=P(10000))
    db.session.add(goal)
    db.session.commit()
    ledger.add_goal_rule(hh.id, goal.id, bank.id, P(500), "monthly", date.today() - timedelta(days=1))
    assert ledger.run_due(hh.id) == 1
    assert ledger.goal_summary(goal)["saved"] == 0
    e = GoalEntry.query.one()
    e.status = "posted"
    db.session.commit()
    assert ledger.goal_summary(goal)["saved"] == P(500)
    assert bal(hh, bank) == P(5000)                            # still zero impact on the account


# ---------------------------------------------------------------- cashback
def test_percent_cashback_rule_after_delay_to_wallet(hh):
    card = mk_account(hh, "ICICI Amazon", "credit_card", credit_limit=100000)
    wallet = mk_account(hh, "Amazon Wallet", "wallet")
    rule = CashbackRule(household_id=hh.id, name="Amazon 5%", account_id=card.id, category_id=cat(hh, "Amazon").id,
                        mode="percent", basis_points=500, delay_days=30, payout_account_id=wallet.id)
    db.session.add(rule)
    db.session.commit()
    tx(hh, "expense", 2000, card, cat(hh, "Amazon"))
    cb = Transaction.query.filter_by(source="cashback").one()
    assert (cb.amount, cb.status, cb.account_id) == (P(100), "pending", wallet.id)
    assert cb.date == date.today() + timedelta(days=30)
    assert bal(hh, wallet) == 0                                # pending: not counted
    ledger.approve_transaction(cb, hh.admin.id)
    assert bal(hh, wallet, date.today() + timedelta(days=30)) == P(100)
    tx(hh, "expense", 500, card, cat(hh, "Uber"))              # different category: no cashback
    assert Transaction.query.filter_by(source="cashback").count() == 1


def test_one_time_fixed_cashback_with_min_spend_same_account(hh):
    wallet = mk_account(hh, "Zepto Cash", "wallet", start=5000)
    rule = CashbackRule(household_id=hh.id, name="Zepto ₹50", account_id=wallet.id, keyword="zepto", mode="fixed",
                        fixed_amount=P(50), min_spend=P(800), one_time=True)
    db.session.add(rule)
    db.session.commit()
    tx(hh, "expense", 700, wallet, cat(hh, "Zepto"), note="zepto order")           # below min spend
    assert Transaction.query.filter_by(source="cashback").count() == 0
    tx(hh, "expense", 800, wallet, cat(hh, "Zepto"), note="Zepto order")
    cb = Transaction.query.filter_by(source="cashback").one()
    assert cb.amount == P(50) and cb.account_id == wallet.id and cb.date == date.today()
    tx(hh, "expense", 900, wallet, cat(hh, "Zepto"), note="zepto again")           # one-time: used up
    assert Transaction.query.filter_by(source="cashback").count() == 1


def test_adhoc_cashback_on_a_transaction(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    fc = mk_account(hh, "FC Wallet", "wallet")
    tx(hh, "expense", 500, bank, cat(hh, "Clothing"), cashback=dict(mode="percent", value=800, days=7, account_id=fc.id))
    cb = Transaction.query.filter_by(source="cashback").one()
    assert cb.amount == P(40) and cb.account_id == fc.id and cb.status == "pending"


def test_cashback_cap_and_deleting_source_removes_pending_cashback(hh):
    card = mk_account(hh, "Card", "credit_card", credit_limit=100000)
    db.session.add(CashbackRule(household_id=hh.id, name="cap", account_id=card.id, mode="percent",
                                basis_points=1000, max_cashback=P(25)))
    db.session.commit()
    src = tx(hh, "expense", 1000, card, cat(hh, "Amazon"))
    assert Transaction.query.filter_by(source="cashback").one().amount == P(25)
    ledger.delete_transaction(src)
    assert Transaction.query.filter_by(source="cashback").count() == 0


# ---------------------------------------------------------------- reminders
def test_card_reminder_approval_makes_transfer(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000)
    due = (date.today() + timedelta(days=3))
    card = mk_account(hh, "Axis", "credit_card", start=-2500, credit_limit=50000, due_day=due.day)
    assert ledger.ensure_reminders(hh.id) == 1
    assert ledger.ensure_reminders(hh.id) == 0                                 # unique per due date
    r = Reminder.query.one()
    assert r.amount == P(2500)
    assert bal(hh, bank) == P(10000)                                           # nothing moved yet
    ledger.approve_reminder(r, hh.admin.id, bank.id)
    assert bal(hh, bank) == P(7500) and bal(hh, card) == 0 and r.status == "done"
    with pytest.raises(LedgerError):
        ledger.approve_reminder(r, hh.admin.id, bank.id)                       # cannot be approved twice


def test_no_reminder_when_nothing_owed_or_not_near(hh):
    mk_account(hh, "Axis", "credit_card", credit_limit=1000, due_day=(date.today() + timedelta(days=2)).day)
    mk_account(hh, "Far", "credit_card", start=-100, credit_limit=1000, due_day=(date.today() + timedelta(days=15)).day)
    assert ledger.ensure_reminders(hh.id) == 0


def test_loan_emi_splits_principal_and_interest(hh):
    bank = mk_account(hh, "HDFC", "bank", start=20000)
    loan = mk_account(hh, "Car loan", "loan", start=-100000, loan_total=150000, emi_principal=8000, emi_interest=1500,
                      due_day=(date.today() + timedelta(days=2)).day)
    ledger.ensure_reminders(hh.id)
    r = Reminder.query.one()
    assert r.amount == P(9500)
    ledger.approve_reminder(r, hh.admin.id, bank.id)
    assert bal(hh, loan) == -P(92000)                                          # only principal reduces the loan
    assert bal(hh, bank) == P(20000 - 9500)
    interest = Transaction.query.filter_by(type="expense", source="reminder").one()
    assert interest.amount == P(1500)                                          # interest is a real expense
    assert ledger.account_groups(hh.id)["Car loan"][0]["paid"] == P(58000)


def test_loan_reminder_blocked_when_payer_cannot_afford(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100)
    mk_account(hh, "Loan", "loan", start=-5000, emi_principal=1000, emi_interest=100,
               due_day=(date.today() + timedelta(days=1)).day)
    ledger.ensure_reminders(hh.id)
    r = Reminder.query.one()
    with pytest.raises(LedgerError, match="Insufficient"):
        ledger.approve_reminder(r, hh.admin.id, bank.id)
    assert Transaction.query.count() == 0 and r.status == "pending"


# ---------------------------------------------------------------- budgets
def test_individual_vs_family_budget_tracking(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100000)
    shop = cat(hh, "Amazon").parent
    fam = Budget(household_id=hh.id, owner_id=None, category_id=shop.id, period="monthly", amount=P(5000))
    asha = Budget(household_id=hh.id, owner_id=hh.admin.id, category_id=shop.id, period="monthly", amount=P(2000))
    ravi = Budget(household_id=hh.id, owner_id=hh.ravi.id, category_id=shop.id, period="monthly", amount=P(2000))
    db.session.add_all([fam, asha, ravi])
    db.session.commit()
    tx(hh, "expense", 1000, bank, cat(hh, "Amazon"))                                          # family
    tx(hh, "expense", 600, bank, cat(hh, "Clothing"), scope="individuals", beneficiary_ids=[hh.admin.id])
    tx(hh, "expense", 301, bank, cat(hh, "Clothing"), scope="individuals",
       beneficiary_ids=[hh.admin.id, hh.ravi.id])                                              # split between two
    s = {b.owner_id: ledger.budget_status(b) for b in (fam, asha, ravi)}
    assert s[None]["spent"] == P(1000)
    assert s[hh.admin.id]["spent"] == P(600) + 15050           # 301.00 split two ways = 150.50 each
    assert s[hh.ravi.id]["spent"] == 15050
    odd = tx(hh, "expense", 10.01, bank, cat(hh, "Clothing"), scope="individuals",
             beneficiary_ids=[hh.admin.id, hh.ravi.id])
    assert sorted(a for _, a in ledger.shares(odd)) == [500, 501]                              # odd paisa goes to one person
    assert sum(a for _, a in ledger.shares(odd)) == odd.amount                                 # splits never lose a paisa


def test_budget_state_thresholds_and_periods(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100000)
    b = Budget(household_id=hh.id, category_id=cat(hh, "Uber").parent_id, period="weekly", amount=P(1000))
    db.session.add(b)
    db.session.commit()
    tx(hh, "expense", 850, bank, cat(hh, "Uber"))
    assert ledger.budget_status(b)["state"] == "warn"
    tx(hh, "expense", 200, bank, cat(hh, "Ola"))
    assert ledger.budget_status(b)["state"] == "over"
    start, end = ledger.period_range("weekly", date(2026, 10, 29))
    assert (start, end) == (date(2026, 10, 26), date(2026, 11, 1))
    assert ledger.period_range("yearly", date(2026, 10, 29)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert ledger.period_range("monthly", date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))


# ---------------------------------------------------------------- dashboard + activity + csv
def test_dashboard_numbers(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000, institution="HDFC")
    card = mk_account(hh, "Axis", "credit_card", credit_limit=10000)
    addon = mk_account(hh, "Axis add-on", "credit_card", parent_id=card.id)
    tx(hh, "income", 50000, bank, cat(hh, "Salary"))
    tx(hh, "expense", 1000, card, cat(hh, "Zepto"))
    tx(hh, "expense", 500, addon, cat(hh, "Uber"), scope="individuals", beneficiary_ids=[hh.ravi.id])
    tx(hh, "expense", 300, bank, cat(hh, "Petrol"))
    tx(hh, "transfer", 2000, bank, cat(hh, "Investment"), to=mk_account(hh, "Zerodha", "investment"))
    tx(hh, "expense", 99, bank, cat(hh, "Uber"), status="pending", source="subscription")
    s = date.today().replace(day=1)
    d = ledger.dashboard(hh.id, s, date.today())
    assert (d["income"], d["expense"], d["net"]) == (P(50000), P(1800), P(48200))      # transfers + pending excluded
    assert d["total_balance"] == P(10000 + 50000 - 300 - 2000)
    assert d["cards"] == 2 and d["addon_cards"] == 1
    assert d["card_owed"] == P(1500) and d["card_limit"] == P(10000) and d["utilisation"] == 15.0
    assert d["pending"] == 1
    assert {p["name"]: p["total"] for p in d["by_person"]} == {"Family": P(1300), "Ravi": P(500)}
    assert d["by_category"][0]["name"] == "Grocery"
    only_ravi = ledger.dashboard(hh.id, s, date.today(), person=str(hh.ravi.id))
    assert only_ravi["expense"] == P(500)
    cards_only = ledger.dashboard(hh.id, s, date.today(), kind="credit_card")
    assert cards_only["expense"] == P(1500)
    assert d["net_worth"] == d["total_balance"] + d["invested_value"] - d["card_owed"] - d["loans"]


def test_activity_filters_summary_and_csv(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000)
    tx(hh, "income", 1000, bank, cat(hh, "Salary"), note="Oct pay")
    tx(hh, "expense", 120, bank, cat(hh, "Uber"), note="airport 100% legit_trip")
    tx(hh, "expense", 80, bank, cat(hh, "Ola"), on=date.today() - timedelta(days=60))
    f = dict(start=date.today() - timedelta(days=30), end=date.today())
    q = ledger.query_transactions(hh.id, f)
    assert q.count() == 2
    assert ledger.query_transactions(hh.id, dict(f, q="100%")).count() == 1            # % is literal, not a wildcard
    assert ledger.query_transactions(hh.id, dict(f, q="legit_trip")).count() == 1
    assert ledger.query_transactions(hh.id, dict(f, min_amt=P(500))).count() == 1
    assert ledger.query_transactions(hh.id, dict(f, type="expense")).count() == 1
    assert ledger.query_transactions(hh.id, dict(f, category_id=cat(hh, "Uber").parent_id)).count() == 1
    assert ledger.query_transactions(hh.id, dict(f, kind="credit_card")).count() == 0
    s = ledger.activity_summary(q)
    assert (s["income"], s["expense"], s["net"], s["count"]) == (P(1000), P(120), P(880), 2)
    rows = ledger.transactions_csv(q.order_by(Transaction.id)).strip().splitlines()
    assert rows[0].startswith("id,date,type,status") and len(rows) == 3
    assert "1000.00" in rows[1]


# ---------------------------------------------------------------- resets
def _populate(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000)
    goal = Goal(household_id=hh.id, name="Car", target=P(1000))
    db.session.add(goal)
    db.session.commit()
    old = tx(hh, "expense", 100, bank, cat(hh, "Uber"), on=date.today() - timedelta(days=40), recurrence="monthly")
    tx(hh, "income", 500, bank, cat(hh, "Salary"), on=date.today() - timedelta(days=5), goal_id=goal.id)
    tx(hh, "expense", 50, bank, cat(hh, "Uber"), scope="individuals", beneficiary_ids=[hh.ravi.id])
    ledger.run_due(hh.id)
    return bank, goal, old


def test_reset_transactions_keeps_masters(hh):
    bank, goal, _ = _populate(hh)
    ledger.reset_transactions(hh.id)
    assert Transaction.query.count() == 0 and GoalEntry.query.count() == 0 and TxnBeneficiary.query.count() == 0
    assert Account.query.count() == 1 and Goal.query.count() == 1 and User.query.count() == 3
    assert bal(hh, bank) == 0                              # amounts zeroed with starting balance


def test_reset_transactions_can_keep_starting_balance(hh):
    bank, _, _ = _populate(hh)
    ledger.reset_transactions(hh.id, zero_starting_balances=False)
    assert bal(hh, bank) == P(10000)


def test_reset_after_date_only_removes_later_rows_and_does_not_backfill(hh):
    bank, goal, old = _populate(hh)
    cutoff = date.today() - timedelta(days=10)
    ledger.reset_after(hh.id, cutoff)
    remaining = Transaction.query.all()
    assert all(t.date < cutoff for t in remaining) and len(remaining) == 1
    assert GoalEntry.query.count() == 0
    assert bal(hh, bank) == P(10000 - 100)                 # starting balance + the one older txn
    assert ledger.run_due(hh.id) == 0                      # the wiped subscription months are not regenerated


def test_full_reset_removes_household(hh):
    _populate(hh)
    ledger.reset_full(hh.id)
    from samruddhi.models import Category, Household
    assert Household.query.count() == 0 and User.query.count() == 0 and Account.query.count() == 0
    assert Category.query.count() == 0 and Transaction.query.count() == 0


def test_backup_csvs_cover_every_table_without_password_hashes(hh):
    _populate(hh)
    files = ledger.backup_csvs(hh.id)
    assert {"users.csv", "accounts.csv", "transactions.csv", "goals.csv", "categories.csv", "budgets.csv"} <= set(files)
    assert "password_hash" not in files["users.csv"] and "pbkdf2" not in files["users.csv"]
    assert files["transactions.csv"].count("\n") >= 4


# ---------------------------------------------------------------- goal shortfall + custom shares + roles
def test_withdrawal_below_earmark_is_allowed_and_reduces_goal_with_negative_entry(hh):
    from samruddhi.permissions import can
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    goal = Goal(household_id=hh.id, name="Car", target=P(5000))
    db.session.add(goal)
    db.session.commit()
    ledger.add_goal_entry(hh.id, hh.admin.id, goal.id, bank.id, P(800))
    tx(hh, "expense", 500, bank, cat(hh, "Rent"))                 # leaves 500, but 800 is earmarked: flagged, not blocked
    assert bal(hh, bank) == P(500)
    assert ledger.goal_summary(goal)["saved"] == P(500)
    neg = GoalEntry.query.filter(GoalEntry.amount < 0).one()
    assert neg.amount == -P(300) and neg.source == "auto" and "withdrawn" in neg.note
    assert db.session.info.get("warnings")
    tx(hh, "expense", 100, bank, cat(hh, "Rent"))                 # earmark is now fully covered again? 500-100=400 < 500
    assert ledger.goal_summary(goal)["saved"] == P(400)
    assert ledger.earmarked(hh.id)[bank.id] <= bal(hh, bank)      # earmarks never exceed what the account holds
    assert can(hh.admin, "data.reset") and not can(hh.ravi, "accounts.manage")


def test_custom_split_must_add_up_and_drives_budgets(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000)
    ids = [hh.admin.id, hh.ravi.id]
    with pytest.raises(LedgerError, match="add up"):
        tx(hh, "expense", 100, bank, cat(hh, "Clothing"), scope="individuals", beneficiary_ids=ids,
           beneficiary_shares={hh.admin.id: P(60), hh.ravi.id: P(30)})
    t = tx(hh, "expense", 100, bank, cat(hh, "Clothing"), scope="individuals", beneficiary_ids=ids,
           beneficiary_shares={hh.admin.id: P(70), hh.ravi.id: P(30)})
    assert dict(ledger.shares(t)) == {hh.admin.id: P(70), hh.ravi.id: P(30)}
    b = Budget(household_id=hh.id, owner_id=hh.ravi.id, category_id=cat(hh, "Clothing").parent_id, period="monthly", amount=P(500))
    db.session.add(b)
    db.session.commit()
    assert ledger.budget_status(b)["spent"] == P(30)
