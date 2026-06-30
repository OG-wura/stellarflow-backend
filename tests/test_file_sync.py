from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.file_sync import (
    PersistenceEngine,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


def test_atomic_write_bytes_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "tracking.json"
    atomic_write_bytes(target, b'{"seq": 1}\n')

    assert target.read_bytes() == b'{"seq": 1}\n'
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"version": 1}\n', encoding="utf-8")

    atomic_write_text(target, '{"version": 2}\n')

    assert target.read_text(encoding="utf-8") == '{"version": 2}\n'


def test_atomic_write_json_serializes_payload(tmp_path: Path) -> None:
    target = tmp_path / "metrics.json"
    atomic_write_json(target, {"alpha": 1, "beta": 2}, sort_keys=True)

    assert json.loads(target.read_text(encoding="utf-8")) == {"alpha": 1, "beta": 2}


def test_atomic_write_cleans_up_temp_file_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "tracking.json"
    target.write_text('{"safe": true}\n', encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    with mock.patch("utils.file_sync.os.replace", side_effect=OSError("rename failed")):
        with pytest.raises(OSError, match="rename failed"):
            atomic_write_bytes(target, b'{"safe": false}\n')

    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_persistence_engine_round_trip(tmp_path: Path) -> None:
    tracking_file = tmp_path / "worker-state.json"
    engine = PersistenceEngine(tracking_file)

    engine.set("last_seq", 42)
    engine.update({"worker": "horizon-poller", "active": True})

    reloaded = PersistenceEngine(tracking_file)
    assert reloaded.snapshot() == {
        "last_seq": 42,
        "worker": "horizon-poller",
        "active": True,
    }


def test_persistence_engine_uses_atomic_writer(tmp_path: Path) -> None:
    tracking_file = tmp_path / "worker-state.json"
    engine = PersistenceEngine(tracking_file)

    with mock.patch("utils.file_sync.atomic_write_json") as writer:
        engine.set("cursor", 7)

    writer.assert_called_once_with(tracking_file, {"cursor": 7}, sort_keys=True)
