import io
import zipfile
from datetime import date

from flask import (Blueprint, Response, abort, flash, g, make_response, redirect, render_template, request,
                   session, url_for)

from . import THEME_KEYS, ledger
from .permissions import can, require
from .auth import login_required, validate_new_credentials
from .ledger import LedgerError, parse_money
from .models import (ACCOUNT_KINDS, BANK_SUBTYPES, GOAL_KINDS, PERIODS, RECURRENCES, SPENDING_KINDS, Account, Budget,
                     CashbackRule, Category, Goal, GoalEntry, GoalRule, User, db)
from .routes_main import account_choices, money_or_none, parse_date, to_int, users_of

bp = Blueprint("settings", __name__)


def _own(obj, attr="household_id"):
    if getattr(obj, attr) != g.household.id:
        abort(404)
    return obj


def category_tree(hid, kind=None):
    q = Category.query.filter_by(household_id=hid, parent_id=None, archived=False)
    if kind:
        q = q.filter_by(kind=kind)
    return [(c, sorted([k for k in c.children if not k.archived], key=lambda k: k.name))
            for c in q.order_by(Category.name).all()]


# =================================================================== accounts
def account_from_form(f, kind, acc=None):
    name = (f.get("name") or "").strip()
    if not name:
        raise LedgerError("Give the account a name.")
    last4 = (f.get("last4") or "").strip()
    if last4 and not (last4.isdigit() and len(last4) <= 4):
        raise LedgerError("Last 4 digits must be up to 4 numbers.")
    due = to_int(f.get("due_day"))
    if due is not None and not 1 <= due <= 31:
        raise LedgerError("Due day must be between 1 and 31.")
    vals = dict(name=name, institution=(f.get("institution") or "").strip(), subtype=(f.get("subtype") or "").strip(),
                last4=last4, purpose=(f.get("purpose") or "").strip()[:60], owner_id=to_int(f.get("owner_id")))
    if vals["owner_id"] and not User.query.filter_by(id=vals["owner_id"], household_id=g.household.id).first():
        raise LedgerError("Unknown owner.")
    if not vals["institution"] and kind not in ("debit_card", "upi"):
        vals["institution"] = name
    start = money_or_none(f.get("starting")) or 0
    if kind in ("credit_card", "loan"):
        start = -start                                    # entered as "amount owed", stored as negative
    vals["starting_balance"] = 0 if kind in ("debit_card", "upi") else start
    if kind == "credit_card":
        vals.update(credit_limit=money_or_none(f.get("credit_limit")) or 0, due_day=due,
                    parent_id=to_int(f.get("parent_id")), pay_from_id=to_int(f.get("pay_from_id")))
        if vals["parent_id"]:
            parent = db.session.get(Account, vals["parent_id"])
            if not parent or parent.household_id != g.household.id or parent.kind != "credit_card" or parent.parent_id:
                raise LedgerError("An add-on card must belong to a main credit card.")
            if acc and parent.id == acc.id:
                raise LedgerError("A card cannot be its own parent.")
            vals.update(credit_limit=0, due_day=None)
        elif not vals["credit_limit"]:
            raise LedgerError("Enter the credit limit.")
    elif kind in ("debit_card", "upi"):
        link = db.session.get(Account, to_int(f.get("linked_id")) or 0)
        if not link or link.household_id != g.household.id or link.kind != "bank":
            raise LedgerError("Link this to the bank account it draws from.")
        vals["linked_id"] = link.id
    elif kind == "investment":
        vals["current_value"] = money_or_none(f.get("current_value"))
    elif kind == "loan":
        vals.update(loan_total=money_or_none(f.get("loan_total")) or 0, emi_principal=money_or_none(f.get("emi_principal")) or 0,
                    emi_interest=money_or_none(f.get("emi_interest")) or 0, due_day=due, pay_from_id=to_int(f.get("pay_from_id")))
        if vals["loan_total"] and -start > vals["loan_total"]:
            raise LedgerError("Outstanding cannot be more than the original loan amount.")
    return vals


@bp.route("/accounts")
@login_required
def accounts():
    show_archived = bool(request.args.get("archived"))
    groups = ledger.account_groups(g.household.id, include_archived=show_archived)
    return render_template("accounts.html", groups=groups, show_archived=show_archived, kinds=ACCOUNT_KINDS)


