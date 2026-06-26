"""Simple in-process TTL cache with an injectable clock."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class TTLCache:
    """Dict-based TTL cache keyed by arbitrary hashable tuples.

    Thread-safety is provided by the GIL for CPython; no extra locking needed
    for async workloads that share an event loop.
    """

    def __init__(
        self,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a TTL in seconds and an optional clock provider."""
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: dict[tuple[Any, ...], tuple[Any, float]] = {}

    def get(self, key: tuple[Any, ...]) -> tuple[bool, Any]:
        """Return (hit, value).

        hit is False when the key is missing or its entry has expired.
        """
        entry = self._store.get(key)
        if entry is None:
            return False, None
        value, expires_at = entry
        if self._clock() >= expires_at:
            del self._store[key]
            return False, None
        return True, value

    def set(self, key: tuple[Any, ...], value: Any) -> None:
        """Store value under key, expiring after ttl_seconds."""
        self._store[key] = (value, self._clock() + self._ttl)
