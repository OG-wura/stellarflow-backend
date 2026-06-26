from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_bytes(target: Path | str, data: bytes) -> None:
    """Persist *data* to *target* using a temporary file and ``os.replace``.

    The update is isolated to a sibling temporary file in the same directory so
    a crash or power loss during the write cannot leave the live tracking file in
    a partially-written state.
    """
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(
    target: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Atomically persist UTF-8 (or custom) text to *target*."""
    atomic_write_bytes(target, text.encode(encoding))


def atomic_write_json(
    target: Path | str,
    payload: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> None:
    """Atomically persist a JSON document to *target*."""
    serialized = json.dumps(
        payload,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=False,
    )
    atomic_write_text(target, serialized + "\n")


class PersistenceEngine:
    """Thread-safe local tracking store backed by an on-disk JSON file.

    All writes route through :func:`atomic_write_json` so persistence survives
    abrupt process termination without corrupting the active tracking file.
    """

    def __init__(self, tracking_file: Path | str) -> None:
        self._path = Path(tracking_file)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            self._state = {}
            return

        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            self._state = json.loads(raw) if raw else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load tracking file %s: %s", self._path, exc)
            self._state = {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._state[key] = value
            self._persist_locked()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._state.update(values)
            self._persist_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _persist_locked(self) -> None:
        atomic_write_json(self._path, self._state, sort_keys=True)
