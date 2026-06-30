"""Immutable configuration loading with frozen dataclass schemas.

All runtime configuration objects are frozen (``@dataclass(frozen=True)``), so
once the application initialises its network settings they cannot be mutated
by background workers or hot-reload callbacks.  Any attempt to reassign a
field raises ``FrozenInstanceError`` at runtime, which ``__init__`` callers
can catch if they need to surface a clearer error message.

Design notes
------------
* ``AppConfig`` mirrors the structure of ``config.json`` but as an immutable
  dataclass tree.
* ``load`` constructs the config from a JSON source (file path, file-like, or
  raw string), falling back to hard-coded defaults when the source is missing
  or malformed.
* Sub-configs (``RateLimitConfig``, ``RedisConfig``) are themselves frozen so
  the immutability is enforced at every level.
* Callers that need a slightly different setting can derive a new instance via
  ``dataclasses.replace`` — this creates a shallow copy with the requested
  fields changed, leaving the original untouched.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)

__all__ = [
    "RateLimitConfig",
    "RedisConfig",
    "AppConfig",
    "load",
    "defaults",
]


# ---------------------------------------------------------------------------
# Immutable sub-dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RateLimitConfig:
    """Immutable rate-limiting configuration."""

    window_ms: int = 900_000
    max_requests: int = 100
    enabled: bool = True


@dataclass(frozen=True)
class RedisConfig:
    """Immutable Redis connection configuration."""

    url: str = "redis://localhost:6379/0"
    key_prefix: str = "stellarflow:"
    socket_timeout_ms: int = 5000
    socket_connect_timeout_ms: int = 5000


# ---------------------------------------------------------------------------
# Root immutable config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration tree.

    All fields are final once instantiated.  Use ``dataclasses.replace`` to
    derive a modified copy.
    """

    fetch_interval_ms: int = 10_000
    soroban_poll_interval_ms: int = 15_000
    multi_sig_poll_interval_ms: int = 30_000
    hourly_average_check_interval_ms: int = 900_000
    cache_duration_ms: int = 30_000
    batch_window_ms: int = 5_000
    rate_limit: RateLimitConfig = dataclasses.field(default_factory=RateLimitConfig)
    redis: RedisConfig = dataclasses.field(default_factory=RedisConfig)

    def with_overrides(self, **updates: Any) -> AppConfig:
        """Return a new :class:`AppConfig` with the supplied overrides applied.

        Sub-dataclasses are recursively replaced via ``dataclasses.replace``
        when their names appear as mapping keys.

        Args:
            **updates: Field names and their new values.

        Returns:
            A **new** :class:`AppConfig` instance; ``self`` is unchanged.
        """
        new = self
        for key, value in updates.items():
            if not hasattr(new, key):
                raise AttributeError(
                    f"AppConfig has no field '{key}'. "
                    f"Valid fields: {[f.name for f in dataclasses.fields(new)]}"
                )
            current = getattr(new, key)
            if isinstance(value, Mapping) and dataclasses.is_dataclass(current):
                value = replace(current, **value)  # type: ignore[arg-type]
            new = replace(new, **{key: value})  # type: ignore[arg-type]
        return new


# ---------------------------------------------------------------------------
# Canonical defaults
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Defaults:
    app: AppConfig = dataclasses.field(default_factory=AppConfig)


DEFAULTS = _Defaults().app


# ---------------------------------------------------------------------------
# Loader core
# ---------------------------------------------------------------------------
_SOURCE_TYPES = Union[str, bytes, os.PathLike, Mapping[str, Any], None]


def load(source: _SOURCE_TYPES = None) -> AppConfig:
    """Load and validate an :class:`AppConfig` from *source*.

    Args:
        source: One of:
            * ``None`` — loads from ``config.json`` in the current working
              directory (the project root).
            * ``str`` / ``bytes`` — parsed as a JSON document.
            * path-like object — read from disk.
            * ``Mapping`` — treated as an already-parsed dictionary.

    Returns:
        A frozen :class:`AppConfig` instance.
    """
    raw = _read_source(source)
    if raw is None:
        logger.info("[ConfigLoader] No source provided; using defaults.")
        return DEFAULTS

    if not isinstance(raw, Mapping):
        raise TypeError(
            f"Config source must be a mapping or JSON string, got {type(raw).__name__}"
        )

    return _build_config(raw)


def _read_source(source: _SOURCE_TYPES) -> Optional[Mapping[str, Any]]:
    """Resolve *source* to a Python mapping, or ``None`` if unavailable."""
    if source is None:
        source = _default_config_path()

    if isinstance(source, (str, bytes, os.PathLike)):
        try:
            with open(source, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, IsADirectoryError):
            logger.warning("[ConfigLoader] Config file not found: %s", source)
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("[ConfigLoader] Failed to parse config file: %s", exc)
            return None

    if isinstance(source, Mapping):
        return source

    raise TypeError(f"Unsupported config source type: {type(source).__name__}")


def _default_config_path() -> str:
    return os.path.join(os.getcwd(), "config.json")


def _build_config(raw: Mapping[str, Any]) -> AppConfig:
    """Convert a plain mapping into an immutable :class:`AppConfig`."""
    raw_rate_limit = raw.get("rate_limit", {})
    raw_redis = raw.get("redis", {})

    rate_limit = RateLimitConfig(
        window_ms=int(raw_rate_limit.get("window_ms", 900_000)),
        max_requests=int(raw_rate_limit.get("max_requests", 100)),
        enabled=bool(raw_rate_limit.get("enabled", True)),
    )

    redis = RedisConfig(
        url=str(raw_redis.get("url", "redis://localhost:6379/0")),
        key_prefix=str(raw_redis.get("key_prefix", "stellarflow:")),
        socket_timeout_ms=int(raw_redis.get("socket_timeout_ms", 5000)),
        socket_connect_timeout_ms=int(raw_redis.get("socket_connect_timeout_ms", 5000)),
    )

    return AppConfig(
        fetch_interval_ms=int(raw.get("fetch_interval_ms", 10_000)),
        soroban_poll_interval_ms=int(raw.get("soroban_poll_interval_ms", 15_000)),
        multi_sig_poll_interval_ms=int(raw.get("multi_sig_poll_interval_ms", 30_000)),
        hourly_average_check_interval_ms=int(
            raw.get("hourly_average_check_interval_ms", 900_000)
        ),
        cache_duration_ms=int(raw.get("cache_duration_ms", 30_000)),
        batch_window_ms=int(raw.get("batch_window_ms", 5_000)),
        rate_limit=rate_limit,
        redis=redis,
    )


def validate(config: AppConfig) -> None:
    """Raise ``ValueError`` if *config* contains logically invalid values.

    Checks performed:
    * All ``*_interval_ms`` values must be positive.
    * ``rate_limit.window_ms`` must be positive.
    """
    if config.fetch_interval_ms <= 0:
        raise ValueError("fetch_interval_ms must be a positive integer.")
    if config.soroban_poll_interval_ms <= 0:
        raise ValueError("soroban_poll_interval_ms must be a positive integer.")
    if config.rate_limit.window_ms <= 0:
        raise ValueError("rate_limit.window_ms must be a positive integer.")
    if config.rate_limit.max_requests <= 0:
        raise ValueError("rate_limit.max_requests must be a positive integer.")
