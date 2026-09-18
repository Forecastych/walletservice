"""Unit tests for the service layer, with no database involved."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.domain.enums import OperationType
from app.domain.exceptions import (
    IdempotencyKeyConflictError,
    InsufficientFundsError,
    ServiceUnavailableError,
    WalletNotFoundError,
)
from app.services.wallet import WalletService


class FakeWalletRepository:
    def __init__(self, balances: dict[uuid.UUID, Decimal]) -> None:
        self._balances = balances

    async def apply_delta(self, wallet_id: uuid.UUID, delta: Decimal) -> Decimal | None:
        balance = self._balances.get(wallet_id)
        if balance is None or balance + delta < 0:
            return None
        self._balances[wallet_id] = balance + delta
        return self._balances[wallet_id]

    async def exists(self, wallet_id: uuid.UUID) -> bool:
        return wallet_id in self._balances

    async def get(self, wallet_id: uuid.UUID) -> Any:
        balance = self._balances.get(wallet_id)
        if balance is None:
            return None
        return type("Row", (), {"id": wallet_id, "balance": balance})()

    async def create(self, initial_balance: Decimal) -> Any:
        wallet_id = uuid.uuid4()
        self._balances[wallet_id] = initial_balance
        return type("Row", (), {"id": wallet_id, "balance": initial_balance})()


class FakeOperationRepository:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def add(self, **kwargs: Any) -> Any:
        row = type("Row", (), {"id": uuid.uuid4(), **kwargs})()
        self.rows.append(row)
        return row

    async def get_by_idempotency_key(self, wallet_id: uuid.UUID, idempotency_key: str) -> Any:
        for row in self.rows:
            if row.wallet_id == wallet_id and row.idempotency_key == idempotency_key:
                return row
        return None


class FakeUnitOfWork:
    def __init__(self, balances: dict[uuid.UUID, Decimal], operations: FakeOperationRepository):
        self.wallets = FakeWalletRepository(balances)
        self.operations = operations

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class ExplodingUnitOfWork:
    """Fails a fixed number of times with a transient error, then succeeds."""

    def __init__(self, delegate_factory: Any, failures: int) -> None:
        self._delegate_factory = delegate_factory
        self.remaining_failures = failures
        self.attempts = 0

    def __call__(self) -> Any:
        self.attempts += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            return _FailingUow()
        return self._delegate_factory()


class _FailingUow:
    async def __aenter__(self) -> _FailingUow:
        error = OperationalError("UPDATE wallets", {}, Exception("deadlock detected"))
        error.orig = type("Orig", (), {"sqlstate": "40P01"})()  # type: ignore[assignment]
        raise error

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture
def balances() -> dict[uuid.UUID, Decimal]:
    return {}


@pytest.fixture
def operations() -> FakeOperationRepository:
    return FakeOperationRepository()


@pytest.fixture
def service(
    balances: dict[uuid.UUID, Decimal], operations: FakeOperationRepository
) -> WalletService:
    return WalletService(
        uow_factory=lambda: FakeUnitOfWork(balances, operations)  # type: ignore[arg-type]
    )


class TestPerformOperation:
    async def test_deposit_adds_to_balance(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("10.00")

        result = await service.perform_operation(wallet_id, OperationType.DEPOSIT, Decimal("5.00"))

        assert result.balance == Decimal("15.00")
        assert result.replayed is False

    async def test_withdraw_subtracts_from_balance(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("10.00")

        result = await service.perform_operation(wallet_id, OperationType.WITHDRAW, Decimal("4.00"))

        assert result.balance == Decimal("6.00")

    async def test_missing_wallet_raises(self, service: WalletService) -> None:
        with pytest.raises(WalletNotFoundError):
            await service.perform_operation(uuid.uuid4(), OperationType.DEPOSIT, Decimal("1.00"))

    async def test_overdraft_raises(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("1.00")

        with pytest.raises(InsufficientFundsError):
            await service.perform_operation(wallet_id, OperationType.WITHDRAW, Decimal("2.00"))

        assert balances[wallet_id] == Decimal("1.00")


class TestIdempotency:
    async def test_replay_does_not_move_money(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("0.00")

        first = await service.perform_operation(
            wallet_id, OperationType.DEPOSIT, Decimal("10.00"), idempotency_key="k"
        )
        second = await service.perform_operation(
            wallet_id, OperationType.DEPOSIT, Decimal("10.00"), idempotency_key="k"
        )

        assert second.replayed is True
        assert second.operation_id == first.operation_id
        assert balances[wallet_id] == Decimal("10.00")

    async def test_key_reuse_with_different_amount_conflicts(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("0.00")
        await service.perform_operation(
            wallet_id, OperationType.DEPOSIT, Decimal("10.00"), idempotency_key="k"
        )

        with pytest.raises(IdempotencyKeyConflictError):
            await service.perform_operation(
                wallet_id, OperationType.DEPOSIT, Decimal("99.00"), idempotency_key="k"
            )

    async def test_key_reuse_with_different_type_conflicts(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("50.00")
        await service.perform_operation(
            wallet_id, OperationType.DEPOSIT, Decimal("10.00"), idempotency_key="k"
        )

        with pytest.raises(IdempotencyKeyConflictError):
            await service.perform_operation(
                wallet_id, OperationType.WITHDRAW, Decimal("10.00"), idempotency_key="k"
            )


class TestRetryPolicy:
    async def test_transient_failure_is_retried(
        self, balances: dict[uuid.UUID, Decimal], operations: FakeOperationRepository
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("10.00")
        factory = ExplodingUnitOfWork(lambda: FakeUnitOfWork(balances, operations), failures=2)
        service = WalletService(uow_factory=factory, retry_attempts=3)  # type: ignore[arg-type]

        result = await service.perform_operation(wallet_id, OperationType.DEPOSIT, Decimal("5.00"))

        assert result.balance == Decimal("15.00")
        assert factory.attempts == 3

    async def test_exhausted_retries_surface_as_service_unavailable(
        self, balances: dict[uuid.UUID, Decimal], operations: FakeOperationRepository
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("10.00")
        factory = ExplodingUnitOfWork(lambda: FakeUnitOfWork(balances, operations), failures=99)
        service = WalletService(uow_factory=factory, retry_attempts=3)  # type: ignore[arg-type]

        with pytest.raises(ServiceUnavailableError):
            await service.perform_operation(wallet_id, OperationType.DEPOSIT, Decimal("5.00"))

        assert factory.attempts == 3


class TestGetWallet:
    async def test_returns_balance(
        self, service: WalletService, balances: dict[uuid.UUID, Decimal]
    ) -> None:
        wallet_id = uuid.uuid4()
        balances[wallet_id] = Decimal("42.00")

        assert (await service.get_wallet(wallet_id)).balance == Decimal("42.00")

    async def test_missing_wallet_raises(self, service: WalletService) -> None:
        with pytest.raises(WalletNotFoundError):
            await service.get_wallet(uuid.uuid4())
