from collections import OrderedDict
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import ledger
from .permissions import can, require
from .auth import login_required
from .ledger import LedgerError, parse_money
from .models import (ACCOUNT_KINDS, RECURRENCES, Account, Budget, Category, Goal, GoalEntry, Reminder, Transaction,
                     User, db)

bp = Blueprint("main", __name__)
PAGE_SIZE = 50


# ------------------------------------------------------------------ helpers
def parse_date(s, default=None):
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return default


def to_int(s):
    try:
        return int(s) if str(s).strip() else None
    except (TypeError, ValueError):
        return None


def money_or_none(s):
    try:
        return parse_money(s, allow_zero=True) if (s or "").strip() else None
    except LedgerError:
        return None


def pending_count(hid):
    return (Transaction.query.filter_by(household_id=hid, status="pending").count()
            + GoalEntry.query.join(Goal).filter(Goal.household_id == hid, GoalEntry.status == "pending",
                                                GoalEntry.txn_id.is_(None)).count()
            + Reminder.query.filter_by(household_id=hid, status="pending").count())


def users_of(hid):
    return User.query.filter_by(household_id=hid, active=True).order_by(User.name).all()


def account_choices(hid, keep=()):
    """Unified account list with sub-headers per kind. Archived accounts never appear (unless already used)."""
    groups = OrderedDict((k, []) for k in ACCOUNT_KINDS)
    for a in Account.query.filter_by(household_id=hid).order_by(Account.institution, Account.name).all():
        if a.archived and a.id not in keep:
            continue
        groups[a.kind].append(a)
    return [(ACCOUNT_KINDS[k], v) for k, v in groups.items() if v]


def account_hints(hid):
    """Short availability text shown under the account pickers."""
    accs = ledger.accounts_map(hid)
    bal = ledger.balances(hid, accs=accs)
    hints = {}
    for a in accs.values():
        if a.kind == "credit_card":
            _, owed, limit = ledger.card_pool(accs, bal, a)
            hints[a.id] = f"{ledger.inr(limit - owed)} credit available"
        elif a.kind == "loan":
            hints[a.id] = f"{ledger.inr(-bal.get(a.id, 0))} outstanding"
        elif a.kind in ("debit_card", "upi") and a.linked_id in accs:
            hints[a.id] = f"{ledger.inr(bal.get(a.linked_id, 0))} in {accs[a.linked_id].name}"
        else:
            hints[a.id] = f"{ledger.inr(bal.get(a.id, 0))} available"
    return hints


def category_payload(hid):
    out = []
    for c in Category.query.filter_by(household_id=hid, archived=False).order_by(Category.name).all():
        out.append(dict(id=c.id, name=c.name, kind=c.kind, parent=c.parent_id,
                        start=c.start_date.isoformat() if c.start_date else None,
                        end=c.end_date.isoformat() if c.end_date else None))
    return out


def can_edit(t):
    return can(g.user, "txn.edit_any") or (can(g.user, "txn.edit_own") and t.created_by == g.user.id)


# ------------------------------------------------------------------ dashboard
PERIODS = [("this_month", "This month"), ("last_month", "Last month"), ("last_30", "30 days"),
           ("quarter", "Quarter"), ("year", "This year"), ("custom", "Custom")]


