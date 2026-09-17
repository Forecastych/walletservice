"""Classification of database errors into retryable / meaningful categories."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

#: PostgreSQL SQLSTATE codes worth retrying. The transaction was aborted by the
#: server for a reason that is not the caller's fault and is likely to succeed
#: on a second attempt.
_TRANSIENT_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available (lock_timeout fired)
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now (database still starting up)
        "08000",  # connection_exception
        "08003",  # connection_does_not_exist
        "08006",  # connection_failure
    }
)

_UNIQUE_VIOLATION = "23505"


def _sqlstate(error: BaseException) -> str | None:
    """Best-effort extraction of the SQLSTATE from a driver error."""
    orig = getattr(error, "orig", None)
    for candidate in (orig, error):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None


def is_transient(error: BaseException) -> bool:
    """True when retrying the whole transaction is a reasonable response."""
    if isinstance(error, IntegrityError):
        # A constraint violation is deterministic: retrying reproduces it.
        return False
    if _sqlstate(error) in _TRANSIENT_SQLSTATES:
        return True
    # Connection-level failures often arrive without a SQLSTATE at all.
    return isinstance(error, OperationalError | DBAPIError) and getattr(
        error, "connection_invalidated", False
    )


def is_unique_violation(error: BaseException, constraint: str | None = None) -> bool:
    """True when the error is a unique-constraint violation.

    When ``constraint`` is given, the name must match, so an unrelated
    collision is never mistaken for the one being handled.
    """
    if not isinstance(error, IntegrityError) or _sqlstate(error) != _UNIQUE_VIOLATION:
        return False
    if constraint is None:
        return True
    orig = getattr(error, "orig", None)
    name = getattr(orig, "constraint_name", None)
    return name == constraint if name else constraint in str(orig)
