"""Concurrency tests.

These are the tests that matter most for this service: they assert that
parallel operations on the *same* wallet cannot lose an update, cannot
overdraw, and cannot double-charge an idempotent request.

They run against a real PostgreSQL instance, because what is being verified is
the interaction between the application's single-statement update and the
database's row locking.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import WalletOperation
from app.domain.enums import OperationType
from app.domain.exceptions import InsufficientFundsError
from app.services.wallet import OperationResult, WalletService

pytestmark = pytest.mark.concurrency

WALLETS = "/api/v1/wallets"


async def _gather_settled(*coros: object) -> list[object]:
    return await asyncio.gather(*coros, return_exceptions=True)  # type: ignore[arg-type]


class TestParallelWithdrawals:
    async def test_balance_never_goes_negative(
        self, service: WalletService, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """100 parallel withdrawals against a balance that covers only 10.

        Exactly ten must succeed and ninety must be refused. A lost update
        would show up as too many successes or a negative balance.
        """
        wallet = await service.create_wallet(Decimal("100.00"))

        results = await _gather_settled(
            *(
                service.perform_operation(wallet.id, OperationType.WITHDRAW, Decimal("10.00"))
                for _ in range(100)
            )
        )

        succeeded = [r for r in results if isinstance(r, OperationResult)]
        refused = [r for r in results if isinstance(r, InsufficientFundsError)]
        unexpected = [r for r in results if isinstance(r, BaseException) and r not in refused]

        assert not unexpected, f"unexpected failures: {unexpected}"
        assert len(succeeded) == 10
        assert len(refused) == 90

        final = await service.get_wallet(wallet.id)
        assert final.balance == Decimal("0.00")

        # Every successful operation must have observed a distinct balance:
        # two operations reporting the same balance_after is the signature of
        # a lost update.
        async with session_factory() as session:
            balances = (
                await session.scalars(
                    select(WalletOperation.balance_after).where(
                        WalletOperation.wallet_id == wallet.id
                    )
                )
            ).all()
        assert len(balances) == 10
        assert len(set(balances)) == 10
        assert min(balances) == Decimal("0.00")

    async def test_parallel_withdrawals_over_http(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        """The same property, exercised through the HTTP layer."""
        responses = await asyncio.gather(
            *(
                client.post(
                    f"{WALLETS}/{funded_wallet_id}/operation",
                    json={"operation_type": "WITHDRAW", "amount": "100.00"},
                )
                for _ in range(40)
            )
        )

        statuses = [r.status_code for r in responses]
        assert statuses.count(200) == 10
        assert statuses.count(409) == 30
        assert set(statuses) == {200, 409}

        balance = await client.get(f"{WALLETS}/{funded_wallet_id}")
        assert Decimal(balance.json()["balance"]) == Decimal("0.00")


class TestParallelMixedOperations:
    async def test_final_balance_reconciles_with_the_journal(
        self, service: WalletService, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Interleaved deposits and withdrawals must sum exactly."""
        wallet = await service.create_wallet(Decimal("1000.00"))

        operations = []
        for _ in range(60):
            operations.append(
                service.perform_operation(wallet.id, OperationType.DEPOSIT, Decimal("25.00"))
            )
            operations.append(
                service.perform_operation(wallet.id, OperationType.WITHDRAW, Decimal("25.00"))
            )

        results = await _gather_settled(*operations)
        applied = [r for r in results if isinstance(r, OperationResult)]

        async with session_factory() as session:
            deposits = await session.scalar(
                select(func.coalesce(func.sum(WalletOperation.amount), 0)).where(
                    WalletOperation.wallet_id == wallet.id,
                    WalletOperation.operation_type == OperationType.DEPOSIT.value,
                )
            )
            withdrawals = await session.scalar(
                select(func.coalesce(func.sum(WalletOperation.amount), 0)).where(
                    WalletOperation.wallet_id == wallet.id,
                    WalletOperation.operation_type == OperationType.WITHDRAW.value,
                )
            )
            journal_rows = await session.scalar(
                select(func.count())
                .select_from(WalletOperation)
                .where(WalletOperation.wallet_id == wallet.id)
            )

        final = await service.get_wallet(wallet.id)
        assert final.balance == Decimal("1000.00") + deposits - withdrawals
        assert journal_rows == len(applied)
        assert final.balance >= 0

    async def test_parallel_deposits_are_all_applied(self, service: WalletService) -> None:
        """Deposits can never be refused, so every one must land."""
        wallet = await service.create_wallet()

        await asyncio.gather(
            *(
                service.perform_operation(wallet.id, OperationType.DEPOSIT, Decimal("1.00"))
                for _ in range(100)
            )
        )

        final = await service.get_wallet(wallet.id)
        assert final.balance == Decimal("100.00")


class TestConcurrentIdempotency:
    async def test_same_key_in_parallel_charges_once(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        """Twenty simultaneous retries of one request move money exactly once."""
        headers = {"Idempotency-Key": "concurrent-order-1"}
        payload = {"operation_type": "DEPOSIT", "amount": "100.00"}

        responses = await asyncio.gather(
            *(
                client.post(f"{WALLETS}/{wallet_id}/operation", json=payload, headers=headers)
                for _ in range(20)
            )
        )

        assert {r.status_code for r in responses} == {200}
        operation_ids = {r.json()["operation_id"] for r in responses}
        assert len(operation_ids) == 1, "every response must describe the same operation"

        balance = await client.get(f"{WALLETS}/{wallet_id}")
        assert Decimal(balance.json()["balance"]) == Decimal("100.00")

    async def test_parallel_distinct_keys_all_apply(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"{WALLETS}/{wallet_id}/operation",
                    json={"operation_type": "DEPOSIT", "amount": "1.00"},
                    headers={"Idempotency-Key": f"key-{index}"},
                )
                for index in range(50)
            )
        )

        assert {r.status_code for r in responses} == {200}
        balance = await client.get(f"{WALLETS}/{wallet_id}")
        assert Decimal(balance.json()["balance"]) == Decimal("50.00")


class TestWalletIsolation:
    async def test_parallel_operations_on_different_wallets_do_not_interfere(
        self, service: WalletService
    ) -> None:
        wallets = await asyncio.gather(
            *(service.create_wallet(Decimal("100.00")) for _ in range(10))
        )

        await asyncio.gather(
            *(
                service.perform_operation(w.id, OperationType.WITHDRAW, Decimal("40.00"))
                for w in wallets
                for _ in range(2)
            )
        )

        balances = await asyncio.gather(*(service.get_wallet(w.id) for w in wallets))
        assert all(b.balance == Decimal("20.00") for b in balances)
