"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = "development"
    database_url: str = "postgresql+asyncpg://payroll:payroll@localhost:5432/payroll"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PAYROLL_",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
