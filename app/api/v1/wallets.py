"""Wallet endpoints (API v1)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.v1.deps import IdempotencyKeyDep, WalletServiceDep
from app.api.v1.schemas import (
    CreateWalletRequest,
    ErrorResponse,
    OperationRequest,
    OperationResponse,
    WalletResponse,
)
from app.core.http import HTTP_422_UNPROCESSABLE_CONTENT

router = APIRouter(prefix="/wallets", tags=["wallets"])

WalletId = Annotated[uuid.UUID, Path(description="Wallet UUID")]

_NOT_FOUND = {"model": ErrorResponse, "description": "Wallet not found"}
_CONFLICT = {
    "model": ErrorResponse,
    "description": "Insufficient funds, or idempotency key reused with a different payload",
}
_VALIDATION = {"model": ErrorResponse, "description": "Request validation failed"}
_UNAVAILABLE = {"model": ErrorResponse, "description": "Database temporarily unavailable"}


@router.post(
    "",
    response_model=WalletResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a wallet",
    responses={
        HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION,
        status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE,
    },
)
async def create_wallet(
    service: WalletServiceDep,
    payload: CreateWalletRequest | None = None,
) -> WalletResponse:
    """Create a wallet, optionally with a starting balance.

    Not part of the original specification, but without it the service has no
    way to bring a wallet into existence.
    """
    request = payload or CreateWalletRequest()
    wallet = await service.create_wallet(request.initial_balance)
    return WalletResponse(id=wallet.id, balance=wallet.balance)


@router.get(
    "/{wallet_uuid}",
    response_model=WalletResponse,
    summary="Get the current balance of a wallet",
    responses={
        status.HTTP_404_NOT_FOUND: _NOT_FOUND,
        HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION,
        status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE,
    },
)
async def get_wallet(wallet_uuid: WalletId, service: WalletServiceDep) -> WalletResponse:
    wallet = await service.get_wallet(wallet_uuid)
    return WalletResponse(id=wallet.id, balance=wallet.balance)


@router.post(
    "/{wallet_uuid}/operation",
    response_model=OperationResponse,
    summary="Deposit to or withdraw from a wallet",
    responses={
        status.HTTP_404_NOT_FOUND: _NOT_FOUND,
        status.HTTP_409_CONFLICT: _CONFLICT,
        HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION,
        status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE,
    },
)
async def perform_operation(
    wallet_uuid: WalletId,
    payload: OperationRequest,
    service: WalletServiceDep,
    idempotency_key: IdempotencyKeyDep,
) -> OperationResponse:
    """Change a wallet's balance.

    Concurrent calls against the same wallet are safe: the balance change is a
    single atomic statement, so parallel requests serialise on the row rather
    than overwriting each other.

    Supplying an ``Idempotency-Key`` header makes a retry of the same request
    return the original outcome instead of applying the operation twice.
    """
    result = await service.perform_operation(
        wallet_id=wallet_uuid,
        operation_type=payload.operation_type,
        amount=payload.amount,
        idempotency_key=idempotency_key,
    )
    return OperationResponse(
        wallet_id=result.wallet_id,
        operation_id=result.operation_id,
        operation_type=result.operation_type,
        amount=result.amount,
        balance=result.balance,
        replayed=result.replayed,
    )
