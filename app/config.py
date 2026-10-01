"""Settings, loaded once from the environment.

Only DATABASE_URL is genuinely required to boot; the rest have safe defaults or
are optional features. `python -m scripts.doctor` reports exactly what is set.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- database (required) -------------------------------------------
    # Neon: copy the "Pooled connection" string. Accepts postgres://,
    # postgresql:// or postgresql+asyncpg:// and normalises to the async driver.
    database_url: str

    # ---- app ------------------------------------------------------------
    app_env: str = "development"
    secret_key: str = "dev-only-insecure-key"

    # ---- telegram (optional; the app runs fine without them) -------------
    telegram_bot_token: str = ""
    telegram_chat_id: Optional[int] = None
    # Optional extra guard on top of the chat-id check. Leave blank to disable.
    telegram_webhook_secret: Optional[str] = None
    # Public URL of this deployment, used by scripts/setup_webhook.py
    webhook_url: str = ""

    @property
    def async_database_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()