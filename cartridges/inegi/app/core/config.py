from __future__ import annotations

import os

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}


class Settings(BaseSettings):
    app_name: str = "inegi"
    inegi_api_token: SecretStr | None = Field(default=None, alias="INEGI_API_TOKEN")
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

    def require_inegi_token(self) -> str:
        runtime_env = os.environ.get("APP_ENV", "production").strip().lower() or "production"
        if runtime_env not in _LOCAL_ENVIRONMENTS:
            raise RuntimeError("INEGI_API_TOKEN is only allowed for local development")
        if self.inegi_api_token is None:
            raise RuntimeError("INEGI_API_TOKEN is required")
        token = self.inegi_api_token.get_secret_value().strip()
        if not token:
            raise RuntimeError("INEGI_API_TOKEN is required")
        return token


settings = Settings()
