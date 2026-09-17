"""Application configuration, loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Values come from environment variables (or a local ``.env`` file). No secret
    ever has a usable default: the application refuses to start without one.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Application ---------------------------------------------------------
    app_name: str = "wallet-service"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # -- Database ------------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "wallet"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "wallet"

    db_pool_size: int = Field(default=20, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_pool_recycle_seconds: int = Field(default=1800, gt=0)
    db_echo: bool = False

    # Server-side guards: a query or a row lock may never hang forever.
    db_statement_timeout_ms: int = Field(default=5_000, gt=0)
    db_lock_timeout_ms: int = Field(default=3_000, gt=0)

    # -- Business rules ------------------------------------------------------
    # Upper bound for a single operation. Guards against typos and absurd
    # values overflowing NUMERIC(20, 2).
    max_operation_amount: int = Field(default=1_000_000_000, gt=0)
    # Retry budget for transient database failures (deadlock, serialization).
    db_retry_attempts: int = Field(default=3, ge=1, le=10)

    # -- Exposure ------------------------------------------------------------
    docs_enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy DSN."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync DSN, used by Alembic's offline mode and tooling."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg2",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
