"""Data access for wallets and their operation journal.

Repositories own SQL and nothing else: no HTTP concerns, no retry policy, no
transaction boundaries (those belong to the unit of work).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Wallet, WalletOperation
from app.domain.enums import OperationType


class WalletRepository:
    """Reads and writes against the ``wallets`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, initial_balance: Decimal) -> Wallet:
        wallet = Wallet(balance=initial_balance)
        self._session.add(wallet)
        await self._session.flush()
        return wallet

    async def get(self, wallet_id: uuid.UUID) -> Wallet | None:
        return await self._session.get(Wallet, wallet_id)

    async def exists(self, wallet_id: uuid.UUID) -> bool:
        stmt = select(select(Wallet.id).where(Wallet.id == wallet_id).exists())
        return bool(await self._session.scalar(stmt))

    async def apply_delta(self, wallet_id: uuid.UUID, delta: Decimal) -> Decimal | None:
        """Atomically move the balance by ``delta``.

        The whole read-modify-write collapses into one statement::

            UPDATE wallets SET balance = balance + :delta
             WHERE id = :id AND balance + :delta >= 0
            RETURNING balance

        This is what makes concurrent operations on the same wallet correct:

        * There is no window between reading the balance and writing it, so two
          parallel withdrawals cannot both observe the same pre-state and
          produce a lost update.
        * The ``UPDATE`` itself takes the row lock, so a second transaction
          touching the same wallet blocks until the first commits and then
          re-evaluates its ``WHERE`` clause against the *new* balance. No
          separate ``SELECT ... FOR UPDATE`` round trip is needed.
        * The balance guard lives in the ``WHERE`` clause, so an overdraft
          simply matches no row rather than being rejected after the fact.

        Returns the new balance, or ``None`` when no row matched — meaning
        either the wallet does not exist or the operation would overdraw it.
        The caller distinguishes the two.
        """
        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id, Wallet.balance + delta >= 0)
            .values(balance=Wallet.balance + delta)
            .returning(Wallet.balance)
            # Nothing in this transaction reads a Wallet object afterwards, and
            # the default "auto" strategy would append its own RETURNING clause
            # to synchronise identity-map state. Disable it explicitly.
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class WalletOperationRepository:
    """Reads and writes against the ``wallet_operations`` journal."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        wallet_id: uuid.UUID,
        operation_type: OperationType,
        amount: Decimal,
        balance_after: Decimal,
        idempotency_key: str | None,
    ) -> WalletOperation:
        operation = WalletOperation(
            wallet_id=wallet_id,
            operation_type=operation_type,
            amount=amount,
            balance_after=balance_after,
            idempotency_key=idempotency_key,
        )
        self._session.add(operation)
        await self._session.flush()
        return operation

    async def get_by_idempotency_key(
        self, wallet_id: uuid.UUID, idempotency_key: str
    ) -> WalletOperation | None:
        stmt = select(WalletOperation).where(
            WalletOperation.wallet_id == wallet_id,
            WalletOperation.idempotency_key == idempotency_key,
        )
        # Session.scalar is typed as returning Any; pin it to the declared type.
        operation: WalletOperation | None = await self._session.scalar(stmt)
        return operation
