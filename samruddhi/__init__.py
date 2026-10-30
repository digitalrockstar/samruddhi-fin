from datetime import date

from flask import Flask, g, jsonify, render_template, request, session
from sqlalchemy import text

from . import auth, ledger, permissions
from .config import Config
from .models import Household, db

from .themes_data import THEME_GROUPS, THEME_KEYS, THEMES  # noqa: E402

_ran_today = {}


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config or Config)
    db.init_app(app)

    from .routes_main import bp as main_bp
    from .routes_settings import bp as settings_bp
    app.register_blueprint(auth.bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)

    with app.app_context():
        db.create_all()

    @app.before_request
    def load_user():
        g.user = auth.current_user()
        g.household = db.session.get(Household, g.user.household_id) if g.user else None
        auth.check_csrf()
        # Generate due subscriptions / reminders lazily, at most once a day per household (per process).
        if g.user and request.endpoint not in ("static", "healthz"):
            key = (g.household.id, date.today())
            if _ran_today.get(g.household.id) != key and not app.config.get("TESTING"):
                _ran_today[g.household.id] = key
                ledger.run_due(g.household.id)

    @app.after_request
    def flush_warnings(resp):
        from flask import flash
        for w in db.session.info.pop("warnings", []):
            flash(w, "warn")
        return resp

    @app.context_processor
    def inject():
        theme = (g.user.theme if g.user else None) or request.cookies.get("theme") or "daylight"
        pending = 0
        if g.user:
            from .routes_main import pending_count
            pending = pending_count(g.household.id)
        return dict(csrf_token=auth.csrf_token, themes=THEMES, theme_groups=THEME_GROUPS, theme=theme if theme in THEME_KEYS else "daylight",
                    pending_count=pending, today=date.today(), can=lambda p: permissions.can(g.user, p))

    app.jinja_env.globals["pick"] = lambda form, key, default="": form.get(key, default) if form is not None else default
    app.jinja_env.globals["picked"] = lambda form, key, value, default=False: (
        (value in form.getlist(key)) if form is not None else default)
    app.jinja_env.filters["inr"] = ledger.inr
    app.jinja_env.filters["amt"] = ledger.rupees_str
    app.jinja_env.filters["d"] = lambda v, f="%d %b %Y": v.strftime(f) if v else ""

    @app.route("/healthz")
    def healthz():
        db.session.execute(text("select 1"))
        return jsonify(ok=True)

    @app.route("/sw.js")
    def service_worker():
        resp = app.send_static_file("sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/manifest.webmanifest")
    def manifest():
        icons = [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                 {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                 {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
        return jsonify(name="Samruddhi", short_name="Samruddhi", start_url="/", scope="/", display="standalone",
                       background_color="#007AFF", theme_color="#007AFF", icons=icons)

    @app.route("/favicon.ico")
    def favicon():
        resp = app.send_static_file("icons/favicon-48.png")      # PNG served at the legacy path
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.errorhandler(ledger.LedgerError)
    def ledger_error(e):
        from flask import flash, redirect
        flash(str(e), "error")
        return redirect(request.referrer or "/")

    for code, msg in ((403, "You need admin rights for that."), (404, "We couldn't find that page."),
                      (400, "That request didn't look right.")):
        app.register_error_handler(code, lambda e, code=code, msg=msg: (
            render_template("error.html", code=code, msg=getattr(e, "description", None) or msg), code))
    return app
