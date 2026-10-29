"""End-to-end through the web layer: setup, roles, every page renders, forms obey the ledger."""
from datetime import date

import pytest

from samruddhi import ledger
from samruddhi.models import Account, Category, Household, Transaction, User, db


@pytest.fixture()
def client(app):
    return app.test_client()


def setup_admin(client):
    return client.post("/setup", data=dict(household="Home", name="Asha", username="asha", email="asha@x.in",
                                           password="password123", password2="password123"), follow_redirects=True)


def login(client, user, pw="password123"):
    client.post("/logout")
    return client.post("/login", data=dict(username=user, password=pw), follow_redirects=True)


def cat_id(name):
    return Category.query.filter(Category.name == name, Category.parent_id.isnot(None)).first().id


def test_first_user_becomes_admin_and_household_is_named(client):
    assert client.get("/").status_code == 302 and "/setup" in client.get("/").headers["Location"]
    r = setup_admin(client)
    assert r.status_code == 200
    u = User.query.one()
    assert u.is_admin and Household.query.one().name == "Home"
    assert Category.query.count() > 20                       # starter categories seeded
    assert client.get("/setup").status_code == 302           # setup closes after the first household


def test_default_household_name(client):
    client.post("/setup", data=dict(household="", name="A", username="aaa", email="a@x.in", password="password123", password2="password123"))
    assert Household.query.one().name == "HOUSEHOLD # 1"


def test_setup_validation(client):
    r = client.post("/setup", data=dict(name="A", username="a", email="bad", password="short", password2="short"))
    assert User.query.count() == 0 and b"Username needs" in r.data


