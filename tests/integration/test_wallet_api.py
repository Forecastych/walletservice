"""End-to-end tests for the wallet endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

WALLETS = "/api/v1/wallets"


class TestCreateWallet:
    async def test_creates_empty_wallet(self, client: AsyncClient) -> None:
        response = await client.post(WALLETS)

        assert response.status_code == 201
        body = response.json()
        assert uuid.UUID(body["id"])
        assert Decimal(body["balance"]) == Decimal("0.00")

    async def test_creates_prefunded_wallet(self, client: AsyncClient) -> None:
        response = await client.post(WALLETS, json={"initial_balance": "250.50"})

        assert response.status_code == 201
        assert Decimal(response.json()["balance"]) == Decimal("250.50")

    async def test_rejects_negative_initial_balance(self, client: AsyncClient) -> None:
        response = await client.post(WALLETS, json={"initial_balance": "-1"})

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"


class TestGetWallet:
    async def test_returns_balance(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        response = await client.get(f"{WALLETS}/{funded_wallet_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(funded_wallet_id)
        assert Decimal(body["balance"]) == Decimal("1000.00")

    async def test_unknown_wallet_is_404(self, client: AsyncClient) -> None:
        response = await client.get(f"{WALLETS}/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["code"] == "wallet_not_found"

    async def test_malformed_uuid_is_422(self, client: AsyncClient) -> None:
        response = await client.get(f"{WALLETS}/not-a-uuid")

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_response_carries_request_id(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        response = await client.get(f"{WALLETS}/{wallet_id}")

        assert response.headers["X-Request-ID"]

    async def test_client_request_id_is_echoed(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        response = await client.get(
            f"{WALLETS}/{wallet_id}", headers={"X-Request-ID": "trace-abc-123"}
        )

        assert response.headers["X-Request-ID"] == "trace-abc-123"


class TestOperations:
    async def test_deposit_increases_balance(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"{WALLETS}/{wallet_id}/operation",
            json={"operation_type": "DEPOSIT", "amount": 1000},
        )

        assert response.status_code == 200
        body = response.json()
        assert Decimal(body["balance"]) == Decimal("1000.00")
        assert body["operation_type"] == "DEPOSIT"
        assert body["replayed"] is False
        assert uuid.UUID(body["operation_id"])

    async def test_withdraw_decreases_balance(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"{WALLETS}/{funded_wallet_id}/operation",
            json={"operation_type": "WITHDRAW", "amount": 400},
        )

        assert response.status_code == 200
        assert Decimal(response.json()["balance"]) == Decimal("600.00")

    async def test_withdraw_to_exactly_zero_is_allowed(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"{WALLETS}/{funded_wallet_id}/operation",
            json={"operation_type": "WITHDRAW", "amount": "1000.00"},
        )

        assert response.status_code == 200
        assert Decimal(response.json()["balance"]) == Decimal("0.00")

    async def test_overdraft_is_409_and_leaves_balance_untouched(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"{WALLETS}/{funded_wallet_id}/operation",
            json={"operation_type": "WITHDRAW", "amount": 1001},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "insufficient_funds"

        balance = await client.get(f"{WALLETS}/{funded_wallet_id}")
        assert Decimal(balance.json()["balance"]) == Decimal("1000.00")

    async def test_operation_on_unknown_wallet_is_404(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{WALLETS}/{uuid.uuid4()}/operation",
            json={"operation_type": "DEPOSIT", "amount": 10},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "wallet_not_found"

    async def test_fractional_amounts_are_exact(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        """Three deposits of 0.10 must total exactly 0.30, not 0.30000000000000004."""
        for _ in range(3):
            await client.post(
                f"{WALLETS}/{wallet_id}/operation",
                json={"operation_type": "DEPOSIT", "amount": "0.10"},
            )

        response = await client.get(f"{WALLETS}/{wallet_id}")
        assert Decimal(response.json()["balance"]) == Decimal("0.30")


class TestOperationValidation:
    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            ({"operation_type": "DEPOSIT", "amount": 0}, "zero amount"),
            ({"operation_type": "DEPOSIT", "amount": -5}, "negative amount"),
            ({"operation_type": "DEPOSIT", "amount": "abc"}, "non-numeric amount"),
            ({"operation_type": "DEPOSIT", "amount": "1.005"}, "too many decimal places"),
            ({"operation_type": "DEPOSIT", "amount": 10**12}, "above the maximum"),
            ({"operation_type": "TRANSFER", "amount": 10}, "unknown operation type"),
            ({"operation_type": "deposit", "amount": 10}, "wrong case"),
            ({"amount": 10}, "missing operation_type"),
            ({"operation_type": "DEPOSIT"}, "missing amount"),
            (
                {"operation_type": "DEPOSIT", "amount": 10, "balance": 999},
                "unexpected extra field",
            ),
        ],
    )
    async def test_invalid_payload_is_422(
        self,
        client: AsyncClient,
        wallet_id: uuid.UUID,
        payload: dict[str, object],
        reason: str,
    ) -> None:
        response = await client.post(f"{WALLETS}/{wallet_id}/operation", json=payload)

        assert response.status_code == 422, f"expected rejection for {reason}"
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["details"]

    async def test_balance_unchanged_after_invalid_request(
        self, client: AsyncClient, funded_wallet_id: uuid.UUID
    ) -> None:
        await client.post(
            f"{WALLETS}/{funded_wallet_id}/operation",
            json={"operation_type": "WITHDRAW", "amount": -100},
        )

        response = await client.get(f"{WALLETS}/{funded_wallet_id}")
        assert Decimal(response.json()["balance"]) == Decimal("1000.00")


class TestIdempotency:
    async def test_replay_returns_original_result_without_moving_money(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        headers = {"Idempotency-Key": "order-4711"}
        payload = {"operation_type": "DEPOSIT", "amount": 100}

        first = await client.post(
            f"{WALLETS}/{wallet_id}/operation", json=payload, headers=headers
        )
        second = await client.post(
            f"{WALLETS}/{wallet_id}/operation", json=payload, headers=headers
        )

        assert first.status_code == second.status_code == 200
        assert first.json()["replayed"] is False
        assert second.json()["replayed"] is True
        assert second.json()["operation_id"] == first.json()["operation_id"]

        balance = await client.get(f"{WALLETS}/{wallet_id}")
        assert Decimal(balance.json()["balance"]) == Decimal("100.00")

    async def test_key_reused_with_different_payload_is_409(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        headers = {"Idempotency-Key": "order-4712"}
        await client.post(
            f"{WALLETS}/{wallet_id}/operation",
            json={"operation_type": "DEPOSIT", "amount": 100},
            headers=headers,
        )

        response = await client.post(
            f"{WALLETS}/{wallet_id}/operation",
            json={"operation_type": "DEPOSIT", "amount": 250},
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["code"] == "idempotency_key_conflict"

    async def test_same_key_on_different_wallets_is_independent(
        self, client: AsyncClient
    ) -> None:
        headers = {"Idempotency-Key": "shared-key"}
        payload = {"operation_type": "DEPOSIT", "amount": 50}
        first_id = (await client.post(WALLETS)).json()["id"]
        second_id = (await client.post(WALLETS)).json()["id"]

        await client.post(f"{WALLETS}/{first_id}/operation", json=payload, headers=headers)
        response = await client.post(
            f"{WALLETS}/{second_id}/operation", json=payload, headers=headers
        )

        assert response.status_code == 200
        assert response.json()["replayed"] is False
        assert Decimal(response.json()["balance"]) == Decimal("50.00")

    async def test_requests_without_a_key_are_never_deduplicated(
        self, client: AsyncClient, wallet_id: uuid.UUID
    ) -> None:
        payload = {"operation_type": "DEPOSIT", "amount": 10}
        for _ in range(3):
            await client.post(f"{WALLETS}/{wallet_id}/operation", json=payload)

        response = await client.get(f"{WALLETS}/{wallet_id}")
        assert Decimal(response.json()["balance"]) == Decimal("30.00")


class TestHealth:
    async def test_liveness(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readiness_reports_database(self, client: AsyncClient) -> None:
        response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}
