from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from validation.consensus import (
    MINIMUM_INDEPENDENT_NODE_SOURCES,
    count_independent_sources,
    enforce_minimum_independent_sources,
    has_minimum_independent_sources,
)


def test_count_independent_sources_deduplicates_same_source():
    records = [
        {"source": "node-1", "price": 1.0},
        {"source": "node-1", "price": 1.1},
        {"source": "node-2", "price": 1.05},
    ]

    assert count_independent_sources(records) == 2
    assert not has_minimum_independent_sources(records)


def test_has_minimum_independent_sources_accepts_three_unique_sources():
    records = [
        {"source": "node-a", "price": 1.0},
        {"source": "node-b", "price": 1.1},
        {"source": "node-c", "price": 0.95},
    ]

    assert has_minimum_independent_sources(records)
    assert count_independent_sources(records) == MINIMUM_INDEPENDENT_NODE_SOURCES


def test_enforce_minimum_independent_sources_rejects_and_logs_warning(caplog):
    caplog.set_level(logging.WARNING)

    records = [
        {"source": "node-1", "price": 1.0},
        {"source": "node-2", "price": 1.02},
    ]

    with pytest.raises(ValueError, match="Insufficient independent node sources"):
        enforce_minimum_independent_sources(records, asset_pair="XLM/USD")

    assert any(
        "[ENGINEERING]" in record.message and "XLM/USD" in record.message
        for record in caplog.records
    )
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_enforce_minimum_independent_sources_accepts_exact_minimum():
    records = [
        {"source": "node-1", "price": 1.0},
        {"source": "node-2", "price": 1.02},
        {"source": "node-3", "price": 0.98},
    ]

    enforce_minimum_independent_sources(records, asset_pair="BTC/USD")
