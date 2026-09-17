"""Unit of work: one transaction, one set of repositories."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.wallet import WalletOperationRepository, WalletRepository


class UnitOfWork:
    """Owns the transaction boundary for a single business operation.

    Used as an async context manager: the block commits on success and rolls
    back on any exception, so no caller can leave a half-applied transaction
    behind.
    """

    wallets: WalletRepository
    operations: WalletOperationRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:  # pragma: no cover - programming error
            raise RuntimeError("UnitOfWork must be entered before use")
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._session_factory()
        self.wallets = WalletRepository(self._session)
        self.operations = WalletOperationRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None  # noqa: S101 - invariant of __aenter__
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
