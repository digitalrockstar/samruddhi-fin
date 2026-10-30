"""Configuration. Everything sensitive comes from environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def database_uri() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        inst = BASE_DIR / "instance"
        inst.mkdir(exist_ok=True)
        return f"sqlite:///{inst / 'samruddhi.db'}"
    # Render/Neon hand out postgres:// or postgresql:// - SQLAlchemy wants a driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET") or "dev-only-secret-change-me"
    SQLALCHEMY_DATABASE_URI = database_uri()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = bool(os.environ.get("RENDER"))
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    PAGE_SIZE = 50


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SECRET_KEY = "test"
    WTF_CSRF = False
