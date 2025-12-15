from functools import lru_cache
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "YouTube Scout"
    debug: bool = False
    database_url: str = "postgresql+psycopg2://postgres:postgres@db:5432/app"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str
    openai_api_key: str | None = None
    session_cookie_name: str = "yt_session"
    session_cookie_secure: bool = False
    session_cookie_domain: str | None = None
    admin_bootstrap_email: str = "admin@example.com"
    admin_bootstrap_password: str = "adminpass"
    cors_origins: list[str] = []
    allowed_regions: list[str] = ["US", "CA", "GB", "AU"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


class SessionData(BaseModel):
    user_id: int
    role: str
