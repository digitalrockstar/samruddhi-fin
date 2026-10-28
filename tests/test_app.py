import io
import re
from datetime import date, timedelta

import pytest

from app import (
    Account, AccountOwner, Budget, CashbackEvent, CashbackScheme, Category, CategoryParent, CreditCardProfile,
    CreditCardStatement, CreditLimitGroup, FamilyMember, Goal, GoalEarmark, Subscription,
    Transaction, TransactionBeneficiary, TransactionMetadata, create_app, db, money_to_paise,
)


@pytest.fixture()
def app(tmp_path):
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-secret",
        "APP_PASSWORD": "",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.sqlite'}",
    })
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def token(client, path="/"):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    if not match:
        # Some GET pages do not contain a form; visit a form page to initialise the session token.
        response = client.get("/transactions/new")
        assert response.status_code == 200
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match, "expected CSRF token in rendered form"
    return match.group(1).decode()


def post_form(client, path, data, **kwargs):
    csrf = token(client, kwargs.pop("token_path", "/transactions/new"))
    return client.post(path, data={**data, "_csrf_token": csrf}, **kwargs)


def test_money_uses_integer_paise_and_rejects_invalid_values():
    assert money_to_paise("1250.50") == 125050
    assert money_to_paise("₹1,200.25") == 120025
    assert money_to_paise("0", allow_zero=True) == 0
    with pytest.raises(ValueError):
        money_to_paise("0")
    with pytest.raises(ValueError):
        money_to_paise("not money")


