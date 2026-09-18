"""Wallet use cases.

The service owns business rules and transaction orchestration. It knows nothing
about HTTP, and can be exercised in unit tests with a fake unit of work.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.errors import is_transient, is_unique_violation
from app.db.uow import UnitOfWork
from app.domain.enums import OperationType
from app.domain.exceptions import (
    IdempotencyKeyConflictError,
    InsufficientFundsError,
    ServiceUnavailableError,
    WalletNotFoundError,
)

logger = logging.getLogger(__name__)

_IDEMPOTENCY_CONSTRAINT = "uq_wallet_operations_wallet_idempotency_key"

#: Base delay for the retry backoff. Retrying a deadlock immediately tends to
#: reproduce it, so attempts are spread out and jittered to avoid a thundering
#: herd of retries landing together.
_RETRY_BASE_DELAY_SECONDS = 0.02
_RETRY_MAX_DELAY_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class WalletView:
    """Read model returned to the API layer."""

    id: uuid.UUID
    balance: Decimal


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Outcome of a balance change."""

    wallet_id: uuid.UUID
    operation_id: uuid.UUID
    operation_type: OperationType
    amount: Decimal
    balance: Decimal
    #: True when the request was recognised as a replay and no money moved.
    replayed: bool = False


class WalletService:
    """Use cases for creating wallets, reading balances and moving money."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        retry_attempts: int = 3,
    ) -> None:
        self._uow_factory = uow_factory
        self._retry_attempts = max(1, retry_attempts)

    # -- Queries -------------------------------------------------------------

    async def get_wallet(self, wallet_id: uuid.UUID) -> WalletView:
        """Return the wallet's current balance.

        Raises:
            WalletNotFoundError: no wallet with that id exists.
        """
        async with self._uow_factory() as uow:
            wallet = await uow.wallets.get(wallet_id)
            if wallet is None:
                raise WalletNotFoundError(wallet_id)
            return WalletView(id=wallet.id, balance=wallet.balance)

    # -- Commands ------------------------------------------------------------

    async def create_wallet(self, initial_balance: Decimal = Decimal("0")) -> WalletView:
        """Create a wallet, optionally pre-funded."""
        async with self._uow_factory() as uow:
            wallet = await uow.wallets.create(initial_balance)
            view = WalletView(id=wallet.id, balance=wallet.balance)
        logger.info("wallet created", extra={"wallet_id": str(view.id)})
        return view

    async def perform_operation(
        self,
        wallet_id: uuid.UUID,
        operation_type: OperationType,
        amount: Decimal,
        idempotency_key: str | None = None,
    ) -> OperationResult:
        """Apply a deposit or a withdrawal and return the resulting balance.

        Safe under concurrency: the balance change is a single atomic statement
        (see :meth:`WalletRepository.apply_delta`), and the whole transaction is
        retried on transient database failures such as deadlocks.

        Raises:
            WalletNotFoundError: no wallet with that id exists.
            InsufficientFundsError: a withdrawal would overdraw the wallet.
            IdempotencyKeyConflictError: the key was reused with a different payload.
            ServiceUnavailableError: transient database failure outlived the retry budget.
        """
        last_error: SQLAlchemyError | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                return await self._perform_operation_once(
                    wallet_id, operation_type, amount, idempotency_key
                )
            except IntegrityError as exc:
                # A concurrent request carrying the same idempotency key won the
                # race. Its transaction committed; ours rolled back whole, so no
                # money moved twice. Return the winner's recorded outcome.
                if idempotency_key is not None and is_unique_violation(
                    exc, _IDEMPOTENCY_CONSTRAINT
                ):
                    replay = await self._load_replay(wallet_id, idempotency_key)
                    if replay is not None:
                        self._assert_same_payload(replay, operation_type, amount, idempotency_key)
                        return replay
                raise
            except SQLAlchemyError as exc:
                # A deterministic failure will fail identically next time.
                if not is_transient(exc):
                    raise
                last_error = exc
                if attempt == self._retry_attempts:
                    break
                logger.warning(
                    "transient database error, retrying operation",
                    extra={
                        "wallet_id": str(wallet_id),
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(self._backoff_delay(attempt))

        # Retry budget exhausted: surface a 503 rather than a raw driver error.
        logger.error(
            "operation abandoned after exhausting retries",
            extra={"wallet_id": str(wallet_id), "attempts": self._retry_attempts},
        )
        raise ServiceUnavailableError from last_error

    # -- Internals -----------------------------------------------------------

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff with full jitter."""
        ceiling = min(_RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1), _RETRY_MAX_DELAY_SECONDS)
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography

    async def _perform_operation_once(
        self,
        wallet_id: uuid.UUID,
        operation_type: OperationType,
        amount: Decimal,
        idempotency_key: str | None,
    ) -> OperationResult:
        async with self._uow_factory() as uow:
            # Fast path for a replay that already committed. The unique index is
            # still what guarantees correctness; this lookup only avoids
            # touching the balance when the answer is already known.
            if idempotency_key is not None:
                existing = await uow.operations.get_by_idempotency_key(wallet_id, idempotency_key)
                if existing is not None:
                    result = OperationResult(
                        wallet_id=existing.wallet_id,
                        operation_id=existing.id,
                        operation_type=OperationType(existing.operation_type),
                        amount=existing.amount,
                        balance=existing.balance_after,
                        replayed=True,
                    )
                    self._assert_same_payload(result, operation_type, amount, idempotency_key)
                    return result

            delta = amount * operation_type.sign
            new_balance = await uow.wallets.apply_delta(wallet_id, delta)

            if new_balance is None:
                # The UPDATE matched no row. Either the wallet is missing, or
                # the guard in the WHERE clause rejected an overdraft.
                if not await uow.wallets.exists(wallet_id):
                    raise WalletNotFoundError(wallet_id)
                raise InsufficientFundsError(wallet_id, amount)

            operation = await uow.operations.add(
                wallet_id=wallet_id,
                operation_type=operation_type,
                amount=amount,
                balance_after=new_balance,
                idempotency_key=idempotency_key,
            )
            result = OperationResult(
                wallet_id=wallet_id,
                operation_id=operation.id,
                operation_type=operation_type,
                amount=amount,
                balance=new_balance,
            )

        logger.info(
            "wallet operation applied",
            extra={
                "wallet_id": str(wallet_id),
                "operation_id": str(result.operation_id),
                "operation_type": operation_type.value,
                "amount": str(amount),
                "balance": str(result.balance),
            },
        )
        return result

    async def _load_replay(
        self, wallet_id: uuid.UUID, idempotency_key: str
    ) -> OperationResult | None:
        async with self._uow_factory() as uow:
            existing = await uow.operations.get_by_idempotency_key(wallet_id, idempotency_key)
            if existing is None:
                return None
            return OperationResult(
                wallet_id=existing.wallet_id,
                operation_id=existing.id,
                operation_type=OperationType(existing.operation_type),
                amount=existing.amount,
                balance=existing.balance_after,
                replayed=True,
            )

    @staticmethod
    def _assert_same_payload(
        stored: OperationResult,
        operation_type: OperationType,
        amount: Decimal,
        idempotency_key: str,
    ) -> None:
        """Reject a key reused for a different request.

        Returning the stored result would silently answer a question the client
        never asked, so this is a conflict rather than a replay.
        """
        if stored.operation_type is not operation_type or stored.amount != amount:
            raise IdempotencyKeyConflictError(idempotency_key)
