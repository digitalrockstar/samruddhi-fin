import io
import re

import pytest

from app import Account, Category, Transaction, create_app, db, money_to_paise


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
