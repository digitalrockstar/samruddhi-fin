from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from typing import Any

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_

db = SQLAlchemy()

DEFAULT_CATEGORIES = [
    ("Groceries", "expense", "basket", "#80b895"),
    ("Dining", "expense", "utensils", "#e5a071"),
    ("Transport", "expense", "car", "#7297d2"),
    ("Home", "expense", "house", "#a28ac7"),
    ("Bills", "expense", "receipt", "#d5b45f"),
    ("Health", "expense", "heart", "#dc91a6"),
    ("Shopping", "expense", "bag", "#70afb0"),
    ("Other expense", "expense", "more", "#9da8a0"),
    ("Salary", "income", "wallet", "#80b895"),
    ("Other income", "income", "trend", "#7297d2"),
]
ACCOUNT_TYPES = {"cash", "bank", "savings", "credit", "investment", "other"}
KINDS = {"income", "expense", "transfer"}
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def utcnow_naive() -> datetime:
    """Return UTC time as a naive value for consistent SQLite DateTime round-trips."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    account_type = db.Column(db.String(20), nullable=False, default="bank")
    opening_balance_paise = db.Column(db.Integer, nullable=False, default=0)
    colour = db.Column(db.String(7), nullable=False, default="#7297d2")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    icon = db.Column(db.String(24), nullable=False, default="circle")
    colour = db.Column(db.String(7), nullable=False, default="#7297d2")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    kind = db.Column(db.String(12), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(180), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    transfer_to_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True, index=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    account = db.relationship("Account", foreign_keys=[account_id])
    transfer_to = db.relationship("Account", foreign_keys=[transfer_to_account_id])
    category = db.relationship("Category")


class ImportBatch(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    rows_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    consumed = db.Column(db.Boolean, nullable=False, default=False)


def money_to_paise(value: Any, *, allow_zero: bool = False) -> int:
    """Parse currency without binary floats and store the result as integer paise."""
    try:
        amount = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("Amount must be greater than zero." if not allow_zero else "Amount cannot be negative.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(paise: int | None) -> str:
    value = int(paise or 0)
    sign = "−" if value < 0 else ""
    value = abs(value)
    return f"{sign}₹{value // 100:,}.{value % 100:02d}"


def month_bounds(month_text: str | None) -> tuple[date, date, str]:
    try:
        start = datetime.strptime(month_text or date.today().strftime("%Y-%m"), "%Y-%m").date().replace(day=1)
    except ValueError:
        start = date.today().replace(day=1)
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end, start.strftime("%Y-%m")


def normalized_colour(value: str | None) -> str:
    colour = (value or "").strip()
    return colour if COLOUR_RE.fullmatch(colour) else "#7297d2"


def seed_defaults() -> None:
    if not Category.query.first():
        db.session.add_all([
            Category(name=name, kind=kind, icon=icon, colour=colour)
            for name, kind, icon, colour in DEFAULT_CATEGORIES
        ])
    if not Account.query.first():
        db.session.add_all([
            Account(name="Cash", account_type="cash", colour="#80b895"),
            Account(name="Main bank", account_type="bank", colour="#7297d2"),
            Account(name="Savings", account_type="savings", colour="#a28ac7"),
            Account(name="Credit card", account_type="credit", colour="#e5a071"),
        ])
    db.session.commit()


def make_login_required():
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_password() and not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_password() -> str:
    from flask import current_app
    return str(current_app.config.get("APP_PASSWORD") or os.environ.get("APP_PASSWORD") or "")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-only-change-before-hosting"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///samruddhi.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
    )
    if test_config:
        app.config.update(test_config)

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace("postgres://", "postgresql+psycopg://", 1)
    elif uri.startswith("postgresql://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace("postgresql://", "postgresql+psycopg://", 1)

    db.init_app(app)
    with app.app_context():
        db.create_all()
        seed_defaults()

    login_required = make_login_required()

    @app.context_processor
    def inject_template_helpers():
        def csrf_token() -> str:
            token = session.get("_csrf_token")
            if not token:
                token = secrets.token_urlsafe(32)
                session["_csrf_token"] = token
            return token
        return {"money": format_money, "today": date.today().isoformat(),
                "auth_enabled": bool(current_password()), "csrf_token": csrf_token}

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            expected = session.get("_csrf_token", "")
            provided = request.form.get("_csrf_token", "")
            if not expected or not provided or not secrets.compare_digest(expected, provided):
                abort(400, description="The form has expired. Refresh the page and try again.")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        password = current_password()
        if not password:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            if secrets.compare_digest(request.form.get("password", ""), password):
                session.clear()
                session["authenticated"] = True
                session["_csrf_token"] = secrets.token_urlsafe(32)
                target = request.args.get("next", "")
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("dashboard")
                return redirect(target)
            flash("That password did not match.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login") if current_password() else url_for("dashboard"))

    @app.get("/")
    @login_required
    def dashboard():
        start, end, month = month_bounds(request.args.get("month"))
        income = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).filter(
            Transaction.kind == "income", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).scalar()
        expenses = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).filter(
            Transaction.kind == "expense", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).scalar()
        recent = Transaction.query.filter(Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).order_by(
            Transaction.transaction_date.desc(), Transaction.id.desc()).limit(7).all()
        category_rows = db.session.query(Category.name, Category.colour,
            func.sum(Transaction.amount_paise)).join(Transaction, Transaction.category_id == Category.id).filter(
            Transaction.kind == "expense", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).group_by(Category.id).order_by(
            func.sum(Transaction.amount_paise).desc()).limit(6).all()
        expense_total = int(expenses or 0)
        breakdown = [{"name": name, "colour": colour, "amount": int(value or 0),
            "share": round(int(value or 0) * 100 / expense_total) if expense_total else 0}
            for name, colour, value in category_rows]
        accounts = Account.query.order_by(Account.name).all()
        return render_template("dashboard.html", month=month, income=int(income or 0),
            expenses=expense_total, net=int(income or 0) - expense_total,
            recent=recent, breakdown=breakdown, accounts=accounts)

    @app.get("/transactions")
    @login_required
    def transactions():
        start, end, month = month_bounds(request.args.get("month"))
        show_archived = request.args.get("archived") == "1"
        query = Transaction.query.filter(Transaction.is_archived.is_(show_archived), Transaction.transaction_date >= start, Transaction.transaction_date < end)
        kind = request.args.get("kind", "")
        search = request.args.get("q", "").strip()[:100]
        if kind in KINDS:
            query = query.filter(Transaction.kind == kind)
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(Transaction.description.ilike(pattern), Transaction.notes.ilike(pattern)))
        items = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).all()
        return render_template("transactions.html", items=items, month=month, kind=kind, q=search, archived=show_archived)

    def save_transaction(item: Transaction | None):
        accounts = Account.query.order_by(Account.name).all()
        categories = Category.query.order_by(Category.kind, Category.name).all()
        if not accounts:
            flash("Create an account before adding a transaction.", "error")
            return redirect(url_for("accounts"))
        if request.method == "POST":
            try:
                kind = request.form.get("kind", "expense")
                if kind not in KINDS:
                    raise ValueError("Choose income, expense or transfer.")
                amount = money_to_paise(request.form.get("amount", ""))
                description = request.form.get("description", "").strip()
                if not description:
                    raise ValueError("Add a description.")
                txn_date = date.fromisoformat(request.form.get("transaction_date", ""))
                account_id = int(request.form.get("account_id", ""))
                if not db.session.get(Account, account_id):
                    raise ValueError("Choose a valid account.")
                category_id = None
                target_id = None
                if kind == "transfer":
                    target_id = int(request.form.get("transfer_to_account_id", ""))
                    if target_id == account_id or not db.session.get(Account, target_id):
                        raise ValueError("Choose a different destination account.")
                else:
                    raw_category = request.form.get("category_id", "").strip()
                    category_id = int(raw_category) if raw_category else None
                    if category_id:
                        category = db.session.get(Category, category_id)
                        if not category or category.kind != kind:
                            raise ValueError("Choose a category matching the transaction type.")
                record = item or Transaction()
                record.kind = kind
                record.amount_paise = amount
                record.description = description[:180]
                record.transaction_date = txn_date
                record.account_id = account_id
                record.category_id = category_id
                record.transfer_to_account_id = target_id
                record.notes = request.form.get("notes", "").strip()[:500]
                db.session.add(record)
                db.session.commit()
                flash("Transaction saved.", "success")
                return redirect(url_for("transactions"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check the transaction details.", "error")
        return render_template("transaction_form.html", item=item, accounts=accounts, categories=categories)

    @app.route("/transactions/new", methods=["GET", "POST"])
    @login_required
    def transaction_new():
        return save_transaction(None)

    @app.route("/transactions/<int:transaction_id>/edit", methods=["GET", "POST"])
    @login_required
    def transaction_edit(transaction_id: int):
        return save_transaction(db.get_or_404(Transaction, transaction_id))

    @app.post("/transactions/<int:transaction_id>/archive")
    @login_required
    def transaction_archive(transaction_id: int):
        item = db.get_or_404(Transaction, transaction_id)
        item.is_archived = True
        db.session.commit()
        flash("Transaction archived. You can restore it from Archived transactions.", "success")
        return redirect(url_for("transactions"))

    @app.post("/transactions/<int:transaction_id>/restore")
    @login_required
    def transaction_restore(transaction_id: int):
        item = db.get_or_404(Transaction, transaction_id)
        item.is_archived = False
        db.session.commit()
        flash("Transaction restored.", "success")
        return redirect(url_for("transactions", archived="1"))

    def balances_for_accounts(accounts: list[Account]) -> list[dict[str, Any]]:
        output = []
        for account in accounts:
            txns = Transaction.query.filter(Transaction.is_archived.is_(False), or_(Transaction.account_id == account.id,
                Transaction.transfer_to_account_id == account.id)).all()
            balance = account.opening_balance_paise
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
            output.append({"account": account, "balance": balance, "count": len(txns)})
        return output

    @app.route("/accounts", methods=["GET", "POST"])
    @login_required
    def accounts():
        if request.method == "POST":
            try:
                name = request.form.get("name", "").strip()
                kind = request.form.get("account_type", "bank")
                opening_raw = request.form.get("opening_balance", "0").strip() or "0"
                opening = money_to_paise(opening_raw, allow_zero=True)
                if not name:
                    raise ValueError("Enter an account name.")
                if kind not in ACCOUNT_TYPES:
                    raise ValueError("Choose a valid account type.")
                if Account.query.filter(func.lower(Account.name) == name.lower()).first():
                    raise ValueError("An account with that name already exists.")
                db.session.add(Account(name=name[:80], account_type=kind,
                    opening_balance_paise=opening, colour=normalized_colour(request.form.get("colour"))))
                db.session.commit()
                flash("Account added.", "success")
                return redirect(url_for("accounts"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check the account details.", "error")
        account_list = Account.query.order_by(Account.name).all()
        return render_template("accounts.html", rows=balances_for_accounts(account_list))

    @app.route("/categories", methods=["GET", "POST"])
    @login_required
    def categories():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            kind = request.form.get("kind", "expense")
            if not name:
                flash("Enter a category name.", "error")
            elif kind not in ("income", "expense"):
                flash("Choose income or expense.", "error")
            elif Category.query.filter(func.lower(Category.name) == name.lower()).first():
                flash("That category already exists.", "error")
            else:
                db.session.add(Category(name=name[:80], kind=kind, icon="circle",
                    colour=normalized_colour(request.form.get("colour"))))
                db.session.commit()
                flash("Category added.", "success")
                return redirect(url_for("categories"))
        rows = Category.query.order_by(Category.kind, Category.name).all()
        return render_template("categories.html", categories=rows)

    @app.get("/export.csv")
    @login_required
    def export_csv():
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["date", "type", "amount", "description", "account", "category", "transfer_to", "notes"])
        for txn in Transaction.query.filter(Transaction.is_archived.is_(False)).order_by(Transaction.transaction_date, Transaction.id).all():
            writer.writerow([txn.transaction_date.isoformat(), txn.kind,
                f"{txn.amount_paise // 100}.{txn.amount_paise % 100:02d}", txn.description,
                txn.account.name, txn.category.name if txn.category else "",
                txn.transfer_to.name if txn.transfer_to else "", txn.notes])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="samruddhi-transactions.csv"'})

    def duplicate_exists(row: dict[str, Any]) -> bool:
        return db.session.query(Transaction.id).join(Account, Transaction.account_id == Account.id).filter(
            Transaction.transaction_date == date.fromisoformat(row["date"]),
            Transaction.kind == row["type"],
            Transaction.amount_paise == row["amount_paise"],
            func.lower(Transaction.description) == row["description"].lower(),
            func.lower(Account.name) == row["account"].lower()).first() is not None

    @app.route("/import", methods=["GET", "POST"])
    @login_required
    def import_csv():
        if request.method == "POST" and request.form.get("action") == "confirm":
            batch_id = session.pop("import_batch_id", None)
            batch = db.session.get(ImportBatch, batch_id) if batch_id else None
            if not batch or batch.consumed or utcnow_naive() - batch.created_at > timedelta(hours=1):
                flash("That preview expired. Please upload the CSV again.", "error")
                return redirect(url_for("import_csv"))
            rows = json.loads(batch.rows_json)
            saved = 0
            for row in rows:
                if row.get("error") or duplicate_exists(row):
                    continue
                db.session.add(Transaction(transaction_date=date.fromisoformat(row["date"]),
                    kind=row["type"], amount_paise=int(row["amount_paise"]), description=row["description"],
                    account_id=int(row["account_id"]), category_id=row.get("category_id"),
                    transfer_to_account_id=row.get("transfer_to_account_id"), notes=row.get("notes", "")))
                saved += 1
            batch.consumed = True
            db.session.commit()
            flash(f"Imported {saved} transaction(s). Invalid rows and duplicates were skipped.", "success")
            return redirect(url_for("transactions"))

        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename or not upload.filename.lower().endswith(".csv"):
                flash("Choose a .csv file.", "error")
                return redirect(url_for("import_csv"))
            try:
                content = upload.stream.read().decode("utf-8-sig")
                if len(content.splitlines()) > 501:
                    raise ValueError("Import is limited to 500 rows at a time.")
                reader = csv.DictReader(io.StringIO(content))
                required = {"date", "type", "amount", "description", "account"}
                headers = {header.strip().lower() for header in (reader.fieldnames or [])}
                if not required.issubset(headers):
                    raise ValueError("CSV headers must include date,type,amount,description,account. Category is optional.")
                accounts_by_name = {a.name.lower(): a for a in Account.query.all()}
                categories_by_name = {c.name.lower(): c for c in Category.query.all()}
                rows = []
                for source in reader:
                    raw = {(key or "").strip().lower(): (value or "").strip() for key, value in source.items()}
                    row: dict[str, Any] = {
                        "date": raw.get("date", ""), "type": raw.get("type", "").lower(),
                        "amount": raw.get("amount", ""), "description": raw.get("description", "")[:180],
                        "account": raw.get("account", ""), "category": raw.get("category", ""),
                        "transfer_to": raw.get("transfer_to", ""), "notes": raw.get("notes", "")[:500],
                        "error": "",
                    }
                    try:
                        parsed_date = date.fromisoformat(row["date"])
                        if row["type"] not in KINDS:
                            raise ValueError("Type must be income, expense or transfer.")
                        row["amount_paise"] = money_to_paise(row["amount"])
                        if not row["description"]:
                            raise ValueError("Description is required.")
                        account = accounts_by_name.get(row["account"].lower())
                        if not account:
                            raise ValueError("Account does not match an existing account.")
                        row["account_id"] = account.id
                        row["date"] = parsed_date.isoformat()
                        row["category_id"] = None
                        row["transfer_to_account_id"] = None
                        if row["type"] == "transfer":
                            target = accounts_by_name.get(row["transfer_to"].lower())
                            if not target or target.id == account.id:
                                raise ValueError("Transfer needs a different existing transfer_to account.")
                            row["transfer_to_account_id"] = target.id
                        elif row["category"]:
                            category = categories_by_name.get(row["category"].lower())
                            if not category or category.kind != row["type"]:
                                raise ValueError("Category does not match an existing category of that type.")
                            row["category_id"] = category.id
                        row["duplicate"] = duplicate_exists(row)
                    except (ValueError, TypeError) as exc:
                        row["error"] = str(exc) or "Invalid row."
                        row["duplicate"] = False
                    rows.append(row)
                batch_id = secrets.token_urlsafe(18)
                db.session.add(ImportBatch(id=batch_id, rows_json=json.dumps(rows), created_at=utcnow_naive()))
                db.session.commit()
                session["import_batch_id"] = batch_id
                return render_template("import.html", rows=rows, preview=True)
            except (UnicodeDecodeError, csv.Error, ValueError) as exc:
                flash(str(exc) or "Could not read this CSV.", "error")
                return redirect(url_for("import_csv"))
        return render_template("import.html", rows=[], preview=False)

    @app.errorhandler(413)
    def file_too_large(_error):
        flash("That file is too large. CSV imports are limited to 2 MB.", "error")
        return redirect(url_for("import_csv"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