@bp.route("/accounts/new", methods=["GET", "POST"])
@require("accounts.manage")
def account_new():
    kind = request.values.get("kind", "bank")
    if kind not in ACCOUNT_KINDS:
        kind = "bank"
    if request.method == "POST":
        try:
            vals = account_from_form(request.form, kind)
            db.session.add(Account(household_id=g.household.id, kind=kind, **vals))
            db.session.commit()
            flash("Account added.", "ok")
            return redirect(url_for("settings.accounts"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("account_form.html", acc=None, kind=kind, kinds=ACCOUNT_KINDS, **account_form_ctx(),
                           form=request.form if request.method == "POST" else None)


def account_form_ctx(exclude_id=None):
    hid = g.household.id
    accs = Account.query.filter_by(household_id=hid, archived=False).all()
    return dict(people=users_of(hid), banks=[a for a in accs if a.kind == "bank"],
                parents=[a for a in accs if a.kind == "credit_card" and not a.parent_id and a.id != exclude_id],
                payers=[a for a in accs if a.kind in ("bank", "wallet", "cash")], subtypes=BANK_SUBTYPES)


@bp.route("/accounts/<int:aid>", methods=["GET", "POST"])
@require("accounts.manage")
def account_edit(aid):
    acc = _own(db.get_or_404(Account, aid))
    if request.method == "POST":
        try:
            vals = account_from_form(request.form, acc.kind, acc)
            for k, v in vals.items():
                setattr(acc, k, v)
            db.session.commit()
            flash("Saved.", "ok")
            return redirect(url_for("settings.accounts"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("account_form.html", acc=acc, kind=acc.kind, kinds=ACCOUNT_KINDS, **account_form_ctx(acc.id),
                           form=request.form if request.method == "POST" else None)


@bp.route("/accounts/<int:aid>/archive", methods=["POST"])
@require("accounts.manage")
def account_archive(aid):
    acc = _own(db.get_or_404(Account, aid))
    acc.archived = not acc.archived
    db.session.commit()
    flash(f"{acc.name} {'archived - history is kept' if acc.archived else 'restored'}.", "ok")
    if acc.archived and acc.has_own_balance and ledger.balances(g.household.id).get(acc.id):
        flash(f"{acc.name} still has a balance of {ledger.inr(ledger.balances(g.household.id)[acc.id])}.", "warn")
    return redirect(url_for("settings.accounts"))


# =================================================================== cashbacks
def rule_from_form(f):
    hid = g.household.id
    name = (f.get("name") or "").strip()
    acc = db.session.get(Account, to_int(f.get("account_id")) or 0)
    if not name or not acc or acc.household_id != hid:
        raise LedgerError("Name the rule and choose the account whose purchases earn it.")
    mode = "fixed" if f.get("mode") == "fixed" else "percent"
    try:
        bp_ = round(float(f.get("percent") or 0) * 100) if mode == "percent" else 0
    except ValueError:
        raise LedgerError("Percent must be a number.") from None
    fixed = money_or_none(f.get("fixed_amount")) or 0
    if (mode == "percent" and not 0 < bp_ <= 10000) or (mode == "fixed" and fixed <= 0):
        raise LedgerError("Enter a percentage (0-100) or a fixed cashback amount.")
    cat_id = to_int(f.get("category_id"))
    if cat_id and not Category.query.filter_by(id=cat_id, household_id=hid, kind="expense").first():
        raise LedgerError("Unknown category.")
    payout = to_int(f.get("payout_account_id"))
    if payout and not Account.query.filter_by(id=payout, household_id=hid).first():
        raise LedgerError("Unknown payout account.")
    return dict(name=name, account_id=acc.id, category_id=cat_id, keyword=(f.get("keyword") or "").strip()[:80],
                mode=mode, basis_points=bp_, fixed_amount=fixed, min_spend=money_or_none(f.get("min_spend")) or 0,
                max_cashback=money_or_none(f.get("max_cashback")) or 0, delay_days=max(to_int(f.get("delay_days")) or 0, 0),
                payout_account_id=payout, one_time=bool(f.get("one_time")), active=bool(f.get("active", "1")))


@bp.route("/cashbacks")
@login_required
def cashbacks():
    rules = CashbackRule.query.filter_by(household_id=g.household.id).order_by(CashbackRule.active.desc(), CashbackRule.name).all()
    return render_template("cashbacks.html", rules=rules)


@bp.route("/cashbacks/new", methods=["GET", "POST"])
@bp.route("/cashbacks/<int:rid>", methods=["GET", "POST"])
@require("cashback.manage")
def cashback_form(rid=None):
    rule = _own(db.get_or_404(CashbackRule, rid)) if rid else None
    if request.method == "POST":
        try:
            vals = rule_from_form(request.form)
            if rule:
                for k, v in vals.items():
                    setattr(rule, k, v)
            else:
                db.session.add(CashbackRule(household_id=g.household.id, **vals))
            db.session.commit()
            flash("Cashback rule saved.", "ok")
            return redirect(url_for("settings.cashbacks"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    hid = g.household.id
    accs = [(l, [a for a in v]) for l, v in account_choices(hid, {rule.account_id, rule.payout_account_id} if rule else ())]
    return render_template("cashback_form.html", rule=rule, accounts=accs, tree=category_tree(hid, "expense"),
                           form=request.form if request.method == "POST" else None)


@bp.route("/cashbacks/<int:rid>/toggle", methods=["POST"])
@require("cashback.manage")
def cashback_toggle(rid):
    r = _own(db.get_or_404(CashbackRule, rid))
    r.active = not r.active
    db.session.commit()
    return redirect(url_for("settings.cashbacks"))


# =================================================================== budgets
@bp.route("/budgets")
@login_required
def budgets():
    hid = g.household.id
    rows = []
    for b in Budget.query.filter_by(household_id=hid, archived=False).all():
        rows.append(dict(b=b, s=ledger.budget_status(b)))
    rows.sort(key=lambda r: (r["b"].owner_id is not None, r["b"].owner.name if r["b"].owner else "", r["b"].category.name))
    family = [r for r in rows if r["b"].owner_id is None]
    people = {}
    for r in rows:
        if r["b"].owner_id is not None:
            people.setdefault(r["b"].owner, []).append(r)
    return render_template("budgets.html", family=family, people=people)


@bp.route("/budgets/new", methods=["GET", "POST"])
@bp.route("/budgets/<int:bid>", methods=["GET", "POST"])
@login_required
def budget_form(bid=None):
    hid = g.household.id
    b = _own(db.get_or_404(Budget, bid)) if bid else None
    if b and not (can(g.user, "budget.family") or (can(g.user, "budget.own") and b.owner_id == g.user.id)):
        abort(403, "Family budgets are managed by the admin.")
    if request.method == "POST":
        f = request.form
        try:
            owner = to_int(f.get("owner_id")) if can(g.user, "budget.family") else g.user.id
            if owner is not None and not User.query.filter_by(id=owner, household_id=hid).first():
                raise LedgerError("Unknown person.")
            if owner is None and not can(g.user, "budget.family"):
                raise LedgerError("Only the admin can set family budgets.")
            cat = db.session.get(Category, to_int(f.get("category_id")) or 0)
            if not cat or cat.household_id != hid or cat.kind != "expense":
                raise LedgerError("Choose an expense category.")
            period = f.get("period")
            if period not in PERIODS:
                raise LedgerError("Choose weekly, monthly or annual.")
            amount = parse_money(f.get("amount"))
            dupe = Budget.query.filter_by(household_id=hid, owner_id=owner, category_id=cat.id, period=period, archived=False)
            if b:
                dupe = dupe.filter(Budget.id != b.id)
            if dupe.first():
                raise LedgerError("That budget already exists - edit it instead.")
            if b:
                b.owner_id, b.category_id, b.period, b.amount = owner, cat.id, period, amount
            else:
                db.session.add(Budget(household_id=hid, owner_id=owner, category_id=cat.id, period=period, amount=amount))
            db.session.commit()
            flash("Budget saved.", "ok")
            return redirect(url_for("settings.budgets"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("budget_form.html", b=b, tree=category_tree(hid, "expense"), people=users_of(hid), periods=PERIODS,
                           form=request.form if request.method == "POST" else None)


@bp.route("/budgets/<int:bid>/archive", methods=["POST"])
@login_required
def budget_archive(bid):
    b = _own(db.get_or_404(Budget, bid))
    if not (can(g.user, "budget.family") or b.owner_id == g.user.id):
        abort(403)
    b.archived = True
    db.session.commit()
    flash("Budget removed.", "ok")
    return redirect(url_for("settings.budgets"))


# =================================================================== goals
def can_manage_goal(goal):
    return can(g.user, "goal.shared") or (can(g.user, "goal.own") and goal.owner_id is not None and goal.owner_id == g.user.id)


@bp.route("/goals")
@login_required
def goals():
    rows = [dict(g=x, s=ledger.goal_summary(x)) for x in Goal.query.filter_by(household_id=g.household.id, archived=False)
            .order_by(Goal.name).all() if x.owner_id in (None, g.user.id) or can(g.user, "txn.edit_any")]
    return render_template("goals.html", rows=rows, can_manage=can_manage_goal)


@bp.route("/goals/new", methods=["GET", "POST"])
@bp.route("/goals/<int:gid>/edit", methods=["GET", "POST"])
@login_required
def goal_form(gid=None):
    goal = _own(db.get_or_404(Goal, gid)) if gid else None
    if goal and not can_manage_goal(goal):
        abort(403, "Shared goals are managed by the admin.")
    if request.method == "POST":
        f = request.form
        try:
            name = (f.get("name") or "").strip()
            if not name:
                raise LedgerError("Name the goal.")
            owner = to_int(f.get("owner_id")) if can(g.user, "goal.shared") else g.user.id
            if owner is None and not can(g.user, "goal.shared"):
                raise LedgerError("Only the admin can create shared goals.")
            if owner is not None and not User.query.filter_by(id=owner, household_id=g.household.id).first():
                raise LedgerError("Unknown person.")
            target = money_or_none(f.get("target")) or 0
            deadline = parse_date(f.get("deadline"))
            if goal:
                goal.name, goal.owner_id, goal.target, goal.deadline = name, owner, target, deadline
            else:
                goal = Goal(household_id=g.household.id, name=name, owner_id=owner, target=target, deadline=deadline)
                db.session.add(goal)
            db.session.commit()
            flash("Goal saved.", "ok")
            return redirect(url_for("settings.goal_detail", gid=goal.id))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("goal_form.html", goal=goal, people=users_of(g.household.id),
                           form=request.form if request.method == "POST" else None)


@bp.route("/goals/<int:gid>")
@login_required
def goal_detail(gid):
    goal = _own(db.get_or_404(Goal, gid))
    if goal.owner_id not in (None, g.user.id) and not can(g.user, "txn.edit_any"):
        abort(403)
    accs = ledger.accounts_map(g.household.id)
    eligible = [a for a in accs.values() if a.kind in GOAL_KINDS and not a.archived]
    entries = sorted([e for e in goal.entries if e.status != "rejected"], key=lambda e: (e.date, e.id), reverse=True)
    rules = GoalRule.query.filter_by(goal_id=goal.id).order_by(GoalRule.active.desc()).all()
    return render_template("goal_detail.html", goal=goal, s=ledger.goal_summary(goal), eligible=eligible, entries=entries[:40],
                           rules=rules, accs=accs, can_manage=can_manage_goal(goal), recurrences=RECURRENCES)


@bp.route("/goals/<int:gid>/add", methods=["POST"])
@login_required
def goal_add_money(gid):
    goal = _own(db.get_or_404(Goal, gid))
    f = request.form
    try:
        acc_id = to_int(f.get("account_id"))
        amount = parse_money(f.get("amount"))
        ledger.add_goal_entry(g.household.id, g.user.id, goal.id, acc_id, amount, parse_date(f.get("date"), date.today()))
        flash("Added to the goal. Your account balance is unchanged - this only earmarks the money.", "ok")
        w = ledger.goal_warning(g.household.id, acc_id)
        if w:
            flash(w, "warn")
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("settings.goal_detail", gid=gid))


@bp.route("/goals/<int:gid>/rule", methods=["POST"])
@login_required
def goal_add_rule(gid):
    goal = _own(db.get_or_404(Goal, gid))
    if not can_manage_goal(goal):
        abort(403)
    f = request.form
    try:
        ledger.add_goal_rule(g.household.id, goal.id, to_int(f.get("account_id")), parse_money(f.get("amount")),
                             f.get("recurrence"), parse_date(f.get("start"), date.today()))
        ledger.run_due(g.household.id)
        flash("Recurring assignment created. Each run waits in Approvals before it counts.", "ok")
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("settings.goal_detail", gid=gid))


@bp.route("/goals/rule/<int:rid>/toggle", methods=["POST"])
@login_required
def goal_rule_toggle(rid):
    r = db.get_or_404(GoalRule, rid)
    goal = _own(r.goal)
    if not can_manage_goal(goal):
        abort(403)
    r.active = not r.active
    db.session.commit()
    return redirect(url_for("settings.goal_detail", gid=goal.id))


@bp.route("/goals/<int:gid>/archive", methods=["POST"])
@login_required
def goal_archive(gid):
    goal = _own(db.get_or_404(Goal, gid))
    if not can_manage_goal(goal):
        abort(403)
    goal.archived = True
    db.session.commit()
    flash("Goal archived.", "ok")
    return redirect(url_for("settings.goals"))


# =================================================================== categories (admin edits)
@bp.route("/categories")
@login_required
def categories():
    hid = g.household.id
    sections = []
    for kind, label in (("expense", "Expenses"), ("income", "Income"), ("transfer", "Transfers")):
        rows = Category.query.filter_by(household_id=hid, parent_id=None, kind=kind).order_by(Category.archived, Category.name).all()
        sections.append((kind, label, rows))
    return render_template("categories.html", sections=sections, show=bool(request.args.get("archived")))


@bp.route("/categories/new", methods=["GET", "POST"])
@bp.route("/categories/<int:cid>", methods=["GET", "POST"])
@require("categories.manage")
def category_form(cid=None):
    hid = g.household.id
    c = _own(db.get_or_404(Category, cid)) if cid else None
    parents = Category.query.filter_by(household_id=hid, parent_id=None, archived=False).order_by(Category.kind, Category.name).all()
    if request.method == "POST":
        f = request.form
        try:
            name = (f.get("name") or "").strip()
            if not name:
                raise LedgerError("Enter a name.")
            parent = db.session.get(Category, to_int(f.get("parent_id")) or 0) if f.get("parent_id") else None
            if parent and (parent.household_id != hid or parent.parent_id):
                raise LedgerError("Sub-categories can only sit under a main category.")
            kind = parent.kind if parent else f.get("kind")
            if kind not in ("income", "expense", "transfer"):
                raise LedgerError("Choose income, expense or transfer.")
            start, end = parse_date(f.get("start_date")), parse_date(f.get("end_date"))
            if start and end and end < start:
                raise LedgerError("The end date is before the start date.")
            clash = Category.query.filter_by(household_id=hid, name=name, kind=kind, parent_id=parent.id if parent else None)
            if c:
                clash = clash.filter(Category.id != c.id)
            if clash.first():
                raise LedgerError("A category with that name already exists there.")
            if c:
                c.name, c.start_date, c.end_date = name, start, end
            else:
                c = Category(household_id=hid, name=name, kind=kind, parent_id=parent.id if parent else None,
                             start_date=start, end_date=end)
                db.session.add(c)
            db.session.commit()
            flash("Category saved.", "ok")
            return redirect(url_for("settings.categories"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("category_form.html", c=c, parents=parents, preset=request.args.get("parent"),
                           form=request.form if request.method == "POST" else None)


@bp.route("/categories/<int:cid>/archive", methods=["POST"])
@require("categories.manage")
def category_archive(cid):
    c = _own(db.get_or_404(Category, cid))
    if c.system_key:
        flash("Built-in categories are used by cashback and payments and cannot be archived.", "error")
    else:
        c.archived = not c.archived
        for k in c.children:
            k.archived = c.archived
        db.session.commit()
        flash("Category archived. Old transactions keep it." if c.archived else "Category restored.", "ok")
    return redirect(url_for("settings.categories"))


# =================================================================== people + profile
def _admin_count():
    return User.query.filter_by(household_id=g.household.id, is_admin=True, active=True).count()


@bp.route("/people")
@login_required
def people():
    return render_template("people.html", members=User.query.filter_by(household_id=g.household.id).order_by(User.active.desc(), User.name).all())


@bp.route("/people/new", methods=["GET", "POST"])
@bp.route("/people/<int:uid>", methods=["GET", "POST"])
@require("people.manage")
def person_form(uid=None):
    u = _own(db.get_or_404(User, uid)) if uid else None
    if request.method == "POST":
        f = request.form
        try:
            name = (f.get("name") or "").strip()
            if not name:
                raise LedgerError("Enter a name.")
            minor = bool(f.get("is_minor"))
            make_admin = bool(f.get("is_admin")) and not minor
            if u and u.is_admin and not make_admin and u.active and _admin_count() <= 1:
                raise LedgerError("The household needs at least one admin.")
            if minor:
                username = email = pw = None
            else:
                username, email, pw = f.get("username"), f.get("email"), f.get("password") or None
                err = validate_new_credentials(username, email, pw if (pw or not (u and u.password_hash)) else None, u.id if u else None)
                if err:
                    raise LedgerError(err)
                if not pw and not (u and u.password_hash):
                    raise LedgerError("Set a password for this member.")
            if not u:
                u = User(household_id=g.household.id, name=name)
                db.session.add(u)
            u.name, u.is_minor, u.is_admin = name, minor, make_admin
            if minor:
                u.username = u.email = u.password_hash = None
            else:
                u.username, u.email = username.strip().lower(), email.strip().lower()
                if pw:
                    u.set_password(pw)
            db.session.commit()
            flash("Member saved.", "ok")
            return redirect(url_for("settings.people"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("person_form.html", u=u, form=request.form if request.method == "POST" else None)


@bp.route("/people/<int:uid>/active", methods=["POST"])
@require("people.manage")
def person_active(uid):
    u = _own(db.get_or_404(User, uid))
    if u.active and u.is_admin and _admin_count() <= 1:
        flash("The household needs at least one admin.", "error")
    elif u.id == g.user.id:
        flash("You cannot deactivate yourself.", "error")
    else:
        u.active = not u.active
        db.session.commit()
        flash("Member deactivated - their history stays." if not u.active else "Member reactivated.", "ok")
    return redirect(url_for("settings.people"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def profile():
    u = g.user
    if request.method == "POST":
        f = request.form
        try:
            name = (f.get("name") or "").strip()
            if not name:
                raise LedgerError("Enter your name.")
            new_pw = f.get("new_password") or None
            err = validate_new_credentials(f.get("username"), f.get("email"), new_pw, u.id)
            if err:
                raise LedgerError(err)
            if (new_pw or f.get("email", "").strip().lower() != u.email or f.get("username", "").strip().lower() != u.username) \
                    and not u.check_password(f.get("current_password", "")):
                raise LedgerError("Enter your current password to change login details.")
            u.name, u.username, u.email = name, f["username"].strip().lower(), f["email"].strip().lower()
            if new_pw:
                u.set_password(new_pw)
            if can(g.user, "people.manage") and (f.get("household") or "").strip():
                g.household.name = f["household"].strip()[:120]
            db.session.commit()
            flash("Saved.", "ok")
            return redirect(url_for("settings.profile"))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("profile.html")


@bp.route("/settings/theme", methods=["POST"])
@login_required
def set_theme():
    t = request.form.get("theme")
    if t in THEME_KEYS:
        g.user.theme = t
        db.session.commit()
    resp = make_response(redirect(request.referrer or url_for("settings.profile")))
    resp.set_cookie("theme", g.user.theme, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# =================================================================== data reset
@bp.route("/reset")
@require("data.reset")
def reset():
    return render_template("reset.html", today=date.today())


@bp.route("/reset/backup", methods=["POST"])
@require("data.reset")
def reset_backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in ledger.backup_csvs(g.household.id).items():
            z.writestr(name, "\ufeff" + body)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="samruddhi-backup-{date.today()}.zip"'})


@bp.route("/reset/run", methods=["POST"])
@require("data.reset")
def reset_run():
    f = request.form
    mode = f.get("mode")
    if not g.user.check_password(f.get("password", "")):
        flash("Wrong password - nothing was deleted.", "error")
        return redirect(url_for("settings.reset"))
    if (f.get("confirm") or "").strip() != "RESET":
        flash("Type RESET to confirm - nothing was deleted.", "error")
        return redirect(url_for("settings.reset"))
    hid = g.household.id
    if mode == "full":
        ledger.reset_full(hid)
        session.clear()
        flash("Everything was deleted. Set up a new household.", "ok")
        return redirect(url_for("auth.setup"))
    if mode == "transactions":
        n = ledger.reset_transactions(hid, zero_starting_balances=bool(f.get("zero_starting")))
        flash(f"Deleted {n} transactions. Accounts, budgets, goals, people and categories were kept.", "ok")
    elif mode == "after":
        cutoff = parse_date(f.get("cutoff"))
        if not cutoff:
            flash("Choose the date to reset from.", "error")
            return redirect(url_for("settings.reset"))
        n = ledger.reset_after(hid, cutoff)
        flash(f"Deleted {n} transactions dated {cutoff:%d %b %Y} or later.", "ok")
    else:
        flash("Choose a reset option.", "error")
    return redirect(url_for("main.dashboard"))
