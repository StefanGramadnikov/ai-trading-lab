from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    execution_mode: Literal["PAPER_ONLY"] = "PAPER_ONLY"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://ai_trading_lab:local_example_only@localhost:5432/ai_trading_lab"
    )
    database_timeout_seconds: float = Field(default=2, gt=0, le=30)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
                raise ValueError("Expected a PostgreSQL psycopg URL with host and database")
        except ArgumentError:
            raise ValueError("Invalid PostgreSQL URL") from None
        return value
