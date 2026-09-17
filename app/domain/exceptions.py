"""Domain-level errors.

These carry no HTTP semantics. The API layer maps them onto status codes in a
single place (``app.core.errors``), so the service layer stays transport
agnostic and is unit-testable without a web stack.
"""

from __future__ import annotations

import uuid
from decimal import Decimal


class DomainError(Exception):
    """Base class for every expected, non-bug failure."""

    code: str = "domain_error"
    message: str = "Domain error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class WalletNotFoundError(DomainError):
    code = "wallet_not_found"
    message = "Wallet not found"

    def __init__(self, wallet_id: uuid.UUID) -> None:
        super().__init__(f"Wallet {wallet_id} not found")
        self.wallet_id = wallet_id


class InsufficientFundsError(DomainError):
    code = "insufficient_funds"
    message = "Insufficient funds"

    def __init__(self, wallet_id: uuid.UUID, requested: Decimal) -> None:
        super().__init__(
            f"Wallet {wallet_id} has insufficient funds for a withdrawal of {requested}"
        )
        self.wallet_id = wallet_id
        self.requested = requested


class IdempotencyKeyConflictError(DomainError):
    """Same idempotency key replayed with a different request body."""

    code = "idempotency_key_conflict"
    message = "Idempotency key was already used with a different payload"

    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency key {key!r} was already used with a different payload")
        self.key = key


class WalletAlreadyExistsError(DomainError):
    code = "wallet_already_exists"
    message = "Wallet already exists"

    def __init__(self, wallet_id: uuid.UUID) -> None:
        super().__init__(f"Wallet {wallet_id} already exists")
        self.wallet_id = wallet_id


class ServiceUnavailableError(DomainError):
    """Transient infrastructure failure that survived the retry budget."""

    code = "service_unavailable"
    message = "Service temporarily unavailable, please retry"