def test_dashboard_and_defaults_render(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Net cash flow" in response.data
    assert b"Main bank" in response.data
    assert b"Where it goes" in response.data


def test_transaction_crud_and_transfer_semantics(client, app):
    with app.app_context():
        cash = Account.query.filter_by(name="Cash").one()
        bank = Account.query.filter_by(name="Main bank").one()
        groceries = Category.query.filter_by(name="Groceries").one()
        cash_id, bank_id, category_id = cash.id, bank.id, groceries.id

    response = post_form(client, "/transactions/new", {
        "kind": "income", "amount": "50000.00", "description": "Salary",
        "transaction_date": "2026-10-01", "account_id": str(bank_id),
        "category_id": "", "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Transaction saved" in response.data

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "850.25", "description": "BigBasket",
        "transaction_date": "2026-10-02", "account_id": str(cash_id),
        "category_id": str(category_id), "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"BigBasket" in response.data

    response = post_form(client, "/transactions/new", {
        "kind": "transfer", "amount": "1000", "description": "Move to savings",
        "transaction_date": "2026-10-03", "account_id": str(bank_id),
        "transfer_to_account_id": str(cash_id), "category_id": "", "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        assert Transaction.query.filter_by(kind="income").count() == 1
        assert Transaction.query.filter_by(kind="expense").one().amount_paise == 85025
        assert Transaction.query.filter_by(kind="transfer").one().transfer_to_account_id == cash_id
        bank_page = client.get("/accounts")
        assert bank_page.status_code == 200
        assert b"49,000.00" in bank_page.data
        assert b"BigBasket" in client.get("/transactions?month=2026-10").data

        expense_id = Transaction.query.filter_by(description="BigBasket").one().id

    response = post_form(client, f"/transactions/{expense_id}/edit", {
        "kind": "expense", "amount": "900", "description": "BigBasket updated",
        "transaction_date": "2026-10-02", "account_id": str(cash_id),
        "category_id": str(category_id), "notes": "weekly shop",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"BigBasket updated" in response.data


def test_csv_export_and_preview_confirmation(client, app):
    response = client.get("/export.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"date,type,amount,description,account,category" in response.data

    with app.app_context():
        account_name = Account.query.filter_by(name="Cash").one().name
    content = f"date,type,amount,description,account,category\n2026-10-01,expense,45.50,Tea,{account_name},Dining\n"
    response = post_form(client, "/import", {
        "file": (io.BytesIO(content.encode()), "transactions.csv")
    }, token_path="/import", content_type="multipart/form-data")
    assert response.status_code == 200
    assert b"Nothing has been saved yet" in response.data
    with app.app_context():
        assert Transaction.query.count() == 0
    response = post_form(client, "/import", {"action": "confirm"}, token_path="/import", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        imported = Transaction.query.filter_by(description="Tea").one()
        assert imported.amount_paise == 4550
        imported_meta = db.session.get(TransactionMetadata, imported.id)
        assert imported_meta.source == "import"
        assert imported_meta.budget_scope == "family"
        assert imported_meta.payer_snapshot_set is True


def test_post_without_csrf_is_rejected(client):
    response = client.post("/accounts", data={"name": "Should not appear"})
    assert response.status_code == 400


def test_individual_authentication_setup_and_login(tmp_path):
    application = create_app({"TESTING": True, "TEST_AUTH_REQUIRED": True, "SECRET_KEY": "test-only-secret",
        "APP_PASSWORD": "", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'auth.sqlite'}"})
    client = application.test_client()
    assert client.get("/").status_code == 302
    setup_token = token(client, "/setup")
    response = client.post("/setup", data={
        "_csrf_token": setup_token, "name": "Family Admin", "email": "admin@example.test",
        "password": "a-strong-test-password", "confirm_password": "a-strong-test-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Net cash flow" in response.data
    assert b"admin@example.test" not in response.data
    with application.app_context():
        primary_admin = FamilyMember.query.filter_by(email="admin@example.test").one()
        assert primary_admin.username == "akshayp"
        assert primary_admin.role == "admin"
    csrf = token(client, "/")
    client.post("/logout", data={"_csrf_token": csrf})
    login_token = token(client, "/login")
    response = client.post("/login", data={
        "_csrf_token": login_token, "identifier": "akshayp", "password": "a-strong-test-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Net cash flow" in response.data

    password_token = token(client, "/change-password")
    response = client.post("/change-password", data={
        "_csrf_token": password_token,
        "old_password": "a-strong-test-password",
        "new_password": "a-new-strong-test-password",
        "confirm_password": "a-new-strong-test-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Password changed successfully" in response.data
    client.post("/logout", data={"_csrf_token": token(client, "/")})
    old_token = token(client, "/login")
    response = client.post("/login", data={
        "_csrf_token": old_token, "email": "admin@example.test", "password": "a-strong-test-password",
    })
    assert b"did not match" in response.data
    new_token = token(client, "/login")
    response = client.post("/login", data={
        "_csrf_token": new_token, "email": "admin@example.test", "password": "a-new-strong-test-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Net cash flow" in response.data

    bad_client = application.test_client()
    bad_token = token(bad_client, "/login")
    response = bad_client.post("/login", data={
        "_csrf_token": bad_token, "email": "admin@example.test", "password": "wrong-password",
    })
    assert response.status_code == 200
    assert b"did not match" in response.data



def test_primary_admin_is_unique_and_member_cannot_manage_people_or_approvals(tmp_path):
    application = create_app({
        "TESTING": True, "TEST_AUTH_REQUIRED": True, "SECRET_KEY": "test-only-secret",
        "APP_PASSWORD": "", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'admin-permissions.sqlite'}",
    })
    client = application.test_client()
    setup_token = token(client, "/setup")
    response = client.post("/setup", data={
        "_csrf_token": setup_token, "name": "Primary Admin", "email": "akshayp@example.test",
        "password": "a-strong-test-password", "confirm_password": "a-strong-test-password",
    }, follow_redirects=True)
    assert response.status_code == 200

    # Supplying role=admin must not grant admin rights to another household login.
    response = post_form(client, "/people", {
        "action": "add_member", "name": "Household Member", "username": "member-one",
        "email": "member@example.test", "password": "another-strong-password", "role": "admin",
    }, follow_redirects=True)
    assert response.status_code == 200
    with application.app_context():
        admin = FamilyMember.query.filter_by(username="akshayp").one()
        member = FamilyMember.query.filter_by(username="member-one").one()
        assert admin.role == "admin"
        assert member.role == "member"

    client.post("/logout", data={"_csrf_token": token(client, "/")})
    login_token = token(client, "/login")
    response = client.post("/login", data={
        "_csrf_token": login_token, "identifier": "member-one",
        "password": "another-strong-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert client.get("/people").status_code == 403
    assert client.get("/approvals").status_code == 403


def test_archiving_is_reversible_and_removes_transaction_from_active_views(client, app):
    with app.app_context():
        cash = Account.query.filter_by(name="Cash").one()
        category = Category.query.filter_by(name="Groceries").one()
        cash_id, category_id = cash.id, category.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "125.00", "description": "Archive test purchase",
        "transaction_date": "2026-10-10", "account_id": str(cash_id),
        "category_id": str(category_id), "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        txn_id = Transaction.query.filter_by(description="Archive test purchase").one().id

    response = post_form(client, f"/transactions/{txn_id}/archive", {}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Transaction archived" in response.data
    assert b"Archive test purchase" not in client.get("/transactions?month=2026-10").data
    assert b"Archive test purchase" in client.get("/transactions?month=2026-10&archived=1").data

    response = post_form(client, f"/transactions/{txn_id}/restore", {}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Transaction restored" in response.data
    assert b"Archive test purchase" in client.get("/transactions?month=2026-10").data
    with app.app_context():
        assert db.session.get(Transaction, txn_id).is_archived is False


def test_credit_card_purchases_increase_amount_owed_and_payments_reduce_it(client, app):
    with app.app_context():
        credit = Account.query.filter_by(name="Credit card").one()
        bank = Account.query.filter_by(name="Main bank").one()
        category = Category.query.filter_by(name="Groceries").one()
        credit_id, bank_id, category_id = credit.id, bank.id, category.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "1200.00", "description": "Card purchase",
        "transaction_date": "2026-10-05", "account_id": str(credit_id),
        "category_id": str(category_id), "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200

    response = post_form(client, "/transactions/new", {
        "kind": "transfer", "amount": "300.00", "description": "Card payment",
        "transaction_date": "2026-10-06", "account_id": str(bank_id),
        "transfer_to_account_id": str(credit_id), "category_id": "", "notes": "",
    }, follow_redirects=True)
    assert response.status_code == 200

    account_page = client.get("/accounts")
    assert account_page.status_code == 200
    assert b"Amount owed" in account_page.data
    assert b"\xe2\x82\xb9900.00" in account_page.data


def test_credit_cards_support_shared_limits_statements_reminders_and_payments(client, app):
    with app.app_context():
        main_card = Account.query.filter_by(name="Credit card").one()
        second_card = Account(name="Add-on card", account_type="credit", colour="#7297d2")
        category = Category.query.filter_by(name="Groceries").one()
        main_card_id, category_id = main_card.id, category.id
        db.session.add(second_card)
        db.session.commit()
        second_card_id = second_card.id

    response = post_form(client, "/credit-cards", {
        "action": "create_group", "group_name": "Family shared limit", "group_limit": "1000",
    }, token_path="/credit-cards", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        group_id = CreditLimitGroup.query.filter_by(name="Family shared limit").one().id

    for card_id in (main_card_id, second_card_id):
        response = post_form(client, "/credit-cards", {
            "action": "save_profile", "account_id": str(card_id), "limit_mode": "shared",
            "shared_group_id": str(group_id), "statement_day": "5", "due_day": "25",
        }, token_path="/credit-cards", follow_redirects=True)
        assert response.status_code == 200

    with app.app_context():
        db.session.add_all([
            Transaction(transaction_date=date.today(), kind="expense", amount_paise=12000,
                description="Primary card purchase", account_id=main_card_id, category_id=category_id),
            Transaction(transaction_date=date.today(), kind="expense", amount_paise=8000,
                description="Add-on card purchase", account_id=second_card_id, category_id=category_id),
        ])
        db.session.commit()

    due_date, statement_date = date.today() + timedelta(days=2), date.today()
    response = post_form(client, "/credit-cards", {
        "action": "save_statement", "account_id": str(main_card_id),
        "statement_month": statement_date.strftime("%Y-%m"), "statement_date": statement_date.isoformat(),
        "due_date": due_date.isoformat(), "total_due": "100.00", "minimum_due": "25.00", "notes": "",
    }, token_path="/credit-cards", follow_redirects=True)
    assert response.status_code == 200
    assert b"Payment due in 2 days" in response.data
    assert b"200.00" in response.data
    assert b"800.00" in response.data

    with app.app_context():
        statement_id = CreditCardStatement.query.filter_by(account_id=main_card_id).one().id
        bank_id = Account.query.filter_by(name="Main bank").one().id
    response = post_form(client, "/credit-cards", {
        "action": "record_payment", "statement_id": str(statement_id),
        "payment_account_id": str(bank_id), "payment_amount": "30.00",
        "payment_date": date.today().isoformat(),
    }, token_path="/credit-cards", follow_redirects=True)
    assert response.status_code == 200
    assert b"statement payment recorded" in response.data
    assert b"70.00" in response.data
    assert b"<small>Paid</small><strong>\xe2\x82\xb930.00</strong>" in response.data
    assert b"<small>Remaining</small><strong>\xe2\x82\xb970.00</strong>" in response.data
    with app.app_context():
        statement = db.session.get(CreditCardStatement, statement_id)
        payment = Transaction.query.filter_by(kind="transfer", transfer_to_account_id=main_card_id).one()
        profile = CreditCardProfile.query.filter_by(account_id=second_card_id).one()
        payment_id = payment.id
        assert statement.total_due_paise == 10000
        assert profile.limit_mode == "shared"
        assert payment.description.startswith("Credit card payment")

    response = post_form(client, f"/transactions/{payment_id}/archive", {}, follow_redirects=True)
    assert response.status_code == 200
    archived_page = client.get("/credit-cards").data
    assert b"<small>Paid</small><strong>\xe2\x82\xb90.00</strong>" in archived_page
    assert b"<small>Remaining</small><strong>\xe2\x82\xb9100.00</strong>" in archived_page
    response = post_form(client, f"/transactions/{payment_id}/restore", {}, follow_redirects=True)
    assert response.status_code == 200
    restored_page = client.get("/credit-cards").data
    assert b"<small>Paid</small><strong>\xe2\x82\xb930.00</strong>" in restored_page
    assert b"<small>Remaining</small><strong>\xe2\x82\xb970.00</strong>" in restored_page

    with app.app_context():
        statement = db.session.get(CreditCardStatement, statement_id)
        statement.due_date = date.today()
        db.session.commit()
    assert b"Payment due today" in client.get("/credit-cards").data
    with app.app_context():
        statement = db.session.get(CreditCardStatement, statement_id)
        statement.due_date = date.today() - timedelta(days=1)
        db.session.commit()
    assert b"Overdue by 1 day(s)" in client.get("/credit-cards").data

    response = post_form(client, "/credit-cards", {
        "action": "save_profile", "account_id": str(second_card_id), "limit_mode": "individual",
        "individual_limit": "500.00", "statement_day": "10", "due_day": "28",
    }, token_path="/credit-cards", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        profile = CreditCardProfile.query.filter_by(account_id=second_card_id).one()
        assert profile.limit_mode == "individual"
        assert profile.individual_limit_paise == 50000

    response = post_form(client, "/credit-cards", {
        "action": "update_group", "group_id": str(group_id),
        "group_name": "Updated family group", "group_limit": "1100.00",
    }, token_path="/credit-cards", follow_redirects=True)
    assert response.status_code == 200
    assert b"Shared credit limit group updated" in response.data
    assert b"Updated family group" in response.data
    assert b"1100.00" in response.data


def test_budget_and_beneficiary_amount_allocations_are_separate(app, client):
    with app.app_context():
        groceries = Category.query.filter_by(name="Groceries").one()
        cash = Account.query.filter_by(name="Cash").one()
        first = FamilyMember(name="Person A", email="person-a@example.test", password_hash="unused", role="member")
        second = FamilyMember(name="Person B", email="person-b@example.test", password_hash="unused", role="member")
        db.session.add_all([first, second])
        db.session.flush()
        budget = Budget(name="Family groceries", owner_type="family", category_id=groceries.id,
            period_type="monthly", amount_paise=1000000, start_date=date.today().replace(day=1))
        db.session.add(budget)
        db.session.commit()
        first_id, second_id, budget_id, cash_id, category_id = first.id, second.id, budget.id, cash.id, groceries.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "100.00", "description": "Shared grocery run",
        "transaction_date": date.today().isoformat(), "account_id": str(cash_id),
        "category_id": str(category_id), "notes": "",
        "budget_selection": f"budget:{budget_id}", "beneficiary_ids": [str(first_id), str(second_id)],
        "split_method": "amount", f"beneficiary_amount_{first_id}": "60.00",
        f"beneficiary_amount_{second_id}": "40.00", "cashback_rate": "", "cashback_destination_account_id": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        txn = Transaction.query.filter_by(description="Shared grocery run").one()
        metadata = db.session.get(TransactionMetadata, txn.id)
        allocations = {row.member_id: row.amount_paise for row in TransactionBeneficiary.query.filter_by(transaction_id=txn.id).all()}
        assert metadata.budget_id == budget_id
        assert metadata.budget_scope == "family"
        assert allocations == {first_id: 6000, second_id: 4000}
        assert sum(allocations.values()) == txn.amount_paise
        assert TransactionBeneficiary.query.filter_by(transaction_id=txn.id).count() == 2


def test_goal_earmark_does_not_move_money_or_create_transaction(app, client):
    with app.app_context():
        account = Account(name="Bike fund account", account_type="savings", opening_balance_paise=34599200, colour="#7297d2")
        db.session.add(account)
        goal = Goal(name="Bike", owner_type="family", target_paise=15000000)
        db.session.add(goal)
        db.session.commit()
        account_id, goal_id = account.id, goal.id

    response = post_form(client, "/goals", {
        "action": "earmark", "goal_id": str(goal_id), "account_id": str(account_id), "amount": "100000.00",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        earmark = GoalEarmark.query.filter_by(goal_id=goal_id, account_id=account_id).one()
        assert earmark.amount_paise == 10000000
        account = db.session.get(Account, account_id)
        assert account.opening_balance_paise == 34599200
        assert Transaction.query.count() == 0


def test_due_subscription_creates_new_transaction_and_future_amount_does_not_rewrite_history(app, client):
    with app.app_context():
        bank = Account.query.filter_by(name="Main bank").one()
        bills = Category.query.filter_by(name="Bills").one()
        member = FamilyMember(name="Subscription member", email="sub@example.test", password_hash="unused", role="admin")
        db.session.add(member)
        db.session.commit()
        bank_id, category_id = bank.id, bills.id
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    response = post_form(client, "/subscriptions", {
        "action": "create", "name": "Microsoft OneDrive", "account_id": str(bank_id),
        "category_id": str(category_id), "amount": "8549.00", "frequency": "annual",
        "interval_weeks": "6", "next_date": yesterday, "budget_selection": "family", "notes": "",
    }, token_path="/transactions/new", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        generated = Transaction.query.filter_by(description="Microsoft OneDrive").one()
        assert generated.amount_paise == 854900
        subscription = Subscription.query.filter_by(name="Microsoft OneDrive").one()
        subscription_id = subscription.id

    response = post_form(client, "/subscriptions", {
        "action": "update", "subscription_id": str(subscription_id), "amount": "8708.00",
        "next_date": date.today().isoformat(), "is_active": "1",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        rows = Transaction.query.filter_by(description="Microsoft OneDrive").order_by(Transaction.id).all()
        assert [row.amount_paise for row in rows] == [854900, 870800]
        subscription = db.session.get(Subscription, subscription_id)
        assert subscription.amount_paise == 870800
        assert subscription.next_date > date.today()


def test_cashback_waits_for_approval_and_credits_selected_destination(app, client):
    with app.app_context():
        card = Account.query.filter_by(name="Credit card").one()
        destination = Account.query.filter_by(name="Main bank").one()
        scheme = CashbackScheme(name="x3008 AZ reward", account_id=card.id, merchant_keyword="AZ",
            rate_bps=500, destination_account_id=destination.id, delay_days=0)
        db.session.add(scheme)
        db.session.commit()
        card_id, destination_id = card.id, destination.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "1000.00", "description": "AZ purchase",
        "transaction_date": date.today().isoformat(), "account_id": str(card_id),
        "category_id": "", "notes": "", "budget_selection": "family",
        "cashback_scheme_id": "", "cashback_rate": "", "cashback_destination_account_id": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        source = Transaction.query.filter_by(description="AZ purchase").one()
        event = CashbackEvent.query.filter_by(transaction_id=source.id).one()
        assert event.expected_paise == 5000
        assert event.destination_account_id == destination_id
        assert event.status == "pending_approval"
        assert Transaction.query.filter_by(account_id=destination_id, kind="income").count() == 0
        event_id = event.id

    response = post_form(client, "/approvals", {
        "event_id": str(event_id), "action": "approve", "actual_amount": "50.00",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        event = db.session.get(CashbackEvent, event_id)
        assert event.status == "approved"
        assert event.actual_paise == 5000
        posted = db.session.get(Transaction, event.posted_transaction_id)
        assert posted.account_id == destination_id
        assert db.session.get(TransactionMetadata, posted.id).source == "cashback"
        assert Transaction.query.filter_by(account_id=destination_id, kind="income").count() == 1
        posted_id = posted.id

    response = post_form(client, f"/transactions/{posted_id}/archive", {}, follow_redirects=True)
    assert response.status_code == 200
    assert b"linked to its approval" in response.data
    with app.app_context():
        assert db.session.get(Transaction, posted_id).is_archived is False


def test_family_finance_screens_render(client):
    for path in ("/setup", "/people", "/budgets", "/goals", "/subscriptions",
                 "/approvals", "/cashback-schemes", "/more", "/accounts", "/categories",
                 "/credit-cards", "/transactions/new"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:500])


def test_cashback_to_card_reduces_owed_balance_and_is_not_regular_income(app, client):
    with app.app_context():
        card = Account.query.filter_by(name="Credit card").one()
        scheme = CashbackScheme(name="x2504 FK reward", account_id=card.id, merchant_keyword="FK",
            rate_bps=500, destination_account_id=card.id, delay_days=0)
        db.session.add(scheme)
        db.session.commit()
        card_id = card.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "2000.00", "description": "FK purchase",
        "transaction_date": date.today().isoformat(), "account_id": str(card_id),
        "category_id": "", "notes": "", "budget_selection": "family",
        "cashback_scheme_id": "", "cashback_rate": "", "cashback_destination_account_id": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        source = Transaction.query.filter_by(description="FK purchase").one()
        event = CashbackEvent.query.filter_by(transaction_id=source.id).one()
        assert event.expected_paise == 10000
        event_id = event.id

    response = post_form(client, "/approvals", {
        "event_id": str(event_id), "action": "approve", "actual_amount": "100.00",
    }, follow_redirects=True)
    assert response.status_code == 200
    dashboard = client.get("/")
    assert b"Cashback received" in dashboard.data
    assert b"Money in" in dashboard.data
    with app.app_context():
        card = db.session.get(Account, card_id)
        assert card.opening_balance_paise == 0
        # Credit-card purchases increase debt; cashback applied to that same card reduces it.
        assert "₹1,900.00".encode() in client.get("/accounts").data
        posted_event = db.session.get(CashbackEvent, event_id)
        posted = db.session.get(Transaction, posted_event.posted_transaction_id)
        assert posted.kind == "income" and posted.account_id == card_id
        dashboard = client.get("/")
        assert "₹2,000.00".encode() in dashboard.data
        assert "₹100.00".encode() in dashboard.data


def test_transaction_payer_is_snapshotted_when_account_owner_changes(app, client):
    with app.app_context():
        cash = Account.query.filter_by(name="Cash").one()
        category = Category.query.filter_by(name="Groceries").one()
        first = FamilyMember(name="Original payer", email="original-payer@example.test", password_hash="unused", role="member")
        second = FamilyMember(name="New payer", email="new-payer@example.test", password_hash="unused", role="member")
        db.session.add_all([first, second])
        db.session.flush()
        db.session.add(AccountOwner(account_id=cash.id, member_id=first.id))
        db.session.commit()
        cash_id, category_id, first_id, second_id = cash.id, category.id, first.id, second.id

    response = post_form(client, "/transactions/new", {
        "kind": "expense", "amount": "25.00", "description": "Historical payer check",
        "transaction_date": date.today().isoformat(), "account_id": str(cash_id),
        "category_id": str(category_id), "notes": "", "budget_selection": "family",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        txn = Transaction.query.filter_by(description="Historical payer check").one()
        txn_id = txn.id
        meta = db.session.get(TransactionMetadata, txn.id)
        assert meta.payer_member_id == first_id
        owner = db.session.get(AccountOwner, cash_id)
        owner.member_id = second_id
        db.session.commit()

    response = client.get("/transactions")
    assert response.status_code == 200
    assert b"Original payer" in response.data


def test_category_hierarchy_is_limited_to_l1_and_l2(client, app):
    response = post_form(client, "/categories", {
        "name": "Grocery", "kind": "expense", "parent_category_id": "", "colour": "#7297d2",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        parent_id = Category.query.filter_by(name="Grocery").one().id

    response = post_form(client, "/categories", {
        "name": "Zepto", "kind": "expense", "parent_category_id": str(parent_id), "colour": "#7297d2",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        child = Category.query.filter_by(name="Zepto").one()
        child_id = child.id
        assert db.session.get(CategoryParent, child_id).parent_category_id == parent_id

    response = post_form(client, "/categories", {
        "name": "Fresh delivery", "kind": "expense", "parent_category_id": str(child_id), "colour": "#7297d2",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"one level" in response.data
    with app.app_context():
        assert Category.query.filter_by(name="Fresh delivery").count() == 0


def test_legacy_transaction_metadata_backfill_is_idempotent(tmp_path):
    database = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    config = {"TESTING": True, "SECRET_KEY": "test-only-secret", "SQLALCHEMY_DATABASE_URI": database}
    first_app = create_app(config)
    with first_app.app_context():
        account = Account.query.filter_by(name="Cash").one()
        category = Category.query.filter_by(name="Groceries").one()
        transaction = Transaction(
            transaction_date=date.today(), kind="expense", amount_paise=2500,
            description="Pre-family transaction", account_id=account.id, category_id=category.id)
        db.session.add(transaction)
        db.session.commit()
        transaction_id = transaction.id
        db.session.remove()

    second_app = create_app(config)
    with second_app.app_context():
        metadata = db.session.get(TransactionMetadata, transaction_id)
        assert metadata is not None
        assert metadata.source == "legacy"
        assert metadata.payer_snapshot_set is True
        assert metadata.payer_member_id is None
        db.session.remove()

    third_app = create_app(config)
    with third_app.app_context():
        assert TransactionMetadata.query.filter_by(transaction_id=transaction_id).count() == 1
        db.session.remove()
        db.drop_all()
