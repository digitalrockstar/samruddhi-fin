from datetime import date

import pytest

from samruddhi import create_app
from samruddhi.config import TestConfig
from samruddhi.models import Account, Category, Household, User, db
from samruddhi.seed import seed_categories


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture()
def hh(app):
    h = Household(name="Test House")
    db.session.add(h)
    db.session.flush()
    admin = User(household_id=h.id, name="Asha", username="asha", email="asha@x.in", is_admin=True)
    admin.set_password("password123")
    ravi = User(household_id=h.id, name="Ravi", username="ravi", email="ravi@x.in")
    ravi.set_password("password123")
    kid = User(household_id=h.id, name="Kiddo", is_minor=True)
    db.session.add_all([admin, ravi, kid])
    seed_categories(h.id)
    db.session.commit()
    h.admin, h.ravi, h.kid = admin, ravi, kid
    return h


def P(rupees):
    """rupees -> paise"""
    return int(round(rupees * 100))


def mk_account(hh, name, kind, start=0, **kw):
    a = Account(household_id=hh.id, name=name, kind=kind, institution=kw.pop("institution", name),
                starting_balance=P(start), **{k: (P(v) if k in ("credit_limit", "emi_principal", "emi_interest", "loan_total") else v)
                                              for k, v in kw.items()})
    db.session.add(a)
    db.session.commit()
    return a


def cat(hh, l2, kind=None):
    q = Category.query.filter_by(household_id=hh.id, name=l2)
    if kind:
        q = q.filter_by(kind=kind)
    return q.filter(Category.parent_id.isnot(None)).first()


def tx(hh, ttype, amount, account, category=None, to=None, on=None, **kw):
    from samruddhi.ledger import create_transaction
    d = dict(type=ttype, amount=P(amount), account_id=account.id, to_account_id=to.id if to else None,
             category_id=category.id if category else None, date=on or date.today(), note=kw.pop("note", ""))
    d.update(kw)
    return create_transaction(hh.id, hh.admin.id, d)
