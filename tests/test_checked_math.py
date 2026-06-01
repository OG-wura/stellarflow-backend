from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analytics.checked_math import (
    BoundaryViolationError,
    MathOverflowError,
    PrecisionLossError,
    chain_fractional_percentages,
    checked_div,
    checked_mul,
    fractional_metric,
    multiply_fractional_metrics,
    percentage,
    safe_round,
    validate_metric_value,
)


def test_percentage_basic():
    assert percentage(25, 100) == 25.0
    assert percentage(1, 3, decimal_places=4) == pytest.approx(33.3333)


def test_percentage_zero_whole():
    assert percentage(5, 0) == 0.0


def test_checked_mul_and_div():
    assert checked_mul(10, 0.25) == 2.5
    assert checked_div(1, 4) == 0.25


def test_fractional_metric():
    assert fractional_metric(1000, 3, 100) == 30.0


def test_chain_fractional_percentages():
    # 100 * 1.10 * 1.05 = 115.5
    result = chain_fractional_percentages(100, [10, 5])
    assert result == pytest.approx(115.5)


def test_multiply_fractional_metrics():
    assert multiply_fractional_metrics(2, 0.5, 10) == 10.0


def test_safe_round():
    assert safe_round(1.234, 2) == 1.23
    assert safe_round(1.235, 2) == 1.24


def test_validate_metric_value_accepts_finite():
    assert validate_metric_value(42.5) == 42.5


def test_division_by_near_zero_raises():
    with pytest.raises(BoundaryViolationError):
        checked_div(1, 0)


def test_overflow_on_excessive_product_raises():
    with pytest.raises(MathOverflowError):
        checked_mul(1e15, 1e15)


def test_invalid_percentage_in_chain_raises():
    with pytest.raises(BoundaryViolationError):
        chain_fractional_percentages(100, [20000])


def test_negative_total_percentage_raises():
    with pytest.raises(BoundaryViolationError):
        chain_fractional_percentages(100, [-150])


def test_precision_loss_on_non_representable_float_raises():
    # 2**53 + 1 is not exactly representable as IEEE-754 float.
    with pytest.raises(PrecisionLossError):
        validate_metric_value(9007199254740993)


def test_non_finite_input_raises():
    with pytest.raises(BoundaryViolationError):
        validate_metric_value(math.inf)


def test_bool_operand_raises():
    with pytest.raises(BoundaryViolationError):
        checked_mul(True, 2)
