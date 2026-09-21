"""Per-chat rate limiting and short-term search memory.

AstrBot 层用它把"点播频率"约束在温和范围：搜索与交付各自使用每会话令牌桶，
超限时给出需要等待的秒数；同时记住每个会话最近一次搜索参数，供"换一批"
之类的人类常用表达复用。模块刻意不依赖 AstrBot，便于独立单测。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

# 默认预算：搜索 6 次/分钟，交付 3 次/分钟；单次超限不会锁死太久。
DEFAULT_SEARCH_CAPACITY = 6
DEFAULT_SEARCH_REFILL_SECONDS = 60.0
DEFAULT_DELIVERY_CAPACITY = 3
DEFAULT_DELIVERY_REFILL_SECONDS = 60.0
DEFAULT_MEMORY_TTL_SECONDS = 300.0
MAX_MEMORY_ENTRIES = 1024


class TokenBucket:
    """Fixed-capacity token bucket keyed by an opaque session id."""

    def __init__(
        self,
        capacity: int,
        refill_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if refill_seconds <= 0:
            raise ValueError("refill_seconds must be positive")
        self._capacity = float(capacity)
        self._refill_seconds = refill_seconds
        self._clock = clock
        self._tokens: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}

    def check(self, key: str) -> float | None:
        """Consume one token; return retry_after seconds when over the limit.

        ``None`` means the action is allowed now. The return value is a
        floor-estimated wait time, safe to render directly to a user.
        """
        if not key:
            return None
        now = self._clock()
        tokens = min(
            self._capacity,
            self._tokens.get(key, self._capacity)
            + self._refill_rate() * (now - self._last_seen.get(key, now)),
        )
        if tokens < 1.0:
            missing = 1.0 - tokens
            # 拒绝也记账：连续抢跑会累积"债务"，等待时间随之增长。
            self._tokens[key] = max(-self._capacity, tokens - 1.0)
            self._last_seen[key] = now
            return max(1.0, missing / self._refill_rate())
        self._tokens[key] = tokens - 1.0
        self._last_seen[key] = now
        self._purge(now)
        return None

    def _refill_rate(self) -> float:
        return self._capacity / self._refill_seconds

    def _purge(self, now: float, *, max_entries: int = 512) -> None:
        if len(self._tokens) <= max_entries:
            return
        stale: list[str] = []
        for key, last in self._last_seen.items():
            # 满桶且久未使用的会话直接丢弃，无状态可言。
            if self._tokens.get(key) == self._capacity and now - last > 300.0:
                stale.append(key)
        for key in stale:
            self._tokens.pop(key, None)
            self._last_seen.pop(key, None)


class SessionState:
    """Owns the rate buckets and the last-search memory of a plugin run."""

    def __init__(
        self,
        *,
        search_capacity: int = DEFAULT_SEARCH_CAPACITY,
        search_refill_seconds: float = DEFAULT_SEARCH_REFILL_SECONDS,
        delivery_capacity: int = DEFAULT_DELIVERY_CAPACITY,
        delivery_refill_seconds: float = DEFAULT_DELIVERY_REFILL_SECONDS,
        memory_ttl_seconds: float = DEFAULT_MEMORY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._search_bucket = TokenBucket(
            search_capacity, search_refill_seconds, clock=clock
        )
        self._delivery_bucket = TokenBucket(
            delivery_capacity, delivery_refill_seconds, clock=clock
        )
        self._memory_ttl = memory_ttl_seconds
        self._clock = clock
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}

    def check_search(self, session_id: str) -> float | None:
        """Rate-limit one search; return retry_after seconds or None."""
        return self._search_bucket.check(session_id)

    def check_delivery(self, session_id: str) -> float | None:
        """Rate-limit one media preparation; return retry_after or None."""
        return self._delivery_bucket.check(session_id)

    def remember_search(self, session_id: str, parameters: Mapping[str, Any]) -> None:
        """Store the parameters of the latest search for one chat."""
        now = self._clock()
        self._memory[session_id] = (now + self._memory_ttl, dict(parameters))
        if len(self._memory) > MAX_MEMORY_ENTRIES:
            oldest = min(self._memory, key=lambda key: self._memory[key][0])
            self._memory.pop(oldest, None)

    def last_search(self, session_id: str) -> dict[str, Any] | None:
        """Return the remembered search parameters, or None when stale/missing."""
        entry = self._memory.get(session_id)
        if entry is None:
            return None
        expires_at, parameters = entry
        if self._clock() >= expires_at:
            self._memory.pop(session_id, None)
            return None
        return dict(parameters)

    def forget_search(self, session_id: str) -> None:
        self._memory.pop(session_id, None)


__all__ = [
    "DEFAULT_DELIVERY_CAPACITY",
    "DEFAULT_DELIVERY_REFILL_SECONDS",
    "DEFAULT_MEMORY_TTL_SECONDS",
    "DEFAULT_SEARCH_CAPACITY",
    "DEFAULT_SEARCH_REFILL_SECONDS",
    "SessionState",
    "TokenBucket",
]
