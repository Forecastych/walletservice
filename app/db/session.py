"""Async engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Build an async engine with production-sane pool settings."""
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        # Detects connections killed by the database or a proxy, so the first
        # request after an outage retries instead of failing.
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": settings.app_name,
                # No query and no lock wait may run unbounded: a stuck row lock
                # must surface as an error, not as an exhausted connection pool.
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "lock_timeout": str(settings.db_lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.db_statement_timeout_ms * 2
                ),
            },
            "timeout": settings.db_pool_timeout_seconds,
        },
    )


def init_engine(settings: Settings) -> AsyncEngine:
    """Create the process-wide engine and session factory."""
    global _engine, _session_factory  # noqa: PLW0603

    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


async def dispose_engine() -> None:
    """Close every pooled connection. Called on shutdown."""
    global _engine, _session_factory  # noqa: PLW0603

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    if _engine is None:  # pragma: no cover - guarded by app lifespan
        raise RuntimeError("Database engine is not initialised")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:  # pragma: no cover - guarded by app lifespan
        raise RuntimeError("Session factory is not initialised")
    return _session_factory

