"""Business rules. Routes stay thin; everything that must stay true lives here.

Principles
- Zero-sum: balance(account) = starting balance + posted income - posted expense +/- posted transfers.
- Only POSTED transactions dated on/before the as-of date count. Pending/rejected never do.
- Credit cards / loans hold negative balances (= amount owed). Add-on cards share the parent's limit.
- Debit cards and UPI have no balance - they move the bank account they are linked to.
"""
import calendar
import csv
import io
import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import aliased

from .models import (
    ACCOUNT_KINDS, GOAL_KINDS, LINKED_KINDS, RECURRENCES, SPENDING_KINDS, TXN_TYPES,
    Account, Budget, CashbackRule, Category, Goal, GoalEntry, GoalRule, Household, Reminder,
    Transaction, TxnBeneficiary, User, db,
)

REMINDER_WINDOW_DAYS = 7
MAX_CATCHUP = 60


class LedgerError(ValueError):
    """A rule was broken; the message is safe to show to the user."""


# ----------------------------------------------------------------- money helpers
def parse_money(text, allow_zero=False) -> int:
    s = str(text or "").replace(",", "").replace("₹", "").strip()
    if not s:
        raise LedgerError("Enter an amount.")
    try:
        whole, _, frac = s.partition(".")
        if s.startswith("-") or not (whole or frac) or not (whole + frac).isdigit() or len(frac) > 2:
            raise ValueError
        paise = int(whole or "0") * 100 + int((frac + "00")[:2])
    except ValueError:
        raise LedgerError(f"“{text}” is not a valid amount (use up to 2 decimals).") from None
    if paise == 0 and not allow_zero:
        raise LedgerError("Amount must be greater than zero.")
    return paise


