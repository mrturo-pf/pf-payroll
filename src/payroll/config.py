"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Represent Settings."""

    env: str = "development"
    database_url: str = "postgresql+asyncpg://payroll:payroll@localhost:5432/payroll"
    api_base_url: str = "http://127.0.0.1:8000"
    log_level: str = "INFO"
    financial_data_base_url: str = ""
    financial_data_api_key: str = ""
    financial_data_cache_ttl_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PAYROLL_",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
