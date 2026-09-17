"""Domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class OperationType(StrEnum):
    """Kind of balance change requested by a client."""

    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"

    @property
    def sign(self) -> int:
        """Direction the operation moves the balance in."""
        return 1 if self is OperationType.DEPOSIT else -1
