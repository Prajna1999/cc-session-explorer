"""Runtime configuration for the cloud backend (env-var driven)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CC_CLOUD_", env_file=".env", extra="ignore")

    # SQLite by default (dev/tests); set CC_CLOUD_DATABASE_URL=postgresql+psycopg://… in prod.
    database_url: str = "sqlite:///./cc_cloud.db"
    jwt_secret: str = "dev-secret-change-me"
    token_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days
    api_public_url: str = "http://localhost:8000"
    # Git-native ingest (M1): where the worker keeps bare mirror clones, and the
    # shared secret for verifying git-host webhook signatures (HMAC-SHA256).
    repos_dir: str = "data/repos"
    webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
