from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    sap_payroll_base_url: str = ""
    sap_payroll_user: str = ""
    sap_payroll_pass: str = ""
    sap_payroll_client: str = "100"
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
