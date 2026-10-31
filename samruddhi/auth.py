"""Session auth, CSRF, and role guards."""
import secrets
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for)

from .models import Household, User, db
from .seed import seed_categories, seed_upi_apps

bp = Blueprint("auth", __name__)


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    u = db.session.get(User, uid)
    return u if u and u.can_login else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not g.user:
            if not Household.query.first():
                return redirect(url_for("auth.setup"))
            return redirect(url_for("auth.login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if not g.user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf():
    if request.method in ("POST", "PUT", "DELETE") and not current_app.config.get("TESTING"):
        sent = request.form.get("csrf") or request.headers.get("X-CSRF")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Session expired - please go back and try again.")


def validate_new_credentials(username, email, password, exclude_id=None):
    username = (username or "").strip().lower()
    email = (email or "").strip().lower()
    if len(username) < 3 or not username.replace("_", "").replace(".", "").isalnum():
        return "Username needs 3+ letters/digits (underscore or dot allowed)."
    if "@" not in email or "." not in email.split("@")[-1]:
        return "Enter a valid email address."
    if password is not None and len(password) < 8:
        return "Password must be at least 8 characters."
    for field, val in (("username", username), ("email", email)):
        q = User.query.filter(getattr(User, field) == val)
        if exclude_id:
            q = q.filter(User.id != exclude_id)
        if q.first():
            return f"That {field} is already taken."
    return None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First run: whoever signs up first becomes the household admin."""
    if Household.query.first():
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        f = request.form
        err = validate_new_credentials(f.get("username"), f.get("email"), f.get("password"))
        if not (f.get("name") or "").strip():
            err = "Enter your name."
        if f.get("password") != f.get("password2"):
            err = "Passwords do not match."
        if err:
            flash(err, "error")
        else:
            hh = Household(name=(f.get("household") or "").strip() or "HOUSEHOLD # 1")
            db.session.add(hh)
            db.session.flush()
            u = User(household_id=hh.id, name=f["name"].strip(), username=f["username"].strip().lower(),
                     email=f["email"].strip().lower(), is_admin=True)
            u.set_password(f["password"])
            db.session.add(u)
            seed_categories(hh.id)
            seed_upi_apps(hh.id)
            db.session.commit()
            session.clear()
            session["uid"] = u.id
            session.permanent = True
            flash("Welcome! You are the household admin. Start by adding your accounts.", "ok")
            return redirect(url_for("settings.accounts"))
    return render_template("auth/setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not Household.query.first():
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        ident = (request.form.get("username") or "").strip().lower()
        u = User.query.filter((User.username == ident) | (User.email == ident)).first()
        if u and u.can_login and u.check_password(request.form.get("password", "")):
            session.clear()
            session["uid"] = u.id
            session.permanent = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash("Wrong username or password.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
