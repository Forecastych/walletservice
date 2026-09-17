"""FastAPI dependency wiring for API v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.db.uow import UnitOfWork
from app.services.wallet import WalletService

#: Idempotency keys are opaque to us, but they are stored and indexed, so the
#: length is capped to match the column and keep the index small.
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def get_wallet_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WalletService:
    """Build a service bound to a fresh unit of work per business operation."""
    session_factory = get_session_factory()
    return WalletService(
        uow_factory=lambda: UnitOfWork(session_factory),
        retry_attempts=settings.db_retry_attempts,
    )


def get_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
            description=(
                "Optional client-generated key. Repeating a request with the same "
                "key returns the original result instead of moving money twice."
            ),
        ),
    ] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    return key or None


WalletServiceDep = Annotated[WalletService, Depends(get_wallet_service)]
IdempotencyKeyDep = Annotated[str | None, Depends(get_idempotency_key)]