def test_every_page_renders_for_admin(client):
    setup_admin(client)
    h = Household.query.one()
    bank = Account(household_id=h.id, name="HDFC", kind="bank", institution="HDFC", starting_balance=500000)
    card = Account(household_id=h.id, name="Axis", kind="credit_card", institution="Axis", credit_limit=5000000, due_day=5)
    db.session.add_all([bank, card])
    db.session.commit()
    ledger.create_transaction(h.id, 1, dict(type="expense", date=date.today(), amount=12300, account_id=card.id,
                                            category_id=cat_id("Zepto"), note="milk"))
    for path in ["/", "/api/dashboard", "/activity", "/activity?pending=1&q=milk", "/activity.csv", "/activity.csv?all=1",
                 "/transactions/new", "/transactions/new?type=income", "/transactions/new?type=transfer", "/transactions/1",
                 "/approvals", "/subscriptions", "/more", "/accounts", "/accounts?archived=1", "/accounts/new",
                 "/accounts/new?kind=credit_card", "/accounts/new?kind=loan", "/accounts/new?kind=debit_card",
                 "/accounts/new?kind=investment", "/accounts/1", "/cashbacks", "/cashbacks/new", "/budgets", "/budgets/new",
                 "/goals", "/goals/new", "/categories", "/categories/new", "/people", "/people/new", "/settings", "/reset",
                 "/healthz", "/manifest.webmanifest", "/sw.js"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
    assert b"milk" in client.get("/activity.csv").data
    d = client.get("/api/dashboard?period=year&person=family&kind=credit_card").get_json()
    assert d["expense"] == 12300 and d["cards"] == 1


def test_add_transaction_form_and_ledger_error_shown_not_crashed(client):
    setup_admin(client)
    h = Household.query.one()
    bank = Account(household_id=h.id, name="HDFC", kind="bank", institution="HDFC", starting_balance=10000)
    db.session.add(bank)
    db.session.commit()
    base = dict(type="expense", date=date.today().isoformat(), account_id=bank.id, category_id=cat_id("Uber"), recurrence="none")
    r = client.post("/transactions/new", data=dict(base, amount="250"), follow_redirects=True)
    assert b"Insufficient balance" in r.data and Transaction.query.count() == 0
    assert b'value="250"' in r.data                                         # the form keeps what you typed
    client.post("/transactions/new", data=dict(base, amount="40.50"))
    t = Transaction.query.one()
    assert t.amount == 4050 and ledger.balances(h.id)[bank.id] == 5950


def test_transfer_and_goal_via_form_and_custom_split(client):
    setup_admin(client)
    h = Household.query.one()
    a = Account(household_id=h.id, name="A", kind="bank", institution="A", starting_balance=100000)
    b = Account(household_id=h.id, name="B", kind="wallet", institution="B")
    db.session.add_all([a, b])
    ravi = User(household_id=h.id, name="Kid", is_minor=True)
    db.session.add(ravi)
    db.session.commit()
    client.post("/transactions/new", data=dict(type="transfer", date=date.today().isoformat(), amount="100", account_id=a.id,
                                               to_account_id=b.id, recurrence="none"))
    assert ledger.balances(h.id)[b.id] == 10000
    r = client.post("/transactions/new", data=dict(type="expense", date=date.today().isoformat(), amount="100", account_id=a.id,
                    category_id=cat_id("Toys"), scope="individuals", split="custom", beneficiaries=[1, ravi.id],
                    share_1="70", **{f"share_{ravi.id}": "20"}, recurrence="none"), follow_redirects=True)
    assert b"add up" in r.data
    client.post("/transactions/new", data=dict(type="expense", date=date.today().isoformat(), amount="100", account_id=a.id,
                category_id=cat_id("Toys"), scope="individuals", split="custom", beneficiaries=[1, ravi.id],
                share_1="70", **{f"share_{ravi.id}": "30"}, recurrence="none"))
    t = Transaction.query.filter_by(type="expense").one()
    assert dict(ledger.shares(t)) == {1: 7000, ravi.id: 3000}


def test_member_permissions_and_minor_cannot_login(client):
    setup_admin(client)
    h = Household.query.one()
    client.post("/people/new", data=dict(name="Ravi", username="ravi", email="r@x.in", password="password123"))
    client.post("/people/new", data=dict(name="Baby", is_minor="1"))
    baby = User.query.filter_by(name="Baby").one()
    assert baby.is_minor and baby.password_hash is None and not baby.can_login
    assert b"Wrong username" in client.post("/login", data=dict(username="baby", password="x"), follow_redirects=True).data
    login(client, "ravi")
    for path in ("/accounts/new", "/people/new", "/reset", "/categories/new", "/cashbacks/new"):
        assert client.get(path).status_code == 403, path
    assert client.post("/reset/run", data=dict(mode="full", password="password123", confirm="RESET")).status_code == 403
    assert client.get("/budgets/new").status_code == 200 and client.get("/goals/new").status_code == 200
    # a member cannot create a family budget even by forging the field
    client.post("/budgets/new", data=dict(owner_id="", category_id=cat_id("Zepto"), period="monthly", amount="100"))
    from samruddhi.models import Budget
    assert Budget.query.count() == 1 and Budget.query.one().owner_id == User.query.filter_by(username="ravi").one().id


def test_member_cannot_approve_and_cannot_edit_others_transactions(client):
    setup_admin(client)
    h = Household.query.one()
    bank = Account(household_id=h.id, name="HDFC", kind="bank", institution="HDFC", starting_balance=100000)
    db.session.add(bank)
    db.session.commit()
    t = ledger.create_transaction(h.id, 1, dict(type="expense", date=date.today(), amount=100, account_id=bank.id,
                                                category_id=cat_id("Uber"), status="pending", source="subscription"))
    own = ledger.create_transaction(h.id, 1, dict(type="expense", date=date.today(), amount=100, account_id=bank.id, category_id=cat_id("Uber")))
    client.post("/people/new", data=dict(name="Ravi", username="ravi", email="r@x.in", password="password123"))
    login(client, "ravi")
    assert client.post(f"/approvals/txn/{t.id}/approve").status_code == 403
    assert client.get(f"/transactions/{own.id}").status_code == 403
    assert client.get("/approvals").status_code == 200


def test_last_admin_cannot_be_demoted_and_self_deactivation_blocked(client):
    setup_admin(client)
    r = client.post("/people/1", data=dict(name="Asha", username="asha", email="asha@x.in"), follow_redirects=True)
    assert b"at least one admin" in r.data and db.session.get(User, 1).is_admin
    assert b"at least one admin" in client.post("/people/1/active", follow_redirects=True).data and db.session.get(User, 1).active


def test_account_rules_via_form(client):
    setup_admin(client)
    r = client.post("/accounts/new?kind=credit_card", data=dict(kind="credit_card", name="Axis", credit_limit="", due_day="5"), follow_redirects=True)
    assert b"Enter the credit limit" in r.data
    client.post("/accounts/new?kind=credit_card", data=dict(name="Axis", credit_limit="50000", due_day="5", starting="1000"))
    card = Account.query.one()
    assert card.starting_balance == -100000 and card.credit_limit == 5000000      # owed amount stored as negative
    r = client.post("/accounts/new?kind=debit_card", data=dict(name="Debit"), follow_redirects=True)
    assert b"Link this to the bank" in r.data
    client.post(f"/accounts/{card.id}/archive")
    assert db.session.get(Account, card.id).archived and Account.query.count() == 1      # archived, never deleted


def test_csrf_enforced_outside_tests(app):
    app.config["TESTING"] = False
    c = app.test_client()
    r = c.post("/setup", data=dict(name="A", username="aaa", email="a@x.in", password="password123", password2="password123"))
    assert r.status_code == 400 and User.query.count() == 0


def test_reset_requires_password_and_confirmation_and_full_reset_returns_to_setup(client):
    setup_admin(client)
    r = client.post("/reset/run", data=dict(mode="full", password="wrong", confirm="RESET"), follow_redirects=True)
    assert b"Wrong password" in r.data and Household.query.count() == 1
    r = client.post("/reset/run", data=dict(mode="full", password="password123", confirm="nope"), follow_redirects=True)
    assert b"Type RESET" in r.data and Household.query.count() == 1
    z = client.post("/reset/backup")
    assert z.status_code == 200 and z.mimetype == "application/zip"
    client.post("/reset/run", data=dict(mode="full", password="password123", confirm="RESET"))
    assert Household.query.count() == 0 and "/setup" in client.get("/").headers["Location"]


def test_theme_choice_persists(client):
    setup_admin(client)
    client.post("/settings/theme", data=dict(theme="forest"))
    assert db.session.get(User, 1).theme == "forest" and b'data-theme="forest"' in client.get("/").data
    client.post("/settings/theme", data=dict(theme="not-a-theme"))
    assert db.session.get(User, 1).theme == "forest"
