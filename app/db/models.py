"""ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import OperationType


class Wallet(Base):
    """A user wallet holding a non-negative balance."""

    __tablename__ = "wallets"
    __table_args__ = (
        # Last line of defence. The service never issues an UPDATE that could
        # go negative, but a bug or a manual query must not be able to either.
        CheckConstraint("balance >= 0", name="ck_wallets_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    balance: Mapped[Decimal] = mapped_column(
        nullable=False, server_default=text("0"), default=Decimal("0")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WalletOperation(Base):
    """Append-only journal of every balance change.

    Serves two purposes: an audit trail (the balance can always be recomputed
    and reconciled), and the storage behind idempotent replays.
    """

    __tablename__ = "wallet_operations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_wallet_operations_amount_positive"),
        CheckConstraint(
            "balance_after >= 0",
            name="ck_wallet_operations_balance_after_non_negative",
        ),
        # Makes a replayed request a no-op instead of a double spend. NULLs
        # are distinct in PostgreSQL, so requests without a key are not
        # constrained against one another.
        UniqueConstraint(
            "wallet_id", "idempotency_key", name="uq_wallet_operations_wallet_idempotency_key"
        ),
        Index("ix_wallet_operations_wallet_id_created_at", "wallet_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operation_type: Mapped[OperationType] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
