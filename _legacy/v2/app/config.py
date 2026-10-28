from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_password: str = ""
    app_username: str = ""
    database_url: str = ""
    python_version: str = "3.12.9"
    secret_key: str = ""
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_string: str = ""
    telegram_chat_id: int | None = None
    ingest_batch_size: int = 500

    def db_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            parts = urlsplit(self.database_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query.pop("sslmode", None)
            query.pop("channel_binding", None)
            return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), parts.fragment))
        return self.database_url

    def validate_production(self) -> None:
        required = {
            "APP_USERNAME": self.app_username,
            "APP_PASSWORD": self.app_password,
            "DATABASE_URL": self.database_url,
            "SECRET_KEY": self.secret_key,
            "TELEGRAM_API_ID": self.telegram_api_id,
            "TELEGRAM_API_HASH": self.telegram_api_hash,
            "TELEGRAM_PHONE": self.telegram_phone,
            "TELEGRAM_CHAT_ID": self.telegram_chat_id,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError("Missing required production secrets/config: " + ", ".join(missing))

settings = Settings()
