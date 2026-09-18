"""Shared test fixtures.

Integration tests run against a real PostgreSQL instance: the concurrency
guarantees under test are properties of PostgreSQL's row locking, and no
in-memory substitute can stand in for them.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_engine
from app.db.uow import UnitOfWork
from app.main import create_app
from app.services.wallet import WalletService


def _test_settings() -> Settings:
    """Settings pointed at the test database.

    Defaults match docker-compose; CI and local runs override via environment.
    """
    return Settings(
        environment="test",
        log_level="WARNING",
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_user=os.getenv("POSTGRES_USER", "wallet"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "wallet"),
        postgres_db=os.getenv("POSTGRES_DB", "wallet_test"),
        db_pool_size=30,
        db_max_overflow=20,
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    return _test_settings()


@pytest_asyncio.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Session-wide engine with a freshly created schema."""
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """Leave every test a clean database.

    Truncation rather than a rollback-wrapped session: the concurrency tests
    need real, independently committing transactions, which a shared outer
    transaction would prevent.
    """
    yield
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE wallet_operations, wallets CASCADE"))


@pytest_asyncio.fixture
async def service(session_factory: async_sessionmaker[AsyncSession]) -> WalletService:
    return WalletService(uow_factory=lambda: UnitOfWork(session_factory))


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the real ASGI app.

    The app's own lifespan runs, so the engine, pool and shutdown path under
    test are the ones that run in production - not a stand-in wired up by the
    test. It connects to the same test database the ``engine`` fixture
    prepared the schema in.
    """
    app = create_app(settings)
    transport = ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as http_client,
    ):
        yield http_client


@pytest_asyncio.fixture
async def wallet_id(client: AsyncClient) -> uuid.UUID:
    """A freshly created, empty wallet."""
    response = await client.post("/api/v1/wallets")
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


@pytest_asyncio.fixture
async def funded_wallet_id(client: AsyncClient) -> uuid.UUID:
    """A wallet pre-loaded with 1000.00."""
    response = await client.post("/api/v1/wallets", json={"initial_balance": "1000.00"})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])
