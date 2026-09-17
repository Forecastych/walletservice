"""Request and response models for API v1."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import OperationType

#: Amounts are decimals with at most two fraction digits and a hard upper bound.
#: ``strict=False`` lets JSON numbers and numeric strings through; floats are
#: normalised by Pydantic into Decimal without going through binary floats.
Amount = Annotated[
    Decimal,
    Field(
        gt=0,
        le=Decimal("1000000000"),
        max_digits=18,
        decimal_places=2,
        # Rejected during parsing, before the ordering constraints run: a
        # comparison against Decimal("NaN") raises rather than returning False.
        allow_inf_nan=False,
    ),
]


class _Base(BaseModel):
    # Unknown fields are a client bug: fail loudly instead of ignoring them.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperationRequest(_Base):
    """Body of ``POST /wallets/{id}/operation``."""

    operation_type: OperationType
    amount: Amount

    @field_validator("amount")
    @classmethod
    def _reject_non_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("amount must be a finite number")
        return value

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        # Quantise once, here, so the service and the database always see the
        # same scale as the NUMERIC(20, 2) column.
        self.amount = self.amount.quantize(Decimal("0.01"))
        return self


class CreateWalletRequest(_Base):
    """Body of ``POST /wallets``. Everything is optional."""

    initial_balance: Annotated[
        Decimal,
        Field(
            ge=0,
            le=Decimal("1000000000"),
            max_digits=18,
            decimal_places=2,
            allow_inf_nan=False,
        ),
    ] = Decimal("0")


class WalletResponse(_Base):
    """A wallet and its current balance."""

    id: uuid.UUID
    balance: Decimal


class OperationResponse(_Base):
    """Outcome of a balance change."""

    wallet_id: uuid.UUID
    operation_id: uuid.UUID
    operation_type: OperationType
    amount: Decimal
    balance: Decimal
    replayed: bool = Field(
        default=False,
        description="True when the request was recognised as an idempotent replay",
    )


class ErrorResponse(_Base):
    """Uniform error envelope for every non-2xx response."""

    code: str = Field(description="Stable, machine-readable error code")
    message: str = Field(description="Human-readable description")
    request_id: str | None = Field(default=None, description="Correlation id for support")
    details: list[dict[str, object]] | None = Field(
        default=None, description="Field-level validation problems, when applicable"
    )


class HealthResponse(_Base):
    status: str
    database: str | None = None
