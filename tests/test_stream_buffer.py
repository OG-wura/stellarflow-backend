from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ingestion.stream_buffer import StreamBuffer


def test_stream_buffer_reuses_preallocated_storage_across_feeds() -> None:
    buffer = StreamBuffer()

    original_buffer_id = id(buffer._buf)
    assert len(buffer._buf) > 0

    frames = list(buffer.feed(b'{"first": 1}\n'))
    assert frames == [{"first": 1}]

    buffer.reset()
    assert id(buffer._buf) == original_buffer_id

    frames = list(buffer.feed(b'{"second": 2}\n'))
    assert frames == [{"second": 2}]