def resolve_period(key, start=None, end=None, today=None):
    today = today or date.today()
    first = today.replace(day=1)
    if key == "last_month":
        last = first - timedelta(days=1)
        return last.replace(day=1), last
    if key == "last_30":
        return today - timedelta(days=29), today
    if key == "quarter":
        qm = 3 * ((today.month - 1) // 3) + 1
        return date(today.year, qm, 1), today
    if key == "year":
        return date(today.year, 1, 1), today
    if key == "custom" and start and end and start <= end:
        return start, end
    return first, today


@bp.route("/")
@login_required
def dashboard():
    hid = g.household.id
    budgets = Budget.query.filter_by(household_id=hid, archived=False).all()
    mine = [b for b in budgets if b.owner_id in (None, g.user.id)]
    brows = sorted(({"b": b, "s": ledger.budget_status(b)} for b in mine), key=lambda r: -r["s"]["pct"])[:3]
    goals = [x for x in Goal.query.filter_by(household_id=hid, archived=False).all() if x.owner_id in (None, g.user.id)]
    grows = [{"g": x, "s": ledger.goal_summary(x)} for x in goals][:3]
    return render_template("dashboard.html", periods=PERIODS, people=users_of(hid), kinds=ACCOUNT_KINDS,
                           brows=brows, grows=grows)


@bp.route("/api/dashboard")
@login_required
def api_dashboard():
    a = request.args
    start, end = resolve_period(a.get("period", "this_month"), parse_date(a.get("start")), parse_date(a.get("end")))
    person = a.get("person", "all")
    if person not in ("all", "family") and to_int(person) is None:
        person = "all"
    kind = a.get("kind", "all")
    if kind != "all" and kind not in ACCOUNT_KINDS:
        kind = "all"
    return jsonify(ledger.dashboard(g.household.id, start, end, person, kind))


# ------------------------------------------------------------------ activity
def activity_filters(args):
    today = date.today()
    return dict(
        start=parse_date(args.get("start"), today - timedelta(days=30)),
        end=parse_date(args.get("end"), today),
        account_id=to_int(args.get("account")), kind=args.get("kind") or None,
        category_id=to_int(args.get("category")), type=args.get("type") or None,
        min_amt=money_or_none(args.get("min")), max_amt=money_or_none(args.get("max")),
        q=(args.get("q") or "").strip() or None, status="all" if args.get("pending") else "posted")


@bp.route("/activity")
@login_required
def activity():
    hid = g.household.id
    f = activity_filters(request.args)
    q = ledger.query_transactions(hid, f)
    summary = ledger.activity_summary(q)
    page = max(to_int(request.args.get("page")) or 1, 1)
    total = q.count()
    rows = q.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
    days = OrderedDict()
    for t in rows:
        days.setdefault(t.date, []).append(t)
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    cats = Category.query.filter_by(household_id=hid, parent_id=None, archived=False).order_by(Category.kind, Category.name).all()
    n_active = len([k for k in ("account", "kind", "category", "type", "min", "max", "q", "pending") if request.args.get(k)])
    return render_template(
        "activity.html", days=days, summary=summary, f=f, total=total, page=page, pages=-(-total // PAGE_SIZE),
        args=args, accounts=account_choices(hid, keep={f["account_id"]} if f["account_id"] else ()), kinds=ACCOUNT_KINDS,
        cats=cats, can_edit=can_edit, active_filters=n_active)


@bp.route("/activity.csv")
@login_required
def activity_csv():
    hid = g.household.id
    if request.args.get("all"):
        q = Transaction.query.filter_by(household_id=hid)
        name = "samruddhi-all-transactions.csv"
    else:
        q = ledger.query_transactions(hid, activity_filters(request.args))
        name = "samruddhi-transactions.csv"
    body = ledger.transactions_csv(q.order_by(Transaction.date, Transaction.id).all())
    return Response("\ufeff" + body, mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ transactions
def txn_form_context(hid, t=None, ttype="expense", form=None):
    keep = {x for x in (t.account_id if t else None, t.to_account_id if t else None) if x}
    goals = [x for x in Goal.query.filter_by(household_id=hid, archived=False).all()
             if x.owner_id in (None, g.user.id) or can(g.user, "txn.edit_any")]
    return dict(
        t=t, ttype=(t.type if t else ttype), form=form, accounts=account_choices(hid, keep), hints=account_hints(hid),
        cats=category_payload(hid), people=users_of(hid), goals=goals, recurrences=RECURRENCES,
        entry=GoalEntry.query.filter_by(txn_id=t.id).first() if t else None)


def txn_data_from_form(ttype, f):
    d = dict(type=ttype, date=parse_date(f.get("date")), amount=parse_money(f.get("amount")),
             account_id=to_int(f.get("account_id")), to_account_id=to_int(f.get("to_account_id")),
             category_id=to_int(f.get("category_id")), note=f.get("note", ""), scope=f.get("scope", "family"),
             beneficiary_ids=[int(x) for x in f.getlist("beneficiaries") if x.isdigit()],
             goal_id=to_int(f.get("goal_id")) if ttype != "expense" else None,
             recurrence=f.get("recurrence", "none"))
    if ttype == "expense" and d["scope"] == "individuals" and f.get("split") == "custom":
        d["beneficiary_shares"] = {uid: parse_money(f.get(f"share_{uid}", ""), allow_zero=True)
                                   for uid in d["beneficiary_ids"]}
    if ttype == "expense" and (f.get("cb_value") or "").strip():
        mode = "fixed" if f.get("cb_mode") == "fixed" else "percent"
        try:
            value = round(float(f["cb_value"]) * 100) if mode == "percent" else parse_money(f["cb_value"])
        except ValueError:
            raise LedgerError("Cashback value is not a number.") from None
        d["cashback"] = dict(mode=mode, value=value, days=to_int(f.get("cb_days")) or 0,
                             account_id=to_int(f.get("cb_account_id")))
    return d


@bp.route("/transactions/new", methods=["GET", "POST"])
@login_required
def txn_new():
    hid = g.household.id
    ttype = request.values.get("type", "expense")
    if ttype not in ("income", "expense", "transfer"):
        ttype = "expense"
    if request.method == "POST":
        try:
            d = txn_data_from_form(ttype, request.form)
            t = ledger.create_transaction(hid, g.user.id, d)
            flash("Transaction added.", "ok")
            tgt = t.to_account_id if t.type == "transfer" else t.account_id
            if d.get("goal_id"):
                w = ledger.goal_warning(hid, tgt)
                if w:
                    flash(w, "warn")
            if t.recurrence != "none":
                ledger.run_due(hid)
            return redirect(request.form.get("next") or url_for("main.activity"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("txn_form.html", **txn_form_context(hid, ttype=ttype, form=request.form if request.method == "POST" else None))


@bp.route("/transactions/<int:tid>", methods=["GET", "POST"])
@login_required
def txn_edit(tid):
    t = db.get_or_404(Transaction, tid)
    if t.household_id != g.household.id:
        abort(404)
    if not can_edit(t):
        abort(403, "Only the person who added this (or an admin) can change it.")
    if request.method == "POST":
        try:
            d = txn_data_from_form(t.type, request.form)
            ledger.update_transaction(t, d)
            flash("Saved.", "ok")
            return redirect(request.form.get("next") or url_for("main.activity"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            t = db.session.get(Transaction, tid)
    return render_template("txn_form.html", **txn_form_context(g.household.id, t=t, form=request.form if request.method == "POST" else None))


@bp.route("/transactions/<int:tid>/delete", methods=["POST"])
@login_required
def txn_delete(tid):
    t = db.get_or_404(Transaction, tid)
    if t.household_id != g.household.id:
        abort(404)
    if not can_edit(t):
        abort(403)
    ledger.delete_transaction(t)
    flash("Transaction deleted.", "ok")
    return redirect(request.form.get("next") or url_for("main.activity"))


# ------------------------------------------------------------------ approvals
@bp.route("/approvals")
@login_required
def approvals():
    hid = g.household.id
    txns = Transaction.query.filter_by(household_id=hid, status="pending").order_by(Transaction.date, Transaction.id).all()
    entries = (GoalEntry.query.join(Goal).filter(Goal.household_id == hid, GoalEntry.status == "pending",
                                                 GoalEntry.txn_id.is_(None)).order_by(GoalEntry.date).all())
    rems = Reminder.query.filter_by(household_id=hid, status="pending").order_by(Reminder.due_date).all()
    accs = ledger.accounts_map(hid)
    payers = [a for a in accs.values() if a.kind in ("bank", "upi", "debit_card", "wallet", "cash") and not a.archived]
    return render_template("approvals.html", txns=txns, entries=entries, rems=rems, payers=payers, accs=accs,
                           total=len(txns) + len(entries) + len(rems))


def _guard(obj_hid):
    if obj_hid != g.household.id:
        abort(404)


@bp.route("/approvals/txn/<int:tid>/<action>", methods=["POST"])
@require("approvals.manage")
def approve_txn(tid, action):
    t = db.get_or_404(Transaction, tid)
    _guard(t.household_id)
    try:
        if action == "approve":
            ledger.approve_transaction(t, g.user.id)
            flash("Approved.", "ok")
        elif action == "reject":
            ledger.reject_transaction(t, g.user.id)
            flash("Rejected.", "ok")
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("main.approvals"))


@bp.route("/approvals/goal/<int:eid>/<action>", methods=["POST"])
@require("approvals.manage")
def approve_goal_entry(eid, action):
    e = db.get_or_404(GoalEntry, eid)
    _guard(e.goal.household_id)
    if e.status == "pending":
        e.status = "posted" if action == "approve" else "rejected"
        db.session.commit()
        flash("Approved." if action == "approve" else "Rejected.", "ok")
    return redirect(url_for("main.approvals"))


@bp.route("/approvals/reminder/<int:rid>/<action>", methods=["POST"])
@require("approvals.manage")
def handle_reminder(rid, action):
    r = db.get_or_404(Reminder, rid)
    _guard(r.household_id)
    try:
        if action == "approve":
            ledger.approve_reminder(r, g.user.id, to_int(request.form.get("pay_from")),
                                    money_or_none(request.form.get("amount")) or None)
            flash("Payment recorded as a transfer.", "ok")
        elif r.status == "pending":
            r.status = "dismissed"
            db.session.commit()
            flash("Reminder dismissed.", "ok")
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("main.approvals"))


# ------------------------------------------------------------------ subscriptions
@bp.route("/subscriptions")
@login_required
def subscriptions():
    subs = (Transaction.query.filter(Transaction.household_id == g.household.id, Transaction.recurrence != "none",
                                     Transaction.template_id.is_(None), Transaction.status == "posted")
            .order_by(Transaction.rec_active.desc(), Transaction.next_run).all())
    return render_template("subscriptions.html", subs=subs, can_edit=can_edit)


@bp.route("/subscriptions/<int:tid>/<action>", methods=["POST"])
@login_required
def subscription_action(tid, action):
    t = db.get_or_404(Transaction, tid)
    _guard(t.household_id)
    if not can_edit(t) or not t.is_template:
        abort(403)
    if action in ("pause", "resume"):
        ledger.set_subscription_active(t, action == "resume")
        flash("Subscription resumed." if action == "resume" else "Subscription paused.", "ok")
    elif action == "end":
        t.recurrence, t.next_run = "none", None
        db.session.commit()
        flash("Subscription ended. History is kept.", "ok")
    return redirect(url_for("main.subscriptions"))


@bp.route("/more")
@login_required
def more():
    return render_template("more.html")
