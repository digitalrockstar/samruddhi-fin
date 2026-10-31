"""Holders, auto UPI IDs, payment-app master, debit-card grouping, minors excluded from family splits."""
import pytest

from samruddhi import ledger
from samruddhi.models import Account, AccountHolder, Transaction, UpiApp, db
from tests.conftest import P, cat, mk_account, tx
from tests.test_web import client, login, setup_admin  # noqa: F401


def _bank(hh, name="Kotak Savings", subtype="Savings", **kw):
    return mk_account(hh, name, "bank", 1000, institution="Kotak Mahindra Bank", subtype=subtype, last4="0359", **kw)


def test_debit_card_groups_under_its_bank(hh):
    b = _bank(hh)
    d = mk_account(hh, "Kotak Debit Card", "debit_card", institution="", linked_id=b.id)
    groups = ledger.account_groups(hh.id)
    assert d.id in [r["acc"].id for r in groups["Kotak Mahindra Bank"]] and "Other" not in groups


def test_holders_primary_secondary_and_minor_rule(hh):
    b = _bank(hh)
    ledger.set_holders(b, hh.admin.id, [hh.ravi.id, hh.admin.id, hh.ravi.id])
    db.session.commit()
    assert b.owner_id == hh.admin.id and b.holder_ids == [hh.admin.id, hh.ravi.id]
    with pytest.raises(ledger.LedgerError):
        ledger.set_holders(b, None, [hh.ravi.id])                       # secondary needs a primary
    with pytest.raises(ledger.LedgerError):
        ledger.set_holders(b, hh.kid.id, [])                            # minor needs an adult secondary
    ledger.set_holders(b, hh.kid.id, [hh.admin.id])
    assert b.primary_holder.id == hh.kid.id


def test_upi_auto_created_for_savings_joint_minor_only(hh):
    s, j, m = _bank(hh, "S"), _bank(hh, "J", "Joint"), _bank(hh, "M", "Minor")
    c = _bank(hh, "C", "Current")
    ledger.set_holders(m, hh.kid.id, [hh.admin.id])
    for a in (s, j, m, c):
        ledger.ensure_upi(a)
    ledger.ensure_upi(s)                                                # idempotent
    db.session.commit()
    upis = Account.query.filter_by(kind="upi").all()
    assert sorted(u.linked_id for u in upis) == sorted([s.id, j.id, m.id])
    mu = next(u for u in upis if u.linked_id == m.id)
    assert mu.upi_id and "@" in mu.upi_id and mu.holder_ids == [hh.kid.id, hh.admin.id]
    assert len({u.upi_id for u in upis}) == 3


def test_payment_app_only_kept_for_upi_accounts(hh):
    ledger.backfill_accounts()
    app = UpiApp.query.filter_by(household_id=hh.id, name="GPay").one()
    assert {a.name for a in UpiApp.query.filter_by(household_id=hh.id)} == {"Paytm", "GPay", "PhonePe", "Cred"}
    b = _bank(hh)
    u = ledger.ensure_upi(b)
    db.session.commit()
    t1 = tx(hh, "expense", 10, u, cat(hh, "Zepto"), upi_app_id=app.id)
    t2 = tx(hh, "expense", 10, b, cat(hh, "Zepto"), upi_app_id=app.id)
    assert t1.upi_app_id == app.id and t2.upi_app_id is None


def test_minor_can_be_chosen_and_family_splits_among_adults(hh):
    b = _bank(hh)
    k = tx(hh, "expense", 90, b, cat(hh, "Zepto"), scope="individuals", beneficiary_ids=[hh.kid.id])
    assert dict(ledger.shares(k)) == {hh.kid.id: P(90)}
    m = tx(hh, "expense", 90, b, cat(hh, "Zepto"), scope="individuals", beneficiary_ids=[hh.admin.id, hh.kid.id])
    assert dict(ledger.shares(m)) == {hh.admin.id: P(45), hh.kid.id: P(45)}
    f = tx(hh, "expense", 90, b, cat(hh, "Zepto"))
    assert ledger.family_shares(f, [hh.admin.id, hh.ravi.id]) == [(hh.admin.id, P(45)), (hh.ravi.id, P(45))]
    d = ledger.dashboard(hh.id, f.date.replace(day=1), f.date)
    names = {r["name"]: r["total"] for r in d["by_person"]}
    assert names["Kiddo"] == P(135) and names["Asha"] == P(45) + P(45) and names["Ravi"] == P(45)
    assert sum(names.values()) == d["expense"]


def test_joint_account_income_never_attributed_to_minor_secondary(hh):
    j = _bank(hh, "Joint", "Joint")
    ledger.set_holders(j, hh.admin.id, [hh.kid.id])
    assert ledger.acc_matches(str(hh.admin.id), j) and not ledger.acc_matches(str(hh.kid.id), j)
    assert not ledger.acc_matches("family", j) and ledger.acc_matches("family", _bank(hh, "Shared"))


def test_pages_and_forms_via_web(client):
    setup_admin(client)
    assert {a.name for a in UpiApp.query.all()} == {"Paytm", "GPay", "PhonePe", "Cred"}
    r = client.post("/accounts/new", data=dict(kind="bank", name="Kotak Savings", institution="Kotak Mahindra Bank",
                    subtype="Savings", last4="0359", starting="500", owner_id=str(User_id("asha"))), follow_redirects=True)
    assert r.status_code == 200 and Account.query.filter_by(kind="upi").count() == 1
    bank = Account.query.filter_by(kind="bank").one()
    r = client.post("/accounts/new", data=dict(kind="debit_card", name="Kotak Debit Card", last4="2477",
                    linked_id=str(bank.id)), follow_redirects=True)
    dc = Account.query.filter_by(kind="debit_card").one()
    assert dc.institution == "Kotak Mahindra Bank"
    html = client.get("/accounts").get_data(as_text=True)
    assert "OTHER" not in html.upper().split("GROUP-TITLE")[1:][0][:40] and "Kotak Savings UPI" not in html
    upi = Account.query.filter_by(kind="upi").one()
    client.post(f"/accounts/{upi.id}", data=dict(kind="upi", name=upi.name, linked_id=str(bank.id), upi_id="asha@okhdfc"))
    assert db.session.get(Account, upi.id).upi_id == "asha@okhdfc"
    assert client.post(f"/accounts/{upi.id}", data=dict(kind="upi", name=upi.name, linked_id=str(bank.id), upi_id="bad")).status_code in (200, 302)
    client.post(f"/accounts/{bank.id}", data=dict(kind="bank", name="Kotak Savings", subtype="Savings", owner_id=str(User_id("asha")), starting="500", upi_id="asha@kotak2"))
    assert db.session.get(Account, upi.id).upi_id == "asha@kotak2"
    assert "asha@kotak2" in client.get(f"/accounts/{bank.id}").get_data(as_text=True)
    for url in ("/upi-apps", "/transactions/new", "/more"):
        assert client.get(url).status_code == 200
    client.post("/upi-apps", data=dict(name="BHIM"))
    assert UpiApp.query.filter_by(name="BHIM").count() == 1


def User_id(username):
    from samruddhi.models import User
    return User.query.filter_by(username=username).one().id
