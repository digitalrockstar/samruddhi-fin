import io
import re
from datetime import date, timedelta

import pytest

from app import Account, Category, CreditCardProfile, CreditCardStatement, CreditLimitGroup, Transaction, create_app, db, money_to_paise


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
        assert Transaction.query.filter_by(description="Tea").one().amount_paise == 4550


def test_post_without_csrf_is_rejected(client):
    response = client.post("/accounts", data={"name": "Should not appear"})
    assert response.status_code == 400


def test_password_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "correct-horse")
    application = create_app({"TESTING": True, "SECRET_KEY": "test-only-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'auth.sqlite'}"})
    client = application.test_client()
    assert client.get("/").status_code == 302
    token_value = token(client, "/login")
    response = client.post("/login", data={"_csrf_token": token_value, "password": "correct-horse"},
                           follow_redirects=True)
    assert response.status_code == 200
    assert b"Net cash flow" in response.data



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
