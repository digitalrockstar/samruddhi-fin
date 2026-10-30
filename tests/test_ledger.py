"""Rules: zero-sum balances, pending exclusion, limits, linkage, goals."""
from datetime import date, timedelta

import pytest

from samruddhi import ledger
from samruddhi.ledger import LedgerError, balances
from samruddhi.models import Goal, GoalEntry, db

from .conftest import P, cat, mk_account, tx


def bal(hh, acc):
    return balances(hh.id)[acc.id]


def test_starting_balance_and_zero_sum(hh):
    bank = mk_account(hh, "HDFC", "bank", start=10000)
    wallet = mk_account(hh, "Amazon Wallet", "wallet", start=0)
    tx(hh, "income", 5000, bank, cat(hh, "Salary"))
    tx(hh, "expense", 1200, bank, cat(hh, "Zepto"))
    tx(hh, "transfer", 700, bank, cat(hh, "Wallet top-up"), to=wallet)
    assert bal(hh, bank) == P(10000 + 5000 - 1200 - 700)
    assert bal(hh, wallet) == P(700)
    # transfers are zero-sum across accounts
    total = bal(hh, bank) + bal(hh, wallet)
    assert total == P(10000 + 5000 - 1200)


def test_pending_and_rejected_never_count(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    t = tx(hh, "expense", 300, bank, cat(hh, "Uber"), status="pending", source="subscription")
    assert bal(hh, bank) == P(1000)
    ledger.approve_transaction(t, hh.admin.id)
    assert bal(hh, bank) == P(700)
    t2 = tx(hh, "expense", 100, bank, cat(hh, "Uber"), status="pending", source="subscription")
    ledger.reject_transaction(t2, hh.admin.id)
    assert bal(hh, bank) == P(700)


def test_future_dated_transaction_is_not_counted_until_its_date(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    tx(hh, "expense", 400, bank, cat(hh, "Rent"), on=date.today() + timedelta(days=5))
    assert bal(hh, bank) == P(1000)
    assert balances(hh.id, date.today() + timedelta(days=5))[bank.id] == P(600)


def test_insufficient_balance_blocked(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100)
    with pytest.raises(LedgerError, match="Insufficient"):
        tx(hh, "expense", 100.01, bank, cat(hh, "Uber"))
    tx(hh, "expense", 100, bank, cat(hh, "Uber"))   # exact balance is fine
    assert bal(hh, bank) == 0


def test_amount_must_be_positive(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100)
    with pytest.raises(LedgerError):
        tx(hh, "expense", 0, bank, cat(hh, "Uber"))
    with pytest.raises(LedgerError):
        ledger.parse_money("-5")
    with pytest.raises(LedgerError):
        ledger.parse_money("1.234")
    assert ledger.parse_money("1,234.5") == 123450


def test_credit_card_limit_and_owed(hh):
    card = mk_account(hh, "Axis", "credit_card", credit_limit=1000)
    tx(hh, "expense", 600, card, cat(hh, "Amazon"))
    assert bal(hh, card) == -P(600)
    with pytest.raises(LedgerError, match="credit limit"):
        tx(hh, "expense", 400.01, card, cat(hh, "Amazon"))
    tx(hh, "expense", 400, card, cat(hh, "Amazon"))


def test_addon_card_shares_parent_limit(hh):
    parent = mk_account(hh, "Axis", "credit_card", credit_limit=1000)
    addon = mk_account(hh, "Axis add-on", "credit_card", parent_id=parent.id)
    tx(hh, "expense", 700, parent, cat(hh, "Amazon"))
    with pytest.raises(LedgerError, match="credit limit"):
        tx(hh, "expense", 400, addon, cat(hh, "Amazon"))
    tx(hh, "expense", 300, addon, cat(hh, "Amazon"))
    accs = ledger.accounts_map(hh.id)
    _, owed, limit = ledger.card_pool(accs, balances(hh.id), addon)
    assert (owed, limit) == (P(1000), P(1000))


def test_cc_payment_transfer_reduces_owed(hh):
    bank = mk_account(hh, "HDFC", "bank", start=5000)
    card = mk_account(hh, "Axis", "credit_card", start=-1000, credit_limit=5000)   # already owes 1000 at launch
    assert bal(hh, card) == -P(1000)
    tx(hh, "transfer", 1000, bank, cat(hh, "CC Payment"), to=card)
    assert bal(hh, card) == 0 and bal(hh, bank) == P(4000)


def test_debit_card_and_upi_move_the_linked_bank(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    debit = mk_account(hh, "HDFC Debit", "debit_card", linked_id=bank.id)
    upi = mk_account(hh, "GPay", "upi", linked_id=bank.id)
    tx(hh, "expense", 200, debit, cat(hh, "Zepto"))
    tx(hh, "expense", 300, upi, cat(hh, "Zepto"))
    assert bal(hh, bank) == P(500)
    assert debit.id not in balances(hh.id)          # no balance of its own
    with pytest.raises(LedgerError, match="Insufficient"):
        tx(hh, "expense", 501, upi, cat(hh, "Zepto"))


def test_unlinked_debit_card_rejected(hh):
    debit = mk_account(hh, "Orphan", "debit_card")
    with pytest.raises(LedgerError, match="not linked"):
        tx(hh, "expense", 1, debit, cat(hh, "Zepto"))


def test_transfer_rules(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    loan = mk_account(hh, "Car loan", "loan", start=-500, loan_total=2000)
    with pytest.raises(LedgerError, match="different"):
        tx(hh, "transfer", 10, bank, cat(hh, "Self transfer"), to=bank)
    with pytest.raises(LedgerError, match="outstanding"):
        tx(hh, "transfer", 600, bank, cat(hh, "Loan EMI"), to=loan)
    with pytest.raises(LedgerError, match="only take part in transfers"):
        tx(hh, "expense", 10, loan, cat(hh, "Uber"))
    tx(hh, "transfer", 500, bank, cat(hh, "Loan EMI"), to=loan)
    assert bal(hh, loan) == 0


def test_category_must_match_type_and_window(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    with pytest.raises(LedgerError, match="not a"):
        tx(hh, "expense", 10, bank, cat(hh, "Salary"))
    trip = cat(hh, "Travel & trips")
    trip.end_date = date.today() - timedelta(days=1)
    db.session.commit()
    with pytest.raises(LedgerError, match="not active"):
        tx(hh, "expense", 10, bank, trip)
    tx(hh, "expense", 10, bank, trip, on=date.today() - timedelta(days=5))   # inside the window


def test_archived_account_refused(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    bank.archived = True
    db.session.commit()
    with pytest.raises(LedgerError, match="archived"):
        tx(hh, "expense", 10, bank, cat(hh, "Uber"))


def test_goal_funding_is_zero_impact_on_balances(hh):
    bank = mk_account(hh, "HDFC", "bank", start=1000)
    goal = Goal(household_id=hh.id, name="Car", target=P(50000))
    db.session.add(goal)
    db.session.commit()
    tx(hh, "income", 2000, bank, cat(hh, "Salary"), goal_id=goal.id)
    assert bal(hh, bank) == P(3000)
    s = ledger.goal_summary(goal)
    assert s["saved"] == P(2000) and s["by_account"] == {bank.id: P(2000)}


def test_goal_cannot_sit_in_card_or_wallet_or_be_funded_by_expense(hh):
    card = mk_account(hh, "Axis", "credit_card", credit_limit=1000)
    wallet = mk_account(hh, "Wallet", "wallet", start=100)
    bank = mk_account(hh, "HDFC", "bank", start=100)
    goal = Goal(household_id=hh.id, name="Bike", target=P(1000))
    db.session.add(goal)
    db.session.commit()
    with pytest.raises(LedgerError, match="never a card"):
        ledger.add_goal_entry(hh.id, hh.admin.id, goal.id, card.id, P(10))
    with pytest.raises(LedgerError, match="never a card"):
        tx(hh, "income", 10, wallet, cat(hh, "Gifts"), goal_id=goal.id)
    with pytest.raises(LedgerError, match="Only income"):
        tx(hh, "expense", 10, bank, cat(hh, "Uber"), goal_id=goal.id)
    ledger.add_goal_entry(hh.id, hh.admin.id, goal.id, bank.id, P(10))


def test_goal_earmark_warning(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100)
    goal = Goal(household_id=hh.id, name="Gold", target=P(1000))
    db.session.add(goal)
    db.session.commit()
    ledger.add_goal_entry(hh.id, hh.admin.id, goal.id, bank.id, P(80))
    assert ledger.goal_warning(hh.id, bank.id) is None
    assert "earmarked" in ledger.goal_warning(hh.id, bank.id, extra=P(30))


def test_edit_revalidates_and_delete_cleans_goal_entries(hh):
    bank = mk_account(hh, "HDFC", "bank", start=100)
    t = tx(hh, "expense", 50, bank, cat(hh, "Uber"))
    with pytest.raises(LedgerError, match="Insufficient"):
        ledger.update_transaction(t, dict(date=t.date, amount=P(150), account_id=bank.id, category_id=t.category_id))
    ledger.update_transaction(t, dict(date=t.date, amount=P(100), account_id=bank.id, category_id=t.category_id))
    assert bal(hh, bank) == 0
    goal = Goal(household_id=hh.id, name="G", target=100)
    db.session.add(goal)
    db.session.commit()
    inc = tx(hh, "income", 10, bank, cat(hh, "Gifts"), goal_id=goal.id)
    ledger.delete_transaction(inc)
    assert GoalEntry.query.count() == 0


def test_inr_formatting():
    assert ledger.inr(123456789) == "₹12,34,567.89"
    assert ledger.inr(-5000) == "−₹50.00"
    assert ledger.inr(99) == "₹0.99"
