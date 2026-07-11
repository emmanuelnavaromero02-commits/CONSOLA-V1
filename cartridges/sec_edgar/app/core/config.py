from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}


class Settings(BaseSettings):
    app_name: str = "sec_edgar"
    sec_edgar_user_agent: str | None = Field(default=None, alias="SEC_EDGAR_USER_AGENT")
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")
    minio_bucket: str = "lakehouse"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def database_url(self) -> str:
        if self.database_url_override and self.database_url_override.strip():
            return self.database_url_override
        user = os.environ.get("PG_USER", "postgres")
        password = os.environ.get("PG_PASSWORD", "")
        return f"postgresql+psycopg2://{user}:{password}@postgres:5432/modecissions"

    def require_user_agent(self) -> str:
        value = (self.sec_edgar_user_agent or "").strip()
        if value:
            return value
        runtime_env = os.environ.get("APP_ENV", "production").strip().lower() or "production"
        if runtime_env in _LOCAL_ENVIRONMENTS:
            return "OMEGA local-dev contact@example.invalid"
        raise RuntimeError("SEC_EDGAR_USER_AGENT is required in production")


settings = Settings()