def inr(paise, sign=False) -> str:
    """Indian digit grouping: ₹12,34,567.89"""
    paise = int(paise or 0)
    neg = paise < 0
    rupees, p = divmod(abs(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    out = f"₹{s}.{p:02d}"
    if neg:
        return "−" + out
    return ("+" + out) if sign and paise else out


def rupees_str(paise) -> str:
    """Plain 1234.50 for form inputs."""
    return "" if paise in (None, "") else f"{int(paise) / 100:.2f}"


# ----------------------------------------------------------------- account helpers
def accounts_map(hid):
    return {a.id: a for a in Account.query.filter_by(household_id=hid).all()}


def effective(acc, accs):
    """The account whose balance actually moves."""
    if acc.kind in LINKED_KINDS:
        if not acc.linked_id or acc.linked_id not in accs:
            raise LedgerError(f"{acc.name} is not linked to a bank account.")
        return accs[acc.linked_id]
    return acc


def balances(hid, as_of=None, exclude_txn_id=None, accs=None):
    """{account_id: paise} for every account that holds its own balance."""
    as_of = as_of or date.today()
    accs = accs or accounts_map(hid)
    bal = {a.id: a.starting_balance or 0 for a in accs.values() if a.has_own_balance}
    q = Transaction.query.filter(
        Transaction.household_id == hid, Transaction.status == "posted", Transaction.date <= as_of)
    if exclude_txn_id:
        q = q.filter(Transaction.id != exclude_txn_id)
    for t in q.with_entities(Transaction.type, Transaction.amount, Transaction.account_id,
                             Transaction.to_account_id).all():
        src = effective(accs[t.account_id], accs).id
        if t.type == "income":
            bal[src] += t.amount
        elif t.type == "expense":
            bal[src] -= t.amount
        else:
            bal[src] -= t.amount
            bal[effective(accs[t.to_account_id], accs).id] += t.amount
    return bal


def card_pool(accs, bal, card):
    """(parent, owed, limit) - add-on cards draw on the parent's limit."""
    parent = accs[card.parent_id] if card.parent_id and card.parent_id in accs else card
    members = [a for a in accs.values()
               if a.kind == "credit_card" and (a.id == parent.id or a.parent_id == parent.id)]
    owed = sum(-bal.get(m.id, 0) for m in members)
    return parent, owed, parent.credit_limit or 0


def earmarked(hid, include_pending=False):
    q = db.session.query(GoalEntry.account_id, func.sum(GoalEntry.amount)).join(Goal).filter(
        Goal.household_id == hid, GoalEntry.status == "posted").group_by(GoalEntry.account_id)
    return {a: int(s or 0) for a, s in q.all()}


def _check_outflow(src, amount, bal, accs):
    if src.kind == "credit_card":
        _, owed, limit = card_pool(accs, bal, src)
        available = limit - owed
        if amount > available:
            raise LedgerError(
                f"Exceeds credit limit on {src.name}: {inr(max(available, 0))} available of {inr(limit)}.")
    elif src.kind == "loan":
        raise LedgerError("Money cannot be spent from a loan account.")
    elif bal.get(src.id, 0) < amount:
        raise LedgerError(f"Insufficient balance in {src.name}: {inr(bal.get(src.id, 0))} available.")


def validate_posting(hid, ttype, account_id, to_account_id, amount, on_date, exclude_txn_id=None,
                     check_balance=True):
    accs = accounts_map(hid)
    acc = accs.get(account_id)
    if not acc:
        raise LedgerError("Choose an account.")
    if acc.archived:
        raise LedgerError(f"{acc.name} is archived.")
    if ttype in ("income", "expense") and acc.kind not in SPENDING_KINDS:
        raise LedgerError(f"{acc.kind_label} accounts can only take part in transfers.")
    src = effective(acc, accs)
    dest = None
    if ttype == "transfer":
        dest_acc = accs.get(to_account_id)
        if not dest_acc:
            raise LedgerError("Choose the account to transfer to.")
        if dest_acc.archived:
            raise LedgerError(f"{dest_acc.name} is archived.")
        dest = effective(dest_acc, accs)
        if dest.id == src.id:
            raise LedgerError("From and To must be different accounts.")
        if acc.kind == "loan":
            raise LedgerError("A loan cannot be the source of a transfer.")
    if not check_balance:
        return
    as_of = max(on_date, date.today())
    bal = balances(hid, as_of, exclude_txn_id, accs)
    if ttype in ("expense", "transfer"):
        _check_outflow(src, amount, bal, accs)
    if dest is not None and dest.kind == "loan" and amount > -bal.get(dest.id, 0):
        raise LedgerError(f"That is more than the {inr(max(-bal.get(dest.id, 0), 0))} outstanding on {dest.name}.")


# ----------------------------------------------------------------- categories
SYSTEM_CATEGORIES = {
    "cashback": ("income", "Cashback", "Cashback"),
    "cc_payment": ("transfer", "Transfer", "CC Payment"),
    "loan_emi": ("transfer", "Transfer", "Loan EMI"),
    "loan_interest": ("expense", "Finance charges", "Loan interest"),
}


def sys_category(hid, key) -> Category:
    cat = Category.query.filter_by(household_id=hid, system_key=key).first()
    if cat:
        return cat
    kind, l1_name, l2_name = SYSTEM_CATEGORIES[key]
    l1 = Category.query.filter_by(household_id=hid, name=l1_name, kind=kind, parent_id=None).first()
    if not l1:
        l1 = Category(household_id=hid, name=l1_name, kind=kind)
        db.session.add(l1)
        db.session.flush()
    cat = Category(household_id=hid, name=l2_name, kind=kind, parent_id=l1.id, system_key=key)
    db.session.add(cat)
    db.session.flush()
    return cat


def category_ids_with_children(cat: Category):
    return {cat.id} | {c.id for c in cat.children}


# ----------------------------------------------------------------- transactions
def _check_fields(hid, d, skip_category_window=False):
    ttype = d.get("type")
    if ttype not in TXN_TYPES:
        raise LedgerError("Choose Income, Expense or Transfer.")
    amount = d.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise LedgerError("Amount must be greater than zero.")
    if not isinstance(d.get("date"), date):
        raise LedgerError("Choose a date.")
    cat = None
    if d.get("category_id"):
        cat = db.session.get(Category, d["category_id"])
        if not cat or cat.household_id != hid:
            raise LedgerError("Unknown category.")
        if cat.kind != ttype:
            raise LedgerError(f"“{cat.path}” is not a {ttype} category.")
        if not skip_category_window and not cat.active_on(d["date"]):
            raise LedgerError(f"“{cat.path}” is not active on {d['date']:%d %b %Y}.")
    elif ttype != "transfer" and not skip_category_window:
        raise LedgerError("Choose a category.")
    scope = d.get("scope") or "family"
    ben_ids = sorted(set(d.get("beneficiary_ids") or []))
    custom = {int(k): int(v) for k, v in (d.get("beneficiary_shares") or {}).items() if int(k) in ben_ids}
    if ttype == "expense" and scope == "individuals":
        if not ben_ids:
            raise LedgerError("Pick at least one person, or mark the expense as Family.")
        found = User.query.filter(User.household_id == hid, User.id.in_(ben_ids)).count()
        if found != len(ben_ids):
            raise LedgerError("Unknown beneficiary.")
        if custom:
            if set(custom) != set(ben_ids) or any(v <= 0 for v in custom.values()):
                raise LedgerError("Enter an amount for every selected person.")
            if sum(custom.values()) != amount:
                raise LedgerError(f"Shares add up to {inr(sum(custom.values()))} but the expense is {inr(amount)}.")
    else:
        scope, ben_ids, custom = "family", [], {}
    d["_custom"] = custom
    rec = d.get("recurrence") or "none"
    if rec not in RECURRENCES:
        raise LedgerError("Unknown repeat option.")
    goal = None
    if d.get("goal_id"):
        if ttype == "expense":
            raise LedgerError("Only income or transfers can fund a goal.")
        goal = db.session.get(Goal, d["goal_id"])
        if not goal or goal.household_id != hid or goal.archived:
            raise LedgerError("Unknown goal.")
    return cat, scope, ben_ids, rec, goal


def _goal_target_account(d, accs):
    acc = accs[d["to_account_id"]] if d["type"] == "transfer" else accs[d["account_id"]]
    return effective(acc, accs)


def create_transaction(hid, user_id, d, validate=True, commit=True) -> Transaction:
    """d keys: type,date,amount,account_id,to_account_id,category_id,note,scope,beneficiary_ids,
    goal_id,recurrence,cashback{mode,value,days,account_id},status,source,template_id,group_id,..."""
    status = d.get("status", "posted")
    auto = d.get("source", "manual") != "manual"
    cat, scope, ben_ids, rec, goal = _check_fields(hid, d, skip_category_window=auto)
    if validate:
        validate_posting(hid, d["type"], d["account_id"], d.get("to_account_id"), d["amount"], d["date"],
                         check_balance=(status == "posted"))
    accs = accounts_map(hid)
    if goal:
        tgt = _goal_target_account(d, accs)
        if tgt.kind not in GOAL_KINDS:
            raise LedgerError("Goals can only sit in bank or investment accounts - never a card or wallet.")
    t = Transaction(
        household_id=hid, type=d["type"], date=d["date"], amount=d["amount"], account_id=d["account_id"],
        to_account_id=d.get("to_account_id") if d["type"] == "transfer" else None,
        category_id=cat.id if cat else None, note=(d.get("note") or "").strip()[:500],
        status=status, source=d.get("source", "manual"), scope=scope, created_by=user_id,
        recurrence=rec, template_id=d.get("template_id"), rule_id=d.get("rule_id"),
        source_txn_id=d.get("source_txn_id"), group_id=d.get("group_id"))
    if rec != "none":
        t.next_run = next_occurrence(t.date, rec, t.date.day)
    for uid in ben_ids:
        t.beneficiaries.append(TxnBeneficiary(user_id=uid, share=d["_custom"].get(uid)))
    db.session.add(t)
    db.session.flush()
    if goal:
        db.session.add(GoalEntry(goal_id=goal.id, account_id=tgt.id, amount=t.amount, date=t.date,
                                 status=status, source="txn", txn_id=t.id, created_by=user_id))
    if status == "posted":
        on_posted(t, d.get("cashback"))
        reconcile_goals(t)
    if commit:
        db.session.commit()
    return t


def update_transaction(t: Transaction, d) -> Transaction:
    hid = t.household_id
    d = dict(d, type=t.type)
    cat, scope, ben_ids, rec, goal = _check_fields(hid, d, skip_category_window=t.source != "manual")
    validate_posting(hid, t.type, d["account_id"], d.get("to_account_id"), d["amount"], d["date"],
                     exclude_txn_id=t.id, check_balance=(t.status == "posted"))
    accs = accounts_map(hid)
    if goal and _goal_target_account(d, accs).kind not in GOAL_KINDS:
        raise LedgerError("Goals can only sit in bank or investment accounts - never a card or wallet.")
    t.date, t.amount, t.account_id = d["date"], d["amount"], d["account_id"]
    t.to_account_id = d.get("to_account_id") if t.type == "transfer" else None
    t.category_id = cat.id if cat else None
    t.note = (d.get("note") or "").strip()[:500]
    t.scope = scope
    t.beneficiaries.clear()
    db.session.flush()
    for uid in ben_ids:
        t.beneficiaries.append(TxnBeneficiary(user_id=uid, share=d["_custom"].get(uid)))
    if t.template_id is None:
        if rec == "none":
            t.recurrence, t.next_run = "none", None
        elif rec != t.recurrence:
            t.recurrence = rec
            t.next_run = next_occurrence(t.date, rec, t.date.day)
    GoalEntry.query.filter_by(txn_id=t.id).delete()
    if goal:
        db.session.add(GoalEntry(goal_id=goal.id, account_id=_goal_target_account(d, accs).id, amount=t.amount,
                                 date=t.date, status=t.status, source="txn", txn_id=t.id, created_by=t.created_by))
    db.session.flush()
    if t.status == "posted":
        reconcile_goals(t)
    db.session.commit()
    return t


def delete_transaction(t: Transaction):
    """Removes the transaction. Pending children go with it; posted children are kept as plain transactions."""
    kids = Transaction.query.filter(or_(Transaction.template_id == t.id, Transaction.source_txn_id == t.id)).all()
    for k in kids:
        if k.status == "pending":
            GoalEntry.query.filter_by(txn_id=k.id).delete()
            db.session.delete(k)
        else:
            if k.template_id == t.id:
                k.template_id = None
            if k.source_txn_id == t.id:
                k.source_txn_id = None
    GoalEntry.query.filter_by(txn_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()


def approve_transaction(t: Transaction, user_id):
    if t.status != "pending":
        raise LedgerError("This item is not waiting for approval.")
    validate_posting(t.household_id, t.type, t.account_id, t.to_account_id, t.amount, t.date)
    t.status, t.approved_by, t.approved_at = "posted", user_id, func.now()
    GoalEntry.query.filter_by(txn_id=t.id, status="pending").update({"status": "posted"})
    db.session.flush()
    if t.source != "cashback":
        on_posted(t, None)
    reconcile_goals(t)
    db.session.commit()


def reject_transaction(t: Transaction, user_id):
    if t.status != "pending":
        raise LedgerError("This item is not waiting for approval.")
    t.status, t.approved_by = "rejected", user_id
    GoalEntry.query.filter_by(txn_id=t.id).update({"status": "rejected"})
    db.session.commit()


# ----------------------------------------------------------------- cashback
def _cashback_amount(mode, basis_points, fixed, min_spend, cap, spend):
    if min_spend and spend < min_spend:
        return 0
    amt = (spend * basis_points) // 10000 if mode == "percent" else fixed
    return min(amt, cap) if cap else amt


def _make_cashback(src: Transaction, amount, days, payout_id, label, rule_id=None):
    if amount <= 0:
        return None
    exists = Transaction.query.filter_by(source_txn_id=src.id, source="cashback", rule_id=rule_id).first()
    if exists and rule_id is not None:
        return None
    note = f"Cashback: {label}" + (f" · {src.note}" if src.note else "")
    t = Transaction(
        household_id=src.household_id, type="income", date=src.date + timedelta(days=days or 0),
        amount=amount, account_id=payout_id or src.account_id, category_id=sys_category(src.household_id, "cashback").id,
        note=note[:500], status="pending", source="cashback", scope="family", created_by=src.created_by,
        rule_id=rule_id, source_txn_id=src.id)
    db.session.add(t)
    return t


def on_posted(t: Transaction, adhoc=None):
    """Hook run once a transaction becomes posted (cashback generation)."""
    if t.type != "expense" or t.source == "cashback":
        return
    cat = t.category
    cat_ids = {cat.id, cat.parent_id} if cat else set()
    rules = CashbackRule.query.filter_by(household_id=t.household_id, account_id=t.account_id, active=True).all()
    for r in rules:
        if r.category_id and r.category_id not in cat_ids:
            continue
        if r.keyword and r.keyword.lower() not in (t.note or "").lower():
            continue
        if r.one_time and r.uses >= 1:
            continue
        amt = _cashback_amount(r.mode, r.basis_points, r.fixed_amount, r.min_spend, r.max_cashback, t.amount)
        made = _make_cashback(t, amt, r.delay_days, r.payout_account_id, r.name, rule_id=r.id)
        if made:
            r.uses += 1
    if adhoc:
        mode = adhoc.get("mode", "percent")
        amt = _cashback_amount(mode, adhoc.get("value", 0) if mode == "percent" else 0,
                               adhoc.get("value", 0) if mode == "fixed" else 0, 0, 0, t.amount)
        _make_cashback(t, amt, adhoc.get("days", 0), adhoc.get("account_id"), "one-off promotion")


# ----------------------------------------------------------------- recurrence
def add_months(d: date, n: int, anchor_day: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    m += 1
    return date(y, m, min(anchor_day, calendar.monthrange(y, m)[1]))


def next_occurrence(d: date, rec: str, anchor_day: int) -> date:
    if rec == "weekly":
        return d + timedelta(days=7)
    if rec == "monthly":
        return add_months(d, 1, anchor_day)
    if rec == "yearly":
        return add_months(d, 12, anchor_day)
    raise LedgerError("Not a repeating transaction.")


def run_due(hid, today=None) -> int:
    """Create pending items for everything that has come due. Safe to call repeatedly."""
    today = today or date.today()
    made = 0
    templates = Transaction.query.filter(
        Transaction.household_id == hid, Transaction.recurrence != "none", Transaction.template_id.is_(None),
        Transaction.rec_active.is_(True), Transaction.status == "posted", Transaction.next_run <= today).all()
    for t in templates:
        n = 0
        while t.next_run <= today and n < MAX_CATCHUP:
            run = t.next_run
            if not Transaction.query.filter_by(template_id=t.id, date=run).first():
                child = Transaction(
                    household_id=hid, type=t.type, date=run, amount=t.amount, account_id=t.account_id,
                    to_account_id=t.to_account_id, category_id=t.category_id, note=t.note, status="pending",
                    source="subscription", scope=t.scope, created_by=t.created_by, template_id=t.id)
                for b in t.beneficiaries:
                    child.beneficiaries.append(TxnBeneficiary(user_id=b.user_id, share=b.share))
                db.session.add(child)
                db.session.flush()
                for ge in GoalEntry.query.filter_by(txn_id=t.id).all():
                    db.session.add(GoalEntry(goal_id=ge.goal_id, account_id=ge.account_id, amount=child.amount,
                                             date=run, status="pending", source="txn", txn_id=child.id,
                                             created_by=t.created_by))
                made += 1
            t.next_run = next_occurrence(run, t.recurrence, t.date.day)
            n += 1
    rules = GoalRule.query.join(Goal).filter(Goal.household_id == hid, GoalRule.active.is_(True),
                                             GoalRule.next_run <= today).all()
    for r in rules:
        n = 0
        while r.next_run <= today and n < MAX_CATCHUP:
            if not GoalEntry.query.filter_by(rule_id=r.id, date=r.next_run).first():
                db.session.add(GoalEntry(goal_id=r.goal_id, account_id=r.account_id, amount=r.amount,
                                         date=r.next_run, status="pending", source="rule", rule_id=r.id))
                made += 1
            r.next_run = next_occurrence(r.next_run, r.recurrence, r.start_date.day)
            n += 1
    made += ensure_reminders(hid, today)
    db.session.commit()
    return made


def set_subscription_active(t: Transaction, active: bool, today=None):
    today = today or date.today()
    t.rec_active = active
    if active:  # resume from the next future occurrence - no back-filling of the paused gap
        while t.next_run <= today:
            t.next_run = next_occurrence(t.next_run, t.recurrence, t.date.day)
    db.session.commit()


# ----------------------------------------------------------------- reminders
def next_due_date(due_day: int, today: date) -> date:
    d = date(today.year, today.month, min(due_day, calendar.monthrange(today.year, today.month)[1]))
    return d if d >= today else add_months(d, 1, due_day)


def ensure_reminders(hid, today=None) -> int:
    today = today or date.today()
    accs = accounts_map(hid)
    bal = balances(hid, today, accs=accs)
    made = 0
    for a in accs.values():
        if a.archived or not a.due_day:
            continue
        if a.kind == "credit_card" and not a.parent_id:
            _, owed, _ = card_pool(accs, bal, a)
            amount = owed
        elif a.kind == "loan":
            outstanding = -bal.get(a.id, 0)
            amount = (min(a.emi_principal or 0, outstanding) + (a.emi_interest or 0)) if outstanding > 0 else 0
        else:
            continue
        if amount <= 0:
            continue
        due = next_due_date(a.due_day, today)
        if (due - today).days > REMINDER_WINDOW_DAYS:
            continue
        if not Reminder.query.filter_by(account_id=a.id, due_date=due).first():
            db.session.add(Reminder(household_id=hid, account_id=a.id, due_date=due, amount=amount))
            made += 1
    return made


def approve_reminder(rem: Reminder, user_id, pay_from_id, amount=None, on_date=None):
    if rem.status != "pending":
        raise LedgerError("This reminder has already been handled.")
    hid = rem.household_id
    on_date = on_date or date.today()
    accs = accounts_map(hid)
    acc = rem.account
    pay_from = accs.get(pay_from_id)
    if not pay_from:
        raise LedgerError("Choose the account to pay from.")
    amount = amount or rem.amount
    if acc.kind == "credit_card":
        create_transaction(hid, user_id, dict(
            type="transfer", date=on_date, amount=amount, account_id=pay_from_id, to_account_id=acc.id,
            category_id=sys_category(hid, "cc_payment").id, note=f"Card payment · {acc.display}",
            source="reminder"), commit=False)
    elif acc.kind == "loan":
        bal = balances(hid, on_date, accs=accs)
        outstanding = -bal.get(acc.id, 0)
        interest = min(acc.emi_interest or 0, amount - 1) if amount > 1 else 0
        principal = min(amount - interest, outstanding)
        if principal <= 0:
            raise LedgerError("Nothing is outstanding on this loan.")
        interest = amount - principal
        validate_posting(hid, "expense", pay_from_id, None, amount, on_date)    # total must be affordable
        gid = uuid.uuid4().hex
        create_transaction(hid, user_id, dict(
            type="transfer", date=on_date, amount=principal, account_id=pay_from_id, to_account_id=acc.id,
            category_id=sys_category(hid, "loan_emi").id, note=f"EMI principal · {acc.display}",
            source="reminder", group_id=gid), validate=False, commit=False)
        if interest > 0:
            create_transaction(hid, user_id, dict(
                type="expense", date=on_date, amount=interest, account_id=pay_from_id,
                category_id=sys_category(hid, "loan_interest").id, note=f"EMI interest · {acc.display}",
                source="reminder", group_id=gid), validate=False, commit=False)
    else:
        raise LedgerError("Only cards and loans have payment reminders.")
    rem.status = "done"
    db.session.commit()


# ----------------------------------------------------------------- goals
def goal_summary(g: Goal, today=None, accs=None):
    today = today or date.today()
    saved = sum(e.amount for e in g.entries if e.status == "posted")
    pending = sum(e.amount for e in g.entries if e.status == "pending")
    by_acc = defaultdict(int)
    for e in g.entries:
        if e.status == "posted":
            by_acc[e.account_id] += e.amount
    pct = min(100, round(saved * 100 / g.target)) if g.target else None
    days_left = (g.deadline - today).days if g.deadline else None
    monthly_needed = None
    if g.deadline and g.target and saved < g.target and days_left and days_left > 0:
        monthly_needed = int((g.target - saved) / max(days_left / 30.4, 1))
    return dict(saved=saved, pending=pending, by_account=dict(by_acc), pct=pct, days_left=days_left,
                monthly_needed=monthly_needed, remaining=max((g.target or 0) - saved, 0))


def add_goal_entry(hid, user_id, goal_id, account_id, amount, on_date=None, status="posted"):
    goal = db.session.get(Goal, goal_id)
    accs = accounts_map(hid)
    acc = accs.get(account_id)
    if not goal or goal.household_id != hid or goal.archived:
        raise LedgerError("Unknown goal.")
    if not acc or acc.archived:
        raise LedgerError("Choose an account.")
    if effective(acc, accs).kind not in GOAL_KINDS:
        raise LedgerError("Goals can only sit in bank or investment accounts - never a card or wallet.")
    if amount <= 0:
        raise LedgerError("Amount must be greater than zero.")
    e = GoalEntry(goal_id=goal.id, account_id=effective(acc, accs).id, amount=amount, date=on_date or date.today(),
                  status=status, source="manual", created_by=user_id)
    db.session.add(e)
    db.session.commit()
    return e


def add_goal_rule(hid, goal_id, account_id, amount, recurrence, start):
    goal = db.session.get(Goal, goal_id)
    accs = accounts_map(hid)
    acc = accs.get(account_id)
    if not goal or goal.household_id != hid:
        raise LedgerError("Unknown goal.")
    if not acc or effective(acc, accs).kind not in GOAL_KINDS:
        raise LedgerError("Goals can only sit in bank or investment accounts - never a card or wallet.")
    if recurrence not in ("weekly", "monthly", "yearly"):
        raise LedgerError("Choose how often.")
    r = GoalRule(goal_id=goal.id, account_id=effective(acc, accs).id, amount=amount, recurrence=recurrence,
                 start_date=start, next_run=start)
    db.session.add(r)
    db.session.commit()
    return r


def _warn(msg):
    db.session.info.setdefault("warnings", []).append(msg)


def reconcile_goals(t: Transaction):
    """A withdrawal may leave an account holding less than is earmarked for goals. Never block it:
    flag it and shrink the goals with negative entries (same format as contributions)."""
    if t.type == "income" or t.status != "posted" or t.date > date.today():
        return
    accs = accounts_map(t.household_id)
    src = effective(accs[t.account_id], accs)
    if src.kind not in GOAL_KINDS:
        return
    held = max(balances(t.household_id, accs=accs).get(src.id, 0), 0)
    short = earmarked(t.household_id).get(src.id, 0) - held
    if short <= 0:
        return
    rows = (db.session.query(GoalEntry.goal_id, func.sum(GoalEntry.amount))
            .join(Goal).filter(Goal.household_id == t.household_id, GoalEntry.account_id == src.id,
                               GoalEntry.status == "posted").group_by(GoalEntry.goal_id).all())
    reduced = []
    for goal_id, saved in sorted(rows, key=lambda r: -(r[1] or 0)):
        take = min(int(saved or 0), short)
        if take <= 0:
            continue
        goal = db.session.get(Goal, goal_id)
        db.session.add(GoalEntry(goal_id=goal_id, account_id=src.id, amount=-take, date=t.date, status="posted",
                                 source="auto", txn_id=None, created_by=t.created_by,
                                 note=f"Reduced: {inr(t.amount)} withdrawn from {src.name}"))
        reduced.append(f"{goal.name} −{inr(take)}")
        short -= take
        if short <= 0:
            break
    if reduced:
        _warn(f"{src.name} dropped below the amount earmarked for goals. Goals were reduced: " + ", ".join(reduced) + ".")


def goal_warning(hid, account_id, extra=0):
    """Soft warning when earmarks exceed what the account actually holds."""
    accs = accounts_map(hid)
    bal = balances(hid, accs=accs)
    acc = accs.get(account_id)
    if not acc:
        return None
    held = bal.get(acc.id, 0)
    marked = earmarked(hid).get(acc.id, 0) + extra
    if marked > held:
        return f"{acc.name} holds {inr(held)} but {inr(marked)} is earmarked for goals."
    return None


# ----------------------------------------------------------------- budgets
def period_range(period, today):
    if period == "weekly":
        s = today - timedelta(days=today.weekday())
        return s, s + timedelta(days=6)
    if period == "yearly":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    return date(today.year, today.month, 1), date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])


def shares(t):
    """[(user_id | None for family, paise)] - an expense split equally between its beneficiaries."""
    bens = sorted(t.beneficiaries, key=lambda b: b.user_id)
    ids = [b.user_id for b in bens]
    if t.scope != "individuals" or not ids:
        return [(None, t.amount)]
    if all(b.share is not None for b in bens) and sum(b.share for b in bens) == t.amount:
        return [(b.user_id, b.share) for b in bens]
    base, rem = divmod(t.amount, len(ids))
    return [(uid, base + (rem if i == 0 else 0)) for i, uid in enumerate(ids)]


def budget_status(b: Budget, today=None):
    today = today or date.today()
    start, end = period_range(b.period, today)
    ids = category_ids_with_children(b.category)
    spent = 0
    q = Transaction.query.filter(
        Transaction.household_id == b.household_id, Transaction.status == "posted", Transaction.type == "expense",
        Transaction.category_id.in_(ids), Transaction.date >= start, Transaction.date <= min(end, today))
    for t in q.all():
        for uid, amt in shares(t):
            if uid == b.owner_id:          # None == family budget <-> family-scope expenses
                spent += amt
    pct = round(spent * 100 / b.amount) if b.amount else 0
    state = "over" if spent > b.amount else ("warn" if pct >= 80 else "ok")
    return dict(spent=spent, remaining=b.amount - spent, pct=pct, state=state, start=start, end=end)


# ----------------------------------------------------------------- activity
def query_transactions(hid, f):
    """f: dict with start,end,account_id,kind,category_id,type,min_amt,max_amt,q,status"""
    A1, A2 = aliased(Account), aliased(Account)
    q = (Transaction.query.filter(Transaction.household_id == hid)
         .join(A1, Transaction.account_id == A1.id)
         .outerjoin(A2, Transaction.to_account_id == A2.id))
    if f.get("start"):
        q = q.filter(Transaction.date >= f["start"])
    if f.get("end"):
        q = q.filter(Transaction.date <= f["end"])
    if f.get("account_id"):
        q = q.filter(or_(Transaction.account_id == f["account_id"], Transaction.to_account_id == f["account_id"]))
    if f.get("kind"):
        q = q.filter(or_(A1.kind == f["kind"], A2.kind == f["kind"]))
    if f.get("category_id"):
        cat = db.session.get(Category, f["category_id"])
        if cat and cat.household_id == hid:
            q = q.filter(Transaction.category_id.in_(category_ids_with_children(cat)))
    if f.get("type") in TXN_TYPES:
        q = q.filter(Transaction.type == f["type"])
    if f.get("min_amt") is not None:
        q = q.filter(Transaction.amount >= f["min_amt"])
    if f.get("max_amt") is not None:
        q = q.filter(Transaction.amount <= f["max_amt"])
    if f.get("q"):
        term = f["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(Transaction.note.ilike(f"%{term}%", escape="\\"))
    if f.get("status") == "pending":
        q = q.filter(Transaction.status == "pending")
    elif f.get("status") == "all":
        q = q.filter(Transaction.status.in_(["posted", "pending"]))
    else:
        q = q.filter(Transaction.status == "posted")
    return q


def activity_summary(q, today=None):
    today = today or date.today()
    s = dict(income=0, expense=0, transfer=0, count=0)
    for t in q.filter(Transaction.status == "posted").with_entities(
            Transaction.type, Transaction.amount, Transaction.date).all():
        if t.date > today:
            continue
        s[t.type] += t.amount
        s["count"] += 1
    s["net"] = s["income"] - s["expense"]
    return s


TXN_CSV_COLUMNS = ["id", "date", "type", "status", "source", "amount", "account", "to_account", "category",
                   "subcategory", "note", "scope", "beneficiaries", "recurrence", "next_run", "created_by"]


def transactions_csv(txns) -> str:
    users = {u.id: u.name for u in User.query.all()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TXN_CSV_COLUMNS)
    for t in txns:
        cat = t.category
        w.writerow([
            t.id, t.date.isoformat(), t.type, t.status, t.source, f"{t.amount / 100:.2f}",
            t.account.display if t.account else "", t.to_account.display if t.to_account else "",
            (cat.parent.name if cat and cat.parent else (cat.name if cat else "")),
            (cat.name if cat and cat.parent else ""), t.note or "", t.scope,
            "; ".join(users.get(b.user_id, "?") for b in t.beneficiaries),
            t.recurrence, t.next_run.isoformat() if t.next_run else "", users.get(t.created_by, "")])
    return buf.getvalue()


# ----------------------------------------------------------------- dashboard
def person_matches(person, uid):
    if person in (None, "all"):
        return True
    if person == "family":
        return uid is None
    return uid == int(person)


def dashboard(hid, start, end, person="all", kind="all", today=None):
    today = today or date.today()
    end_eff = min(end, today)
    accs = accounts_map(hid)
    cats = {c.id: c for c in Category.query.filter_by(household_id=hid).all()}
    users = {u.id: u for u in User.query.filter_by(household_id=hid).all()}

    def flows(s, e):
        out = []
        q = Transaction.query.filter(
            Transaction.household_id == hid, Transaction.status == "posted", Transaction.type != "transfer",
            Transaction.date >= s, Transaction.date <= e)
        for t in q.all():
            acc = accs[t.account_id]
            if kind not in (None, "all") and acc.kind != kind:
                continue
            if t.type == "income":
                if person_matches(person, acc.owner_id):
                    out.append((t, acc, acc.owner_id, t.amount))
            else:
                for uid, amt in shares(t):
                    if person_matches(person, uid):
                        out.append((t, acc, uid, amt))
        return out

    income = expense = 0
    by_cat, by_kind, by_method, by_person = {}, defaultdict(int), defaultdict(int), defaultdict(int)
    for t, acc, uid, amt in flows(start, end_eff):
        if t.type == "income":
            income += amt
            continue
        expense += amt
        cat = cats.get(t.category_id)
        top = cat.parent if cat and cat.parent else cat
        top_name = top.name if top else "Uncategorised"
        node = by_cat.setdefault(top_name, {"total": 0, "subs": defaultdict(int)})
        node["total"] += amt
        node["subs"][cat.name if cat and cat.parent else "General"] += amt
        by_kind[acc.kind_label] += amt
        by_method[acc.display] += amt
        by_person["Family" if uid is None else users[uid].name] += amt

    # six-month trend under the same filters
    first = add_months(date(end_eff.year, end_eff.month, 1), -5, 1)
    months = [add_months(first, i, 1) for i in range(6)]
    trend = {m: [0, 0] for m in months}
    for t, acc, uid, amt in flows(first, end_eff):
        key = date(t.date.year, t.date.month, 1)
        if key in trend:
            trend[key][0 if t.type == "income" else 1] += amt

    # balances (accounts filtered by owner/kind the same way)
    bal = balances(hid, today, accs=accs)

    def acc_ok(a):
        if kind not in (None, "all") and a.kind != kind:
            return False
        return person_matches(person, a.owner_id)

    liquid = invested = invested_value = loans = 0
    cards = card_count = addon_count = 0
    owed_total = limit_total = 0
    rows = defaultdict(list)
    seen_pools = set()
    for a in sorted(accs.values(), key=lambda x: (x.institution.lower(), x.name.lower())):
        if not a.has_own_balance or not acc_ok(a):
            continue
        b = bal.get(a.id, 0)
        if a.archived and b == 0:
            continue
        row = dict(id=a.id, name=a.display, kind=a.kind, kind_label=a.kind_label, balance=b, archived=a.archived)
        if a.kind in ("bank", "wallet", "cash"):
            liquid += b
        elif a.kind == "investment":
            invested += b
            invested_value += a.current_value if a.current_value is not None else b
        elif a.kind == "loan":
            loans += -b
        elif a.kind == "credit_card":
            if not a.archived:
                card_count += 1
                addon_count += 1 if a.parent_id else 0
            parent, owed, limit = card_pool(accs, bal, a)
            row["owed"] = -b
            if parent.id not in seen_pools:
                seen_pools.add(parent.id)
                owed_total += owed
                limit_total += limit
        rows[a.institution or "Other"].append(row)
    utilisation = round(owed_total * 100 / limit_total, 1) if limit_total else None
    pending = Transaction.query.filter_by(household_id=hid, status="pending").count()
    top_cats = sorted(by_cat.items(), key=lambda kv: -kv[1]["total"])
    return dict(
        period=dict(start=start.isoformat(), end=end_eff.isoformat()),
        income=income, expense=expense, net=income - expense,
        total_balance=liquid, invested=invested, invested_value=invested_value, loans=loans,
        card_owed=owed_total, card_limit=limit_total, utilisation=utilisation,
        cards=card_count, addon_cards=addon_count,
        net_worth=liquid + invested_value - owed_total - loans, pending=pending,
        by_category=[dict(name=k, total=v["total"], subs=sorted(
            [dict(name=sk, total=sv) for sk, sv in v["subs"].items()], key=lambda s: -s["total"]))
            for k, v in top_cats],
        by_account_type=sorted([dict(name=k, total=v) for k, v in by_kind.items()], key=lambda x: -x["total"]),
        by_method=sorted([dict(name=k, total=v) for k, v in by_method.items()], key=lambda x: -x["total"]),
        by_person=sorted([dict(name=k, total=v) for k, v in by_person.items()], key=lambda x: -x["total"]),
        balances=[dict(institution=k, accounts=v) for k, v in rows.items()],
        trend=[dict(month=m.strftime("%b"), income=trend[m][0], expense=trend[m][1]) for m in months],
    )


# ----------------------------------------------------------------- accounts page
def account_groups(hid, include_archived=False, today=None):
    today = today or date.today()
    accs = accounts_map(hid)
    bal = balances(hid, today, accs=accs)
    marks = earmarked(hid)
    groups = defaultdict(list)
    for a in sorted(accs.values(), key=lambda x: (x.institution.lower(), x.name.lower())):
        if a.archived and not include_archived:
            continue
        item = dict(acc=a, balance=bal.get(a.id), earmarked=marks.get(a.id, 0))
        if a.kind == "credit_card":
            parent, owed, limit = card_pool(accs, bal, a)
            item.update(owed=-bal.get(a.id, 0), pool_owed=owed, pool_limit=limit, pool_available=limit - owed,
                        pool_parent=parent, util=round(owed * 100 / limit) if limit else None)
        if a.kind == "loan":
            item.update(outstanding=-bal.get(a.id, 0), paid=max((a.loan_total or 0) + bal.get(a.id, 0), 0))
        if a.kind in LINKED_KINDS and a.linked:
            item["linked_name"] = a.linked.display
        groups[a.institution or "Other"].append(item)
    return dict(groups)


# ----------------------------------------------------------------- backup + resets
def _household_rows(hid):
    tx_ids = db.session.query(Transaction.id).filter_by(household_id=hid)
    goal_ids = db.session.query(Goal.id).filter_by(household_id=hid)
    return [
        ("households", Household.query.filter_by(id=hid)),
        ("users", User.query.filter_by(household_id=hid)),
        ("accounts", Account.query.filter_by(household_id=hid)),
        ("categories", Category.query.filter_by(household_id=hid)),
        ("transactions", Transaction.query.filter_by(household_id=hid)),
        ("txn_beneficiaries", TxnBeneficiary.query.filter(TxnBeneficiary.txn_id.in_(tx_ids))),
        ("cashback_rules", CashbackRule.query.filter_by(household_id=hid)),
        ("budgets", Budget.query.filter_by(household_id=hid)),
        ("goals", Goal.query.filter_by(household_id=hid)),
        ("goal_entries", GoalEntry.query.filter(GoalEntry.goal_id.in_(goal_ids))),
        ("goal_rules", GoalRule.query.filter(GoalRule.goal_id.in_(goal_ids))),
        ("reminders", Reminder.query.filter_by(household_id=hid)),
    ]


def backup_csvs(hid) -> dict:
    """One CSV per table. Password hashes are never exported."""
    out = {}
    for name, q in _household_rows(hid):
        cols = [c.name for c in q.column_descriptions[0]["entity"].__table__.columns if c.name != "password_hash"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for row in q.all():
            w.writerow([getattr(row, c) for c in cols])
        out[f"{name}.csv"] = buf.getvalue()
    return out


def _wipe_transactions(hid, tx_query):
    ids = [i for (i,) in tx_query.with_entities(Transaction.id).all()]
    if not ids:
        return 0
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        Transaction.query.filter(Transaction.template_id.in_(chunk)).update({"template_id": None}, synchronize_session=False)
        Transaction.query.filter(Transaction.source_txn_id.in_(chunk)).update({"source_txn_id": None}, synchronize_session=False)
        GoalEntry.query.filter(GoalEntry.txn_id.in_(chunk)).delete(synchronize_session=False)
        TxnBeneficiary.query.filter(TxnBeneficiary.txn_id.in_(chunk)).delete(synchronize_session=False)
        Transaction.query.filter(Transaction.id.in_(chunk)).delete(synchronize_session=False)
    return len(ids)


def reset_full(hid):
    """4A - delete the household and everything in it."""
    _wipe_transactions(hid, Transaction.query.filter_by(household_id=hid))
    goal_ids = [i for (i,) in db.session.query(Goal.id).filter_by(household_id=hid).all()]
    if goal_ids:
        GoalEntry.query.filter(GoalEntry.goal_id.in_(goal_ids)).delete(synchronize_session=False)
        GoalRule.query.filter(GoalRule.goal_id.in_(goal_ids)).delete(synchronize_session=False)
    for model in (Reminder, CashbackRule, Budget, Goal):
        model.query.filter_by(household_id=hid).delete(synchronize_session=False)
    Account.query.filter_by(household_id=hid).update({"parent_id": None, "linked_id": None, "pay_from_id": None},
                                                     synchronize_session=False)
    Category.query.filter_by(household_id=hid).update({"parent_id": None}, synchronize_session=False)
    for model in (Account, Category, User):
        model.query.filter_by(household_id=hid).delete(synchronize_session=False)
    Household.query.filter_by(id=hid).delete(synchronize_session=False)
    db.session.commit()


def reset_transactions(hid, zero_starting_balances=True):
    """4B - drop every transaction and amount, keep all masters."""
    n = _wipe_transactions(hid, Transaction.query.filter_by(household_id=hid))
    goal_ids = [i for (i,) in db.session.query(Goal.id).filter_by(household_id=hid).all()]
    if goal_ids:
        GoalEntry.query.filter(GoalEntry.goal_id.in_(goal_ids)).delete(synchronize_session=False)
    Reminder.query.filter_by(household_id=hid).delete(synchronize_session=False)
    CashbackRule.query.filter_by(household_id=hid).update({"uses": 0})
    if zero_starting_balances:
        Account.query.filter_by(household_id=hid).update({"starting_balance": 0})
    db.session.commit()
    return n


def reset_after(hid, cutoff: date, today=None):
    """4C - drop transactions dated on/after the cutoff; keep masters and starting balances."""
    today = today or date.today()
    n = _wipe_transactions(hid, Transaction.query.filter(Transaction.household_id == hid, Transaction.date >= cutoff))
    goal_ids = [i for (i,) in db.session.query(Goal.id).filter_by(household_id=hid).all()]
    if goal_ids:
        GoalEntry.query.filter(GoalEntry.goal_id.in_(goal_ids), GoalEntry.date >= cutoff).delete(synchronize_session=False)
    Reminder.query.filter(Reminder.household_id == hid, Reminder.due_date >= cutoff).delete(synchronize_session=False)
    # schedules must not back-fill what was just wiped: resume from the next future date
    for t in Transaction.query.filter(Transaction.household_id == hid, Transaction.recurrence != "none",
                                      Transaction.template_id.is_(None)).all():
        while t.next_run and t.next_run <= today:
            t.next_run = next_occurrence(t.next_run, t.recurrence, t.date.day)
    for r in GoalRule.query.filter(GoalRule.goal_id.in_(goal_ids or [0])).all():
        while r.next_run <= today:
            r.next_run = next_occurrence(r.next_run, r.recurrence, r.start_date.day)
    db.session.commit()
    return n
