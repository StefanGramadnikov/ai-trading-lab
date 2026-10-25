from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ai_trading_lab.ai.models import PriceSnapshot
from ai_trading_lab.paper_execution.models import NonnegativeAmount


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    execution_mode: Literal["PAPER_ONLY"] = "PAPER_ONLY"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://ai_trading_lab:local_example_only@localhost:5432/ai_trading_lab"
    )
    reporting_username: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    reporting_password: SecretStr | None = Field(default=None, min_length=12, max_length=200)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    ai_enabled: bool = False
    ai_total_limit_usd: NonnegativeAmount = Decimal(0)
    # How long a reviewed price may be used after its verification date (the default is one day;
    # an unattended multi-day run needs the owner to raise it deliberately).
    ai_price_max_age_days: int = Field(default=1, ge=1, le=30, strict=True)
    ai_reviewed_prices: tuple[PriceSnapshot, ...] = Field(default=(), max_length=100)
    openai_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
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

    @field_validator("reporting_username", "reporting_password", mode="before")
    @classmethod
    def empty_reporting_secret(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("reporting_password")
    @classmethod
    def ascii_reporting_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().isascii():
            raise ValueError("Reporting password must use ASCII characters")
        return value

    @model_validator(mode="after")
    def reporting_credentials(self) -> "Settings":
        if (self.reporting_username is None) != (self.reporting_password is None):
            raise ValueError("Reporting username and password must be configured together")
        if self.telegram_enabled and (not self.telegram_bot_token or not self.telegram_chat_id):
            raise ValueError("Enabled Telegram requires local token and target")
        return self
