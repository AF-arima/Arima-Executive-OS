from __future__ import annotations

from typing import Any


POSTGRES_RETRYABLE_STATES = frozenset({"40P01", "40001"})


def is_retryable_postgres_error(error: BaseException) -> bool:
    """Return true only for PostgreSQL deadlock/serialization failures."""
    current: Any = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        state = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if state in POSTGRES_RETRYABLE_STATES:
            return True
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None)
    return False
