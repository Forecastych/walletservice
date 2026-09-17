"""Initial schema: wallets and their operation journal

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-17

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # gen_random_uuid() lives in pgcrypto before PostgreSQL 13 and in core
    # afterwards. Creating the extension keeps the migration portable.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "balance",
            sa.Numeric(precision=20, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallets"),
        sa.CheckConstraint("balance >= 0", name="ck_wallets_balance_non_negative"),
    )

    op.create_table(
        "wallet_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_operations"),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["wallets.id"],
            name="fk_wallet_operations_wallet_id_wallets",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("amount > 0", name="ck_wallet_operations_amount_positive"),
        sa.CheckConstraint(
            "balance_after >= 0", name="ck_wallet_operations_balance_after_non_negative"
        ),
        # This is what makes a replayed request a no-op instead of a double
        # spend: the second transaction aborts whole, balance update included.
        sa.UniqueConstraint(
            "wallet_id",
            "idempotency_key",
            name="uq_wallet_operations_wallet_idempotency_key",
        ),
    )
    op.create_index(
        "ix_wallet_operations_wallet_id", "wallet_operations", ["wallet_id"], unique=False
    )
    op.create_index(
        "ix_wallet_operations_wallet_id_created_at",
        "wallet_operations",
        ["wallet_id", "created_at"],
        unique=False,
    )

    # Keeps updated_at honest even for writes that bypass the ORM.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_wallets_set_updated_at
        BEFORE UPDATE ON wallets
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_wallets_set_updated_at ON wallets")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.drop_index("ix_wallet_operations_wallet_id_created_at", table_name="wallet_operations")
    op.drop_index("ix_wallet_operations_wallet_id", table_name="wallet_operations")
    op.drop_table("wallet_operations")
    op.drop_table("wallets")
