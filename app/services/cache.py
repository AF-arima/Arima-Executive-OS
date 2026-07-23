import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from app.schemas.analytics import DashboardSummary


class DashboardCache(Protocol):
    async def get(self, key: str) -> DashboardSummary | None: ...

    async def generation(self) -> int: ...

    async def set(
        self,
        key: str,
        value: DashboardSummary,
        *,
        ttl_seconds: int,
        expected_generation: int,
    ) -> bool: ...

    async def invalidate(self) -> None: ...


@dataclass(slots=True)
class _CacheEntry:
    value: DashboardSummary
    expires_at: float
    namespace: int


class InMemoryDashboardCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._namespace = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> DashboardSummary | None:
        now = monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if (
                entry is None
                or entry.namespace != self._namespace
                or entry.expires_at <= now
            ):
                self._entries.pop(key, None)
                return None
            return entry.value.model_copy(deep=True)

    async def set(
        self,
        key: str,
        value: DashboardSummary,
        *,
        ttl_seconds: int,
        expected_generation: int,
    ) -> bool:
        async with self._lock:
            if expected_generation != self._namespace:
                return False
            self._entries[key] = _CacheEntry(
                value=value.model_copy(deep=True),
                expires_at=monotonic() + ttl_seconds,
                namespace=self._namespace,
            )
            return True

    async def generation(self) -> int:
        async with self._lock:
            return self._namespace

    async def invalidate(self) -> None:
        async with self._lock:
            self._namespace += 1
            self._entries.clear()


dashboard_cache = InMemoryDashboardCache()
