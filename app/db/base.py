"""Declarative base and shared column type mappings."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric
from sqlalchemy.orm import DeclarativeBase, registry


class Base(DeclarativeBase):
    """Base class for all ORM models.

    ``Decimal`` maps to ``NUMERIC(20, 2)`` everywhere: money is exact decimal
    arithmetic inside the database and never a float anywhere in the stack.
    """

    registry = registry(
        type_annotation_map={
            Decimal: Numeric(20, 2),
            datetime: DateTime(timezone=True),
        }
    )
