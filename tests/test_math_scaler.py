from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analytics.math_scaler import (
    SCALE_7,
    SCALE_14,
    cross_feed_multiply,
    floor_divide,
    multiply_rates,
    pack_rate,
    scale_down,
    scale_up,
)


# ---------------------------------------------------------------------------
# scale_up
# ---------------------------------------------------------------------------

def test_scale_up_integer():
    assert scale_up(1) == SCALE_7

def test_scale_up_float_truncates():
    # 1.00000001 → 10_000_000.1 → floor → 10_000_000
    assert scale_up(1.00000001) == 10_000_000

def test_scale_up_custom_factor():
    assert scale_up(2, SCALE_14) == 2 * SCALE_14

def test_scale_up_rejects_bool():
    with pytest.raises(TypeError):
        scale_up(True)

def test_scale_up_rejects_non_finite():
    with pytest.raises(ValueError):
        scale_up(float("inf"))


# ---------------------------------------------------------------------------
# scale_down
# ---------------------------------------------------------------------------

def test_scale_down_roundtrip():
    original = "1500.1234567"
    scaled = scale_up(original)
    assert str(scale_down(scaled)) == original

def test_scale_down_custom_factor():
    from decimal import Decimal
    assert scale_down(SCALE_14, SCALE_14) == Decimal("1")


# ---------------------------------------------------------------------------
# multiply_rates
# ---------------------------------------------------------------------------

def test_multiply_rates_returns_scale14():
    result = multiply_rates(1, 1)
    # 1×10^7 × 1×10^7 = 10^14
    assert result == SCALE_14

def test_multiply_rates_cross_pair():
    # 1500 NGN/USD × 0.00065 USD/XLM
    result = multiply_rates(1500.0, 0.00065)
    # Expected: scale_up(1500)=15_000_000_000 * scale_up(0.00065)=6_500 → 97_500_000_000_000
    assert result == scale_up(1500.0) * scale_up(0.00065)


# ---------------------------------------------------------------------------
# cross_feed_multiply
# ---------------------------------------------------------------------------

def test_cross_feed_multiply_default_scale7():
    # product at SCALE_14 divided back to SCALE_7
    result = cross_feed_multiply(1500.0, 0.00065)
    expected = multiply_rates(1500.0, 0.00065) // (SCALE_14 // SCALE_7)
    assert result == expected

def test_cross_feed_multiply_output_scale14():
    # requesting SCALE_14 output should return the raw product
    result = cross_feed_multiply(1500.0, 0.00065, output_scale=SCALE_14)
    assert result == multiply_rates(1500.0, 0.00065)

def test_cross_feed_multiply_identity():
    # rate_a=1, rate_b=1 → result should equal SCALE_7
    assert cross_feed_multiply(1, 1) == SCALE_7


# ---------------------------------------------------------------------------
# floor_divide
# ---------------------------------------------------------------------------

def test_floor_divide_basic():
    scaled = scale_up(1500.0)
    # Dividing 1500 (scaled) by 3 should give ~500 (scaled)
    result = floor_divide(scaled, 3)
    assert result == scale_up(500.0)

def test_floor_divide_zero_raises():
    with pytest.raises(ZeroDivisionError):
        floor_divide(SCALE_7, 0)


# ---------------------------------------------------------------------------
# pack_rate
# ---------------------------------------------------------------------------

def test_pack_rate_equivalence():
    assert pack_rate(42.5) == scale_up(42.5, SCALE_7)

def test_pack_rate_deterministic():
    # Float and string representations must produce identical integers
    assert pack_rate(0.00065) == pack_rate("0.00065")
