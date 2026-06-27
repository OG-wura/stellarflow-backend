from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlidingWindowConfig:
    window_size: float = 1.0
    max_requests: int = 100
    redis_key_prefix: str = "rl:sw"
    redis_key_ttl: int = 10


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    limit: int
    window_size: float
    retry_after: float


class _InMemorySlidingWindow:
    __slots__ = ("_window_size", "_max_requests", "_timestamps", "_lock")

    def __init__(self, window_size: float, max_requests: int) -> None:
        self._window_size = window_size
        self._max_requests = max_requests
        self._timestamps: Dict[str, Tuple[float, ...]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_size
        with self._lock:
            ts_list = self._timestamps.get(key)
            if ts_list is None:
                self._timestamps[key] = (now,)
                return True
            trimmed = tuple(t for t in ts_list if t > cutoff)
            if len(trimmed) < self._max_requests:
                self._timestamps[key] = trimmed + (now,)
                return True
            self._timestamps[key] = trimmed
            return False

    def check(self, key: str) -> Tuple[int, float]:
        now = time.monotonic()
        cutoff = now - self._window_size
        with self._lock:
            ts_list = self._timestamps.get(key)
            if ts_list is None:
                return self._max_requests, 0.0
            trimmed = tuple(t for t in ts_list if t > cutoff)
            self._timestamps[key] = trimmed
            if not trimmed:
                remaining = self._max_requests
                retry_after = 0.0
            else:
                remaining = max(0, self._max_requests - len(trimmed))
                earliest = trimmed[0]
                retry_after = max(0.0, self._window_size - (now - earliest))
            return remaining, retry_after

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self._window_size
        with self._lock:
            ts_list = self._timestamps.get(key)
            if ts_list is None:
                return self._max_requests
            trimmed = tuple(t for t in ts_list if t > cutoff)
            self._timestamps[key] = trimmed
            return max(0, self._max_requests - len(trimmed))

    def reset(self, key: str) -> None:
        with self._lock:
            self._timestamps.pop(key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._timestamps.clear()


class SlidingWindowRateLimiter:
    __slots__ = (
        "_config",
        "_redis",
        "_redis_lock",
        "_fallback",
        "_fallback_lock",
        "_local",
    )

    def __init__(
        self,
        config: Optional[SlidingWindowConfig] = None,
        redis_client: Optional[object] = None,
    ) -> None:
        self._config = config or SlidingWindowConfig()
        self._redis = redis_client
        self._redis_lock = threading.Lock()
        self._fallback: Optional[_InMemorySlidingWindow] = None
        self._fallback_lock = threading.RLock()
        self._local = _InMemorySlidingWindow(
            self._config.window_size, self._config.max_requests
        )

    def _use_redis(self) -> bool:
        if self._redis is None:
            return False
        try:
            with self._redis_lock:
                self._redis.ping()
            return True
        except Exception:
            logger.warning("Redis ping failed — falling back to in-memory store")
            return False

    def allow(self, key: str) -> bool:
        if self._use_redis():
            try:
                return self._redis_allow(key)
            except Exception:
                logger.exception("Redis allow() failed — falling back")
                self._maybe_init_fallback()
                return self._fallback.allow(key) if self._fallback else self._local.allow(key)
        return self._local.allow(key)

    def check(self, key: str) -> RateLimitResult:
        if self._use_redis():
            try:
                return self._redis_check(key)
            except Exception:
                logger.exception("Redis check() failed — falling back")
                self._maybe_init_fallback()
                if self._fallback:
                    remaining, retry_after = self._fallback.check(key)
                else:
                    remaining, retry_after = self._local.check(key)
                return RateLimitResult(
                    allowed=remaining > 0,
                    remaining=remaining,
                    limit=self._config.max_requests,
                    window_size=self._config.window_size,
                    retry_after=retry_after,
                )
        remaining, retry_after = self._local.check(key)
        return RateLimitResult(
            allowed=remaining > 0,
            remaining=remaining,
            limit=self._config.max_requests,
            window_size=self._config.window_size,
            retry_after=retry_after,
        )

    def remaining(self, key: str) -> int:
        if self._use_redis():
            try:
                return self._redis_remaining(key)
            except Exception:
                logger.exception("Redis remaining() failed — falling back")
                self._maybe_init_fallback()
                if self._fallback:
                    return self._fallback.remaining(key)
        return self._local.remaining(key)

    def reset(self, key: str) -> None:
        if self._use_redis():
            try:
                self._redis_reset(key)
            except Exception:
                logger.exception("Redis reset() failed")
        self._local.reset(key)
        if self._fallback:
            self._fallback.reset(key)

    def reset_all(self) -> None:
        if self._use_redis():
            try:
                self._redis_reset_all()
            except Exception:
                logger.exception("Redis reset_all() failed")
        self._local.reset_all()
        if self._fallback:
            self._fallback.reset_all()

    def close(self) -> None:
        if self._redis is not None:
            try:
                with self._redis_lock:
                    self._redis.close()
            except Exception:
                logger.warning("Error closing Redis connection", exc_info=True)
            self._redis = None

    def _maybe_init_fallback(self) -> None:
        with self._fallback_lock:
            if self._fallback is None:
                self._fallback = _InMemorySlidingWindow(
                    self._config.window_size, self._config.max_requests
                )
                logger.info("Initialized in-memory fallback sliding window")

    def _redis_key(self, key: str) -> str:
        return f"{self._config.redis_key_prefix}:{key}"

    def _redis_allow(self, key: str) -> bool:
        rkey = self._redis_key(key)
        now_ms = int(time.time() * 1000)
        window_ms = int(self._config.window_size * 1000)
        cutoff_ms = now_ms - window_ms
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, cutoff_ms)
        pipe.zcard(rkey)
        pipe.zadd(rkey, {now_ms: now_ms})
        pipe.expire(rkey, self._config.redis_key_ttl)
        _, count, _, _ = pipe.execute()
        return count < self._config.max_requests

    def _redis_check(self, key: str) -> RateLimitResult:
        rkey = self._redis_key(key)
        now_ms = int(time.time() * 1000)
        window_ms = int(self._config.window_size * 1000)
        cutoff_ms = now_ms - window_ms
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, cutoff_ms)
        pipe.zcard(rkey)
        pipe.zrange(rkey, 0, 0, withscores=True)
        pipe.expire(rkey, self._config.redis_key_ttl)
        _, count, earliest, _ = pipe.execute()
        remaining = max(0, self._config.max_requests - count)
        retry_after = 0.0
        if earliest and remaining == 0:
            earliest_ts = earliest[0][1] if earliest else None
            if earliest_ts is not None:
                elapsed_ms = now_ms - earliest_ts
                retry_after = max(0.0, (window_ms - elapsed_ms) / 1000.0)
        return RateLimitResult(
            allowed=remaining > 0,
            remaining=remaining,
            limit=self._config.max_requests,
            window_size=self._config.window_size,
            retry_after=math.ceil(retry_after * 10) / 10,
        )

    def _redis_remaining(self, key: str) -> int:
        rkey = self._redis_key(key)
        now_ms = int(time.time() * 1000)
        window_ms = int(self._config.window_size * 1000)
        cutoff_ms = now_ms - window_ms
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, cutoff_ms)
        pipe.zcard(rkey)
        pipe.expire(rkey, self._config.redis_key_ttl)
        _, count, _ = pipe.execute()
        return max(0, self._config.max_requests - count)

    def _redis_reset(self, key: str) -> None:
        rkey = self._redis_key(key)
        self._redis.delete(rkey)

    def _redis_reset_all(self) -> None:
        cursor = 0
        prefix = f"{self._config.redis_key_prefix}:"
        while True:
            cursor, keys = self._redis.scan(cursor, match=f"{prefix}*")
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                break


rate_limiter = SlidingWindowRateLimiter()

__all__ = [
    "SlidingWindowConfig",
    "RateLimitResult",
    "SlidingWindowRateLimiter",
    "rate_limiter",
]
