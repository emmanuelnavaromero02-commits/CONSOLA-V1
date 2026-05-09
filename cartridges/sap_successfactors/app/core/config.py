from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    sf_base_url: str = ""
    sf_company_id: str = ""
    sf_client_id: str = ""
    sf_client_secret: str = ""
    sf_token_url: str = ""
    database_url: str = ""
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = ""
    internal_api_key: str = ""
    airflow_url: Optional[str] = None
    airflow_user: Optional[str] = None
    airflow_password: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
