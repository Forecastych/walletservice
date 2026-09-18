"""Validation rules for request models."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.v1.schemas import OperationRequest
from app.domain.enums import OperationType


class TestOperationRequest:
    def test_accepts_integer_amount(self) -> None:
        request = OperationRequest(operation_type=OperationType.DEPOSIT, amount=1000)

        assert request.amount == Decimal("1000.00")

    def test_accepts_decimal_string(self) -> None:
        request = OperationRequest.model_validate({"operation_type": "WITHDRAW", "amount": "12.34"})

        assert request.amount == Decimal("12.34")
        assert request.operation_type is OperationType.WITHDRAW

    def test_amount_is_normalised_to_two_places(self) -> None:
        request = OperationRequest.model_validate({"operation_type": "DEPOSIT", "amount": "5.1"})

        assert request.amount == Decimal("5.10")
        assert request.amount.as_tuple().exponent == -2

    @pytest.mark.parametrize("amount", [0, "0.00", -1, "-0.01"])
    def test_rejects_non_positive(self, amount: object) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate({"operation_type": "DEPOSIT", "amount": amount})

    def test_rejects_sub_cent_precision(self) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate({"operation_type": "DEPOSIT", "amount": "1.001"})

    def test_rejects_absurd_amount(self) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate({"operation_type": "DEPOSIT", "amount": 10**15})

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_rejects_non_finite(self, value: str) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate({"operation_type": "DEPOSIT", "amount": value})

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate(
                {"operation_type": "DEPOSIT", "amount": 1, "balance": 10}
            )

    def test_rejects_unknown_operation_type(self) -> None:
        with pytest.raises(ValidationError):
            OperationRequest.model_validate({"operation_type": "TRANSFER", "amount": 1})


class TestOperationType:
    def test_sign(self) -> None:
        assert OperationType.DEPOSIT.sign == 1
        assert OperationType.WITHDRAW.sign == -1
