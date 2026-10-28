"""Family finance features for Samruddhi Finance.

The app is deployed as one household workspace. Every household member has their own
login; financial records are shared within that workspace, while beneficiary attribution,
budget selection, goals, subscriptions and rewards remain separately recorded.
"""
import calendar
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash


def register_family_routes(app, db, models, helpers):
    FamilyMember = models["FamilyMember"]
    AccountOwner = models["AccountOwner"]
    AccountPurposeTag = models["AccountPurposeTag"]
    Category = models["Category"]
    CategoryParent = models["CategoryParent"]
    Budget = models["Budget"]
    Goal = models["Goal"]
    GoalEarmark = models["GoalEarmark"]
    Transaction = models["Transaction"]
    TransactionMetadata = models["TransactionMetadata"]
    TransactionBeneficiary = models["TransactionBeneficiary"]
    Subscription = models["Subscription"]
    SubscriptionOccurrence = models["SubscriptionOccurrence"]
    CashbackScheme = models["CashbackScheme"]
    CashbackEvent = models["CashbackEvent"]
    Account = models["Account"]
    CreditCardStatement = models["CreditCardStatement"]
    StatementPayment = models["StatementPayment"]
    CreditCardProfile = models["CreditCardProfile"]
    money_to_paise = helpers["money_to_paise"]
    format_money = helpers["format_money"]
    normalized_colour = helpers["normalized_colour"]
    login_required = helpers["login_required"]

    PURPOSE_TAGS = {
        "daily": "Daily expenses", "credit": "Credit", "savings": "Savings",
        "emergency": "Emergency fund", "investment": "Investment", "wallet": "Wallet",
        "bills": "Bills and subscriptions",
    }

    def active_members():
        return FamilyMember.query.filter_by(is_active=True).order_by(FamilyMember.name).all()

    def current_member():
        member_id = session.get("member_id")
        return db.session.get(FamilyMember, int(member_id)) if member_id else None

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_app.config.get("TESTING") and not current_app.config.get("TEST_AUTH_REQUIRED"):
                return view(*args, **kwargs)
            member = current_member()
            if not member:
                return redirect(url_for("login", next=request.path))
            if member.role != "admin":
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def next_date_after(day, frequency, interval_weeks=6):
        if frequency == "weekly":
            return day + timedelta(days=7)
        if frequency == "every_n_weeks":
            return day + timedelta(days=max(1, min(int(interval_weeks or 6), 52)) * 7)
        if frequency == "annual":
            try:
                return day.replace(year=day.year + 1)
            except ValueError:
                return day.replace(year=day.year + 1, month=2, day=28)
        month_index = day.year * 12 + (day.month - 1) + 1
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))

    def matching_scheme(account_id, description):
        needle = (description or "").casefold()
        matches = [scheme for scheme in CashbackScheme.query.filter_by(
            account_id=account_id, is_active=True).all()
            if scheme.merchant_keyword.casefold() in needle]
        return max(matches, key=lambda scheme: len(scheme.merchant_keyword), default=None)

    def category_is_within(selected_category_id, budget_category_id):
        if not budget_category_id:
            return True
        if selected_category_id == budget_category_id:
            return True
        relation = CategoryParent.query.filter_by(subcategory_id=selected_category_id).first()
        return bool(relation and relation.parent_category_id == budget_category_id)

    def spend_for_budget(budget, start, end):
        query = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).join(
            TransactionMetadata, TransactionMetadata.transaction_id == Transaction.id
        ).filter(
            TransactionMetadata.budget_id == budget.id,
            Transaction.kind == "expense",
            Transaction.is_archived.is_(False),
            Transaction.transaction_date >= max(start, budget.start_date),
            Transaction.transaction_date < end,
        )
        return int(query.scalar() or 0)

    def budget_metrics(budget):
        today = date.today()
        if budget.period_type == "monthly":
            start = today.replace(day=1)
            end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
            spend = spend_for_budget(budget, start, end)
            target = budget.amount_paise
            monthly_spend = spend
            monthly_target = target
        else:
            start, end = date(today.year, 1, 1), date(today.year + 1, 1, 1)
            spend = spend_for_budget(budget, start, end)
            target = budget.amount_paise
            month_start = today.replace(day=1)
            month_end = date(month_start.year + (month_start.month == 12), 1 if month_start.month == 12 else month_start.month + 1, 1)
            monthly_spend = spend_for_budget(budget, month_start, month_end)
            monthly_target = target // 12 if budget.spread_monthly else 0
        return {
            "budget": budget, "spend": spend, "target": target,
            "remaining": target - spend,
            "progress": min(100, round(spend * 100 / target)) if target else 0,
            "monthly_spend": monthly_spend, "monthly_target": monthly_target,
            "monthly_progress": min(100, round(monthly_spend * 100 / monthly_target)) if monthly_target else 0,
        }

    def account_balance(account):
        balance = int(account.opening_balance_paise or 0)
        txns = Transaction.query.filter(
            Transaction.is_archived.is_(False),
            ((Transaction.account_id == account.id) | (Transaction.transfer_to_account_id == account.id)),
        ).all()
        for txn in txns:
            if txn.kind == "transfer":
                if account.account_type == "credit":
                    balance += -txn.amount_paise if txn.transfer_to_account_id == account.id else txn.amount_paise
                else:
                    balance += txn.amount_paise if txn.transfer_to_account_id == account.id else -txn.amount_paise
            elif txn.account_id == account.id:
                if account.account_type == "credit":
                    balance += txn.amount_paise if txn.kind == "expense" else -txn.amount_paise
                else:
                    balance += txn.amount_paise if txn.kind == "income" else -txn.amount_paise
        return balance

    def add_cashback_event(transaction, scheme=None, rate_bps=None, destination_account_id=None, delay_days=None):
        if transaction.kind != "expense":
            return None
        scheme = scheme or matching_scheme(transaction.account_id, transaction.description)
        if scheme:
            if rate_bps is None:
                rate_bps = scheme.rate_bps
            if destination_account_id is None:
                destination_account_id = scheme.destination_account_id
            if delay_days is None:
                delay_days = scheme.delay_days
        if rate_bps is None or destination_account_id is None:
            return None
        try:
            rate_bps = int(rate_bps)
            delay_days = int(delay_days or 0)
        except (TypeError, ValueError):
            return None
        destination = db.session.get(Account, int(destination_account_id))
        if not destination or rate_bps < 1 or rate_bps > 10000 or delay_days < 0 or delay_days > 365:
            return None
        expected = (int(transaction.amount_paise) * rate_bps + 5000) // 10000
        if expected < 1:
            return None
        due = transaction.transaction_date + timedelta(days=delay_days)
        status = "pending_approval" if due <= date.today() else "scheduled"
        event = CashbackEvent.query.filter_by(transaction_id=transaction.id).first()
        if event and event.status == "approved":
            return event
        if event is None:
            event = CashbackEvent(transaction_id=transaction.id)
            db.session.add(event)
        event.scheme_id = scheme.id if scheme else None
        event.expected_paise = expected
        event.actual_paise = None
        event.destination_account_id = destination.id
        event.expected_date = due
        event.status = status
        return event

    def save_transaction_extras(transaction, is_new):
        # Budgeting and beneficiary attribution apply only to expenses. They are metadata,
        # not transfers, reimbursements or settlements between family members.
        meta = db.session.get(TransactionMetadata, transaction.id)
        if meta is None:
            meta = TransactionMetadata(transaction_id=transaction.id)
            db.session.add(meta)
        if transaction.kind == "expense":
            selection = request.form.get("budget_selection", "family")
            selected_budget = None
            scope = "family"
            if selection.startswith("budget:"):
                try:
                    selected_budget = db.session.get(Budget, int(selection.split(":", 1)[1]))
                except (TypeError, ValueError):
                    selected_budget = None
                if not selected_budget or not selected_budget.is_active:
                    raise ValueError("Choose a valid active budget.")
                scope = selected_budget.owner_type
                if selected_budget.category_id and not category_is_within(transaction.category_id, selected_budget.category_id):
                    raise ValueError("The selected budget is for a different category.")
            meta.budget_id = selected_budget.id if selected_budget else None
            meta.budget_scope = scope
            # Replace editable beneficiary allocations on an edited transaction.
            TransactionBeneficiary.query.filter_by(transaction_id=transaction.id).delete()
            selected_ids = []
            for raw in request.form.getlist("beneficiary_ids"):
                try:
                    member_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if member_id not in selected_ids:
                    selected_ids.append(member_id)
            valid_members = {m.id for m in active_members()}
            if any(member_id not in valid_members for member_id in selected_ids):
                raise ValueError("Choose active family members for the beneficiary split.")
            split_method = request.form.get("split_method", "equal")
            if split_method not in {"equal", "amount", "percentage"}:
                raise ValueError("Choose a valid beneficiary split method.")
            allocations = []
            if selected_ids and split_method == "equal":
                base, remainder = divmod(transaction.amount_paise, len(selected_ids))
                allocations = [base + (1 if idx < remainder else 0) for idx in range(len(selected_ids))]
            elif selected_ids and split_method == "amount":
                allocations = [money_to_paise(request.form.get(f"beneficiary_amount_{member_id}", "0"), allow_zero=True)
                               for member_id in selected_ids]
                if sum(allocations) != transaction.amount_paise:
                    raise ValueError("Beneficiary amounts must add up exactly to the transaction amount.")
            elif selected_ids and split_method == "percentage":
                percentages = []
                for member_id in selected_ids:
                    try:
                        pct = Decimal(request.form.get(f"beneficiary_percent_{member_id}", "0").strip())
                    except (InvalidOperation, ValueError):
                        raise ValueError("Enter a valid beneficiary percentage.") from None
                    if not pct.is_finite() or pct < 0 or pct > 100:
                        raise ValueError("Beneficiary percentages must be between 0 and 100.")
                    percentages.append(pct)
                if sum(percentages) != Decimal("100"):
                    raise ValueError("Beneficiary percentages must add up to exactly 100%.")
                allocations = [int((Decimal(transaction.amount_paise) * pct / Decimal("100")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP)) for pct in percentages]
                if allocations:
                    allocations[0] += transaction.amount_paise - sum(allocations)
            for member_id, amount in zip(selected_ids, allocations):
                db.session.add(TransactionBeneficiary(
                    transaction_id=transaction.id, member_id=member_id, amount_paise=amount,
                    split_method=split_method))
        else:
            meta.budget_id = None
            meta.budget_scope = "family"
            TransactionBeneficiary.query.filter_by(transaction_id=transaction.id).delete()

        # Apply a selected or merchant-matched cashback rule. Expected rewards do not
        # affect any account until approved in the Approvals tab.
        existing_event = CashbackEvent.query.filter_by(transaction_id=transaction.id).first()
        if transaction.kind == "expense":
            raw_scheme = request.form.get("cashback_scheme_id", "").strip()
            scheme = None
            if raw_scheme:
                try:
                    scheme = db.session.get(CashbackScheme, int(raw_scheme))
                except (TypeError, ValueError):
                    scheme = None
                if not scheme or not scheme.is_active or scheme.account_id != transaction.account_id:
                    raise ValueError("Choose an active cashback scheme for the selected account.")
            if not scheme:
                scheme = matching_scheme(transaction.account_id, transaction.description)
            raw_rate = request.form.get("cashback_rate", "").strip()
            raw_destination = request.form.get("cashback_destination_account_id", "").strip()
            raw_delay = request.form.get("cashback_delay_days", "").strip()
            rate_bps = None
            if raw_rate:
                try:
                    rate = Decimal(raw_rate)
                except InvalidOperation:
                    raise ValueError("Enter a valid cashback percentage.") from None
                if not rate.is_finite() or rate <= 0 or rate > 100:
                    raise ValueError("Cashback must be between 0.01% and 100%.")
                rate_bps = int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            destination_id = int(raw_destination) if raw_destination else None
            delay_days = int(raw_delay) if raw_delay else None
            if rate_bps is not None or destination_id is not None or scheme:
                if rate_bps is not None and destination_id is None and scheme is None:
                    raise ValueError("Choose a destination account for cashback.")
                add_cashback_event(transaction, scheme, rate_bps, destination_id, delay_days)
            elif existing_event and existing_event.status != "approved":
                existing_event.status = "cancelled"
        elif existing_event and existing_event.status != "approved":
            existing_event.status = "cancelled"

        # A transaction marked as recurring becomes the anchor record; later occurrences
        # are generated on their due date. Changing the template amount never rewrites history.
        if request.form.get("make_subscription") == "1" and not Subscription.query.filter_by(
            created_from_transaction_id=transaction.id).first():
            frequency = request.form.get("subscription_frequency", "monthly")
            if frequency not in {"weekly", "monthly", "annual", "every_n_weeks"}:
                raise ValueError("Choose a valid subscription frequency.")
            try:
                interval = int(request.form.get("subscription_interval_weeks", "6") or "6")
            except ValueError:
                raise ValueError("Enter a valid interval in weeks.") from None
            if frequency == "every_n_weeks" and not (1 <= interval <= 52):
                raise ValueError("The interval must be between 1 and 52 weeks.")
            if transaction.kind != "expense":
                raise ValueError("Only an expense can be set up as a subscription.")
            beneficiary_ids = [row.member_id for row in TransactionBeneficiary.query.filter_by(
                transaction_id=transaction.id).all()]
            sub_meta = db.session.get(TransactionMetadata, transaction.id)
            db.session.add(Subscription(
                name=transaction.description[:120], account_id=transaction.account_id,
                category_id=transaction.category_id, amount_paise=transaction.amount_paise,
                frequency=frequency, interval_weeks=interval,
                next_date=next_date_after(transaction.transaction_date, frequency, interval),
                notes=transaction.notes, budget_id=sub_meta.budget_id if sub_meta else None,
                budget_scope=sub_meta.budget_scope if sub_meta else "family",
                beneficiary_ids_json=json.dumps(beneficiary_ids),
                created_from_transaction_id=transaction.id))

    def transaction_form_context(item, accounts, categories):
        parents = {row.subcategory_id: row.parent for row in CategoryParent.query.all()}
        for category in categories:
            parent = parents.get(category.id)
            category.display_name = f"{parent.name} › {category.name}" if parent else category.name
        people = active_members()
        current_meta = db.session.get(TransactionMetadata, item.id) if item else None
        current_beneficiary_rows = TransactionBeneficiary.query.filter_by(transaction_id=item.id).all() if item else []
        selected_beneficiaries = {row.member_id: row for row in current_beneficiary_rows}
        budget_options = []
        for budget in Budget.query.filter_by(is_active=True).order_by(Budget.owner_type, Budget.name).all():
            owner = "Family" if budget.owner_type == "family" else (budget.member.name if budget.member else "Individual")
            category_label = f" · {budget.category.name}" if budget.category else ""
            budget_options.append({
                "value": f"budget:{budget.id}",
                "label": f"{owner}: {budget.name}{category_label} ({budget.period_type})",
                "budget": budget,
            })
        selected_budget_value = "family"
        if current_meta and current_meta.budget_id:
            selected_budget_value = f"budget:{current_meta.budget_id}"
        cashback_schemes = CashbackScheme.query.filter_by(is_active=True).order_by(CashbackScheme.name).all()
        item_account_id = item.account_id if item else None
        item_description = item.description if item else ""
        default_scheme = matching_scheme(item_account_id, item_description) if item_account_id else None
        event = CashbackEvent.query.filter_by(transaction_id=item.id).first() if item else None
        return {
            "people": people, "selected_beneficiaries": selected_beneficiaries,
            "split_method": current_beneficiary_rows[0].split_method if current_beneficiary_rows else "equal",
            "budget_options": budget_options, "selected_budget_value": selected_budget_value,
            "cashback_schemes": cashback_schemes, "default_cashback_scheme": default_scheme,
            "cashback_event": event, "purpose_tags": PURPOSE_TAGS,
            "current_member": current_member(),
        }

    def transaction_edit_guard(transaction_id):
        event = CashbackEvent.query.filter_by(transaction_id=transaction_id).first()
        if event and event.status == "approved":
            return "This purchase has an approved cashback credit. Restore or archive it as needed; editing is blocked to keep reward history consistent."
        posted_event = CashbackEvent.query.filter_by(posted_transaction_id=transaction_id).first()
        if posted_event:
            return "Posted cashback entries are managed from Approvals to preserve the reward audit trail."
        return None

    def on_transaction_archive(transaction, archived):
        event = CashbackEvent.query.filter_by(transaction_id=transaction.id).first()
        if event:
            if event.status == "approved" and event.posted_transaction:
                event.posted_transaction.is_archived = archived
            elif event.status != "approved":
                event.status = "cancelled" if archived else (
                    "pending_approval" if event.expected_date <= date.today() else "scheduled")
        # If the cashback credit itself is archived, keep it approved but inactive; restoring
        # the entry reactivates the original credit instead of creating a duplicate.

    def save_account_owner(account_id, owner_member_id, purpose_tags):
        account = db.session.get(Account, int(account_id))
        if not account:
            raise ValueError("Choose a valid account.")
        member_id = None
        if str(owner_member_id or "").strip():
            try:
                member_id = int(owner_member_id)
            except ValueError:
                raise ValueError("Choose a valid account owner.") from None
            if not db.session.get(FamilyMember, member_id) or not db.session.get(FamilyMember, member_id).is_active:
                raise ValueError("Choose an active family member as account owner.")
        owner = db.session.get(AccountOwner, account.id)
        if owner is None:
            owner = AccountOwner(account_id=account.id)
            db.session.add(owner)
        owner.member_id = member_id
        AccountPurposeTag.query.filter_by(account_id=account.id).delete()
        seen = set()
        for tag in purpose_tags:
            tag = (tag or "").strip().lower()
            if tag in PURPOSE_TAGS and tag not in seen:
                db.session.add(AccountPurposeTag(account_id=account.id, tag=tag))
                seen.add(tag)

    def enrich_account_rows(rows):
        owners = {row.account_id: row for row in AccountOwner.query.all()}
        tags = {}
        for row in AccountPurposeTag.query.all():
            tags.setdefault(row.account_id, []).append(row.tag)
        output = []
        for row in rows:
            owner = owners.get(row["account"].id)
            output.append({**row, "owner": owner.member if owner and owner.member_id else None,
                           "is_family_account": not owner or owner.member_id is None,
                           "tags": tags.get(row["account"].id, [])})
        return output

    def enrich_categories(rows):
        parents = {row.subcategory_id: row.parent for row in CategoryParent.query.all()}
        for row in rows:
            row.display_name = f"{parents[row.id].name} › {row.name}" if row.id in parents else row.name
            row.parent_name = parents[row.id].name if row.id in parents else ""
            row.is_subcategory = row.id in parents
        return rows

    def save_category(name, kind, colour, parent_id=None):
        if not name:
            raise ValueError("Enter a category name.")
        if kind not in {"income", "expense"}:
            raise ValueError("Choose income or expense.")
        if Category.query.filter(func.lower(Category.name) == name.lower()).first():
            raise ValueError("That category already exists.")
        parent = db.session.get(Category, int(parent_id)) if parent_id else None
        if parent_id and (not parent or parent.kind != kind):
            raise ValueError("Choose a parent category of the same type.")
        category = Category(name=name[:80], kind=kind, icon="circle", colour=normalized_colour(colour))
        db.session.add(category)
        db.session.flush()
        if parent:
            db.session.add(CategoryParent(subcategory_id=category.id, parent_category_id=parent.id))
        return category

    def due_subscriptions():
        if request.method != "GET" or request.endpoint in {"login", "setup", "static"}:
            return
        if not current_app.config.get("TESTING") and not session.get("member_id"):
            return
        if FamilyMember.query.filter_by(is_active=True).count() == 0:
            return
        changed = False
        today = date.today()
        for subscription in Subscription.query.filter_by(is_active=True).filter(
            Subscription.next_date <= today).order_by(Subscription.next_date).limit(100).all():
            iterations = 0
            while subscription.is_active and subscription.next_date <= today and iterations < 120:
                occurrence_date = subscription.next_date
                txn = Transaction(
                    transaction_date=occurrence_date, kind="expense",
                    amount_paise=subscription.amount_paise, description=subscription.name[:180],
                    account_id=subscription.account_id, category_id=subscription.category_id,
                    notes=(subscription.notes or "")[:500],
                )
                db.session.add(txn)
                db.session.flush()
                db.session.add(SubscriptionOccurrence(
                    subscription_id=subscription.id, transaction_id=txn.id, occurrence_date=occurrence_date))
                db.session.add(TransactionMetadata(
                    transaction_id=txn.id, budget_id=subscription.budget_id,
                    budget_scope=subscription.budget_scope, source="subscription"))
                member_ids = []
                try:
                    member_ids = [int(value) for value in json.loads(subscription.beneficiary_ids_json or "[]")]
                except (ValueError, TypeError):
                    member_ids = []
                member_ids = [member_id for member_id in member_ids
                              if db.session.get(FamilyMember, member_id) and db.session.get(FamilyMember, member_id).is_active]
                if member_ids:
                    base, remainder = divmod(txn.amount_paise, len(member_ids))
                    for idx, member_id in enumerate(member_ids):
                        db.session.add(TransactionBeneficiary(
                            transaction_id=txn.id, member_id=member_id,
                            amount_paise=base + (1 if idx < remainder else 0), split_method="equal"))
                scheme = matching_scheme(txn.account_id, txn.description)
                if scheme:
                    add_cashback_event(txn, scheme)
                subscription.next_date = next_date_after(occurrence_date, subscription.frequency, subscription.interval_weeks)
                iterations += 1
                changed = True
        if changed:
            db.session.commit()

    def create_first_admin():
        if FamilyMember.query.filter_by(is_active=True).count():
            return redirect(url_for("login"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()[:100]
            email = request.form.get("email", "").strip().lower()[:254]
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not name or not email or "@" not in email:
                flash("Enter your name and a valid email address.", "error")
            elif len(password) < 10:
                flash("Use a password with at least 10 characters.", "error")
            elif password != confirm:
                flash("The passwords do not match.", "error")
            else:
                member = FamilyMember(name=name, email=email, password_hash=generate_password_hash(password), role="admin")
                db.session.add(member)
                db.session.flush()
                # Existing household accounts belong to the first administrator until reassigned.
                for account in Account.query.all():
                    if not db.session.get(AccountOwner, account.id):
                        db.session.add(AccountOwner(account_id=account.id, member_id=member.id))
                db.session.commit()
                session.clear()
                session["member_id"] = member.id
                session["_csrf_token"] = __import__("secrets").token_urlsafe(32)
                flash("Family workspace created. Add other members from People.", "success")
                return redirect(url_for("dashboard"))
        return render_template("setup.html")

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        return create_first_admin()

    @app.route("/people", methods=["GET", "POST"])
    @login_required
    @admin_required
    def people():
        if request.method == "POST":
            action = request.form.get("action", "add_member")
            if action == "deactivate":
                try:
                    target_id = int(request.form.get("member_id", ""))
                except ValueError:
                    flash("Choose a valid member.", "error")
                    return redirect(url_for("people"))
                target = db.session.get(FamilyMember, target_id)
                if not target:
                    flash("Member not found.", "error")
                elif current_member() and target.id == current_member().id:
                    flash("You cannot deactivate your own login.", "error")
                elif target.role == "admin" and FamilyMember.query.filter_by(role="admin", is_active=True).count() <= 1:
                    flash("The family must retain at least one active admin.", "error")
                else:
                    target.is_active = False
                    db.session.commit()
                    flash("Member login deactivated; historic attributions remain.", "success")
                    return redirect(url_for("people"))
            else:
                name = request.form.get("name", "").strip()[:100]
                email = request.form.get("email", "").strip().lower()[:254]
                password = request.form.get("password", "")
                role = request.form.get("role", "member")
                if not name or "@" not in email:
                    flash("Enter a name and valid email address.", "error")
                elif len(password) < 10:
                    flash("Use a password with at least 10 characters.", "error")
                elif role not in {"member", "admin"}:
                    flash("Choose a valid role.", "error")
                elif FamilyMember.query.filter(func.lower(FamilyMember.email) == email).first():
                    flash("That email already has a family login.", "error")
                else:
                    db.session.add(FamilyMember(name=name, email=email,
                        password_hash=generate_password_hash(password), role=role))
                    db.session.commit()
                    flash("Family login created. Share the initial password securely.", "success")
                    return redirect(url_for("people"))
        return render_template("people.html", people=FamilyMember.query.order_by(FamilyMember.is_active.desc(), FamilyMember.name).all(),
                               current_member=current_member())

    @app.route("/budgets", methods=["GET", "POST"])
    @login_required
    def budgets():
        if request.method == "POST":
            name = request.form.get("name", "").strip()[:100]
            owner_type = request.form.get("owner_type", "family")
            member_id_raw = request.form.get("member_id", "").strip()
            period_type = request.form.get("period_type", "monthly")
            category_raw = request.form.get("category_id", "").strip()
            try:
                amount = money_to_paise(request.form.get("amount", ""))
                start_date_raw = request.form.get("start_date", "")
                start_date = date.fromisoformat(start_date_raw) if start_date_raw else date.today().replace(day=1)
                member_id = int(member_id_raw) if member_id_raw else None
                category_id = int(category_raw) if category_raw else None
            except (ValueError, TypeError):
                flash("Check the budget amount, date and selections.", "error")
                return redirect(url_for("budgets"))
            if not name:
                flash("Enter a budget name.", "error")
            elif owner_type not in {"family", "individual"} or period_type not in {"monthly", "annual"}:
                flash("Choose a valid budget owner and period.", "error")
            elif owner_type == "individual" and (not member_id or not db.session.get(FamilyMember, member_id) or not db.session.get(FamilyMember, member_id).is_active):
                flash("Choose an active member for an individual budget.", "error")
            elif category_id and (not db.session.get(Category, category_id) or db.session.get(Category, category_id).kind != "expense"):
                flash("Choose an expense category.", "error")
            else:
                if owner_type == "family":
                    member_id = None
                period_start = start_date.replace(month=1, day=1) if period_type == "annual" else start_date.replace(day=1)
                budget = Budget(name=name, owner_type=owner_type, member_id=member_id, category_id=category_id,
                    period_type=period_type, amount_paise=amount, spread_monthly=request.form.get("spread_monthly") == "1",
                    start_date=period_start)
                db.session.add(budget)
                db.session.commit()
                flash("Budget created.", "success")
                return redirect(url_for("budgets"))
        budget_rows = []
        for budget in Budget.query.filter_by(is_active=True).order_by(Budget.owner_type, Budget.name).all():
            row = budget_metrics(budget)
            row["owner_label"] = "Family" if budget.owner_type == "family" else (budget.member.name if budget.member else "Individual")
            budget_rows.append(row)
        return render_template("budgets.html", budget_rows=budget_rows, people=active_members(),
            categories=Category.query.filter_by(kind="expense").order_by(Category.name).all(),
            today=date.today(), month_start=date.today().replace(day=1).isoformat())

    @app.post("/budgets/<int:budget_id>/toggle")
    @login_required
    @admin_required
    def toggle_budget(budget_id):
        budget = db.session.get(Budget, budget_id) or abort(404)
        budget.is_active = not budget.is_active
        db.session.commit()
        flash("Budget status updated.", "success")
        return redirect(url_for("budgets"))

    @app.route("/goals", methods=["GET", "POST"])
    @login_required
    def goals():
        if request.method == "POST":
            action = request.form.get("action", "create")
            if action == "earmark":
                try:
                    goal_id = int(request.form.get("goal_id", ""))
                    account_id = int(request.form.get("account_id", ""))
                    amount = money_to_paise(request.form.get("amount", ""), allow_zero=True)
                except (ValueError, TypeError):
                    flash("Check the account and earmark amount.", "error")
                    return redirect(url_for("goals"))
                goal = db.session.get(Goal, goal_id)
                account = db.session.get(Account, account_id)
                if not goal or not account:
                    flash("Choose a valid goal and account.", "error")
                elif account.account_type == "credit":
                    flash("A goal can only earmark money held in an asset account, not credit-card debt.", "error")
                else:
                    earmark = GoalEarmark.query.filter_by(goal_id=goal.id, account_id=account.id).first()
                    other_total = db.session.query(func.coalesce(func.sum(GoalEarmark.amount_paise), 0)).filter(
                        GoalEarmark.account_id == account.id, GoalEarmark.goal_id != goal.id).scalar()
                    if amount + int(other_total or 0) > max(account_balance(account), 0):
                        flash("Total goal earmarks cannot exceed the account's available balance after existing earmarks.", "error")
                    else:
                        if amount == 0:
                            if earmark:
                                db.session.delete(earmark)
                        elif earmark:
                            earmark.amount_paise = amount
                        else:
                            db.session.add(GoalEarmark(goal_id=goal.id, account_id=account.id, amount_paise=amount))
                        db.session.commit()
                        flash("Goal earmark saved. No account movement was created.", "success")
                        return redirect(url_for("goals"))
            else:
                name = request.form.get("name", "").strip()[:120]
                owner_type = request.form.get("owner_type", "family")
                member_raw = request.form.get("member_id", "").strip()
                target_date_raw = request.form.get("target_date", "").strip()
                try:
                    target = money_to_paise(request.form.get("target_amount", ""))
                    member_id = int(member_raw) if member_raw else None
                    target_date = date.fromisoformat(target_date_raw) if target_date_raw else None
                except (ValueError, TypeError):
                    flash("Check the target amount, member and date.", "error")
                    return redirect(url_for("goals"))
                if not name or owner_type not in {"family", "individual"}:
                    flash("Enter a goal name and valid owner type.", "error")
                elif owner_type == "individual" and (not member_id or not db.session.get(FamilyMember, member_id) or not db.session.get(FamilyMember, member_id).is_active):
                    flash("Choose an active member for an individual goal.", "error")
                else:
                    db.session.add(Goal(name=name, owner_type=owner_type,
                        member_id=member_id if owner_type == "individual" else None,
                        target_paise=target, target_date=target_date))
                    db.session.commit()
                    flash("Goal created.", "success")
                    return redirect(url_for("goals"))
        goal_rows = []
        for goal in Goal.query.filter_by(is_active=True).order_by(Goal.target_date.is_(None), Goal.target_date, Goal.name).all():
            earmarks = GoalEarmark.query.filter_by(goal_id=goal.id).all()
            saved = sum(row.amount_paise for row in earmarks)
            goal_rows.append({"goal": goal, "saved": saved, "remaining": max(goal.target_paise - saved, 0),
                "progress": min(100, round(saved * 100 / goal.target_paise)) if goal.target_paise else 0,
                "earmarks": earmarks, "owner_label": "Family" if goal.owner_type == "family" else (goal.member.name if goal.member else "Individual")})
        return render_template("goals.html", goal_rows=goal_rows, people=active_members(),
                               accounts=Account.query.filter(Account.account_type != "credit").order_by(Account.name).all())

    @app.post("/goals/<int:goal_id>/toggle")
    @login_required
    @admin_required
    def toggle_goal(goal_id):
        goal = db.session.get(Goal, goal_id) or abort(404)
        goal.is_active = not goal.is_active
        db.session.commit()
        flash("Goal status updated.", "success")
        return redirect(url_for("goals"))

    @app.route("/subscriptions", methods=["GET", "POST"])
    @login_required
    def subscriptions():
        if request.method == "POST":
            action = request.form.get("action", "create")
            try:
                if action == "update":
                    subscription = db.session.get(Subscription, int(request.form.get("subscription_id", "")))
                    if not subscription:
                        raise ValueError("Subscription not found.")
                    subscription.amount_paise = money_to_paise(request.form.get("amount", ""))
                    subscription.next_date = date.fromisoformat(request.form.get("next_date", ""))
                    subscription.is_active = request.form.get("is_active") == "1"
                    db.session.commit()
                    flash("Subscription updated. Only future occurrences use the new amount; historical transactions are unchanged.", "success")
                    return redirect(url_for("subscriptions"))
                name = request.form.get("name", "").strip()[:120]
                account_id = int(request.form.get("account_id", ""))
                category_raw = request.form.get("category_id", "").strip()
                category_id = int(category_raw) if category_raw else None
                amount = money_to_paise(request.form.get("amount", ""))
                frequency = request.form.get("frequency", "monthly")
                interval = int(request.form.get("interval_weeks", "6") or "6")
                next_due = date.fromisoformat(request.form.get("next_date", ""))
                budget_selection = request.form.get("budget_selection", "family")
                budget = None
                budget_scope = "family"
                if budget_selection.startswith("budget:"):
                    budget = db.session.get(Budget, int(budget_selection.split(":", 1)[1]))
                    if not budget or not budget.is_active:
                        raise ValueError("Choose an active budget.")
                    budget_scope = budget.owner_type
                if frequency not in {"weekly", "monthly", "annual", "every_n_weeks"}:
                    raise ValueError("Choose a valid subscription frequency.")
                if frequency == "every_n_weeks" and not (1 <= interval <= 52):
                    raise ValueError("The interval must be between 1 and 52 weeks.")
                account = db.session.get(Account, account_id)
                if not account:
                    raise ValueError("Choose a valid account.")
                if category_id and (not db.session.get(Category, category_id) or db.session.get(Category, category_id).kind != "expense"):
                    raise ValueError("Choose a valid expense category.")
                db.session.add(Subscription(name=name, account_id=account_id, category_id=category_id,
                    amount_paise=amount, frequency=frequency, interval_weeks=interval, next_date=next_due,
                    is_active=True, notes=request.form.get("notes", "").strip()[:500],
                    budget_id=budget.id if budget else None, budget_scope=budget_scope,
                    beneficiary_ids_json="[]"))
                db.session.commit()
                flash("Subscription created. Transactions will be generated on their due dates when the app is opened.", "success")
                return redirect(url_for("subscriptions"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check subscription details.", "error")
                return redirect(url_for("subscriptions"))
        rows = Subscription.query.order_by(Subscription.is_active.desc(), Subscription.next_date, Subscription.name).all()
        options = []
        for budget in Budget.query.filter_by(is_active=True).order_by(Budget.owner_type, Budget.name).all():
            owner = "Family" if budget.owner_type == "family" else (budget.member.name if budget.member else "Individual")
            options.append({"value": f"budget:{budget.id}", "label": f"{owner}: {budget.name}"})
        return render_template("subscriptions.html", subscriptions=rows,
            accounts=Account.query.order_by(Account.name).all(),
            categories=Category.query.filter_by(kind="expense").order_by(Category.name).all(),
            budget_options=options, today=date.today().isoformat())

    @app.route("/cashback-schemes", methods=["GET", "POST"])
    @login_required
    @admin_required
    def cashback_schemes():
        if request.method == "POST":
            if request.form.get("action") == "toggle":
                scheme = db.session.get(CashbackScheme, int(request.form.get("scheme_id", "")))
                if not scheme:
                    flash("Scheme not found.", "error")
                else:
                    scheme.is_active = not scheme.is_active
                    db.session.commit()
                    flash("Cashback scheme status updated.", "success")
                return redirect(url_for("cashback_schemes"))
            name = request.form.get("name", "").strip()[:100]
            keyword = request.form.get("merchant_keyword", "").strip()[:100]
            try:
                account_id = int(request.form.get("account_id", ""))
                destination_id = int(request.form.get("destination_account_id", ""))
                rate = Decimal(request.form.get("rate_percent", "").strip())
                delay = int(request.form.get("delay_days", "0") or "0")
                rate_bps = int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            except (ValueError, TypeError, InvalidOperation):
                flash("Check the cashback rate, destination and delay.", "error")
                return redirect(url_for("cashback_schemes"))
            account = db.session.get(Account, account_id)
            destination = db.session.get(Account, destination_id)
            if not name or not keyword or not account or account.account_type != "credit" or not destination:
                flash("Choose a name, merchant keyword, credit-card account and destination.", "error")
            elif not rate.is_finite() or rate <= 0 or rate > 100 or not (0 <= delay <= 365):
                flash("Rate must be 0.01–100%; delay must be 0–365 days.", "error")
            else:
                db.session.add(CashbackScheme(name=name, account_id=account.id, merchant_keyword=keyword,
                    rate_bps=rate_bps, destination_account_id=destination.id, delay_days=delay,
                    notes=request.form.get("notes", "").strip()[:300]))
                db.session.commit()
                flash("Cashback scheme saved.", "success")
                return redirect(url_for("cashback_schemes"))
        schemes = CashbackScheme.query.order_by(CashbackScheme.is_active.desc(), CashbackScheme.name).all()
        return render_template("cashback_schemes.html", schemes=schemes,
            cards=Account.query.filter_by(account_type="credit").order_by(Account.name).all(),
            accounts=Account.query.order_by(Account.name).all())

    @app.route("/approvals", methods=["GET", "POST"])
    @login_required
    def approvals():
        if request.method == "POST":
            try:
                event_id = int(request.form.get("event_id", ""))
            except ValueError:
                flash("Choose a valid cashback item.", "error")
                return redirect(url_for("approvals"))
            event = db.session.get(CashbackEvent, event_id)
            if not event:
                flash("Cashback item not found.", "error")
                return redirect(url_for("approvals"))
            action = request.form.get("action", "approve")
            if action == "not_received":
                if event.status not in {"pending_approval", "not_received"}:
                    flash("Only due cashback can be marked as not received.", "error")
                else:
                    event.status = "not_received"
                    event.notes = "Marked not received; can be reviewed again."
                    db.session.commit()
                    flash("Cashback left unposted. It can be reviewed again later.", "success")
                return redirect(url_for("approvals"))
            if event.status == "approved" or event.posted_transaction_id:
                flash("This cashback has already been posted.", "error")
                return redirect(url_for("approvals"))
            if event.expected_date > date.today():
                flash("This cashback is not due for approval yet.", "error")
                return redirect(url_for("approvals"))
            try:
                actual_amount = money_to_paise(request.form.get("actual_amount", ""), allow_zero=False)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("approvals"))
            destination = db.session.get(Account, event.destination_account_id)
            if not destination:
                flash("The destination account no longer exists.", "error")
                return redirect(url_for("approvals"))
            source_txn = db.session.get(Transaction, event.transaction_id)
            if not source_txn or source_txn.is_archived:
                flash("The source purchase is archived. Restore it before approving cashback.", "error")
                return redirect(url_for("approvals"))
            other_income = Category.query.filter_by(name="Other income", kind="income").first()
            posted = Transaction(
                transaction_date=date.today(), kind="income", amount_paise=actual_amount,
                description=("Cashback · " + source_txn.description)[:180],
                account_id=destination.id, category_id=other_income.id if other_income else None,
                notes=f"Approved reward for transaction #{source_txn.id}; expected {format_money(event.expected_paise)}.",
            )
            db.session.add(posted)
            db.session.flush()
            db.session.add(TransactionMetadata(
                transaction_id=posted.id, source="cashback", budget_scope="family"))
            event.actual_paise = actual_amount
            event.status = "approved"
            event.posted_transaction_id = posted.id
            event.notes = "Approved and posted to destination account."
            db.session.commit()
            flash("Cashback approved and credited to the destination account.", "success")
            return redirect(url_for("approvals"))
        due_events = CashbackEvent.query.filter(
            CashbackEvent.status.in_(["scheduled", "pending_approval", "not_received"]),
            CashbackEvent.expected_date <= date.today()
        ).order_by(CashbackEvent.expected_date, CashbackEvent.id).all()
        for event in due_events:
            if event.status == "scheduled":
                event.status = "pending_approval"
        if due_events:
            db.session.commit()
        events = []
        for event in due_events:
            source_txn = db.session.get(Transaction, event.transaction_id)
            if not source_txn or source_txn.is_archived:
                continue
            events.append({"event": event, "source": source_txn})
        upcoming = CashbackEvent.query.filter_by(status="scheduled").filter(
            CashbackEvent.expected_date > date.today()
        ).order_by(CashbackEvent.expected_date).limit(10).all()
        return render_template("approvals.html", events=events, upcoming=upcoming)

    @app.before_request
    def generate_due_subscription_transactions():
        try:
            due_subscriptions()
        except Exception:
            # A recurring template should never make a page unavailable. The database
            # exception is rolled back and surfaced through CI/logging for investigation.
            db.session.rollback()
            current_app.logger.exception("Unable to materialize due subscription transactions")

    app.extensions["family_finance"] = {
        "active_members": active_members,
        "save_transaction_extras": save_transaction_extras,
        "transaction_form_context": transaction_form_context,
        "transaction_edit_guard": transaction_edit_guard,
        "on_transaction_archive": on_transaction_archive,
        "save_account_owner": save_account_owner,
        "enrich_account_rows": enrich_account_rows,
        "enrich_categories": enrich_categories,
        "save_category": save_category,
        "budget_metrics": budget_metrics,
        "matching_scheme": matching_scheme,
        "add_cashback_event": add_cashback_event,
    }
