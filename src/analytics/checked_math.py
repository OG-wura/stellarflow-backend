from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Sequence, Union

Number = Union[int, float, Decimal]

# Upper bound for persisted analytics / pricing metrics (prevents runaway chains).
MAX_ABS_VALUE = Decimal("1e18")

# Reject denominators at or below this magnitude (division by near-zero).
MIN_DENOMINATOR = Decimal("1e-18")

# Allowed percentage operands for chained pricing adjustments.
MIN_PERCENTAGE = Decimal("-100")
MAX_PERCENTAGE = Decimal("10000")

# Guard against unbounded multi-step chains.
MAX_CHAIN_LENGTH = 64


class CheckedMathError(Exception):
    """Base exception for safe-math violations in analytics calculations."""


class MathOverflowError(CheckedMathError):
    """Raised when a result would exceed safe numeric bounds."""


class BoundaryViolationError(CheckedMathError):
    """Raised when operands fall outside allowed ranges."""


class PrecisionLossError(CheckedMathError):
    """Raised when rounding or float coercion would discard significant precision."""


def _to_decimal(value: Number, name: str) -> Decimal:
    """Coerce *value* to a finite Decimal or raise BoundaryViolationError."""
    if isinstance(value, bool):
        raise BoundaryViolationError(f"{name} must be a numeric type, not bool.")

    try:
        if isinstance(value, Decimal):
            decimal_value = value
        elif isinstance(value, int):
            decimal_value = Decimal(value)
        else:
            decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BoundaryViolationError(f"{name} is not a valid number: {value!r}") from exc

    if not decimal_value.is_finite():
        raise BoundaryViolationError(f"{name} must be finite, got {value!r}.")

    return decimal_value


def _assert_within_abs(value: Decimal, *, limit: Decimal, label: str) -> None:
    if value.copy_abs() > limit:
        raise MathOverflowError(
            f"{label} {value} exceeds absolute limit {limit}."
        )


def _finalize(value: Decimal, *, max_abs: Decimal = MAX_ABS_VALUE) -> float:
    """Convert a validated Decimal to float, surfacing overflow / precision loss."""
    _assert_within_abs(value, limit=max_abs, label="Result")

    result = float(value)
    if not math.isfinite(result):
        raise MathOverflowError(f"Float conversion produced non-finite value: {result!r}.")

    # Detect silent truncation when coercing very precise decimals to float.
    round_trip = Decimal(str(result))
    if value != round_trip:
        delta = (value - round_trip).copy_abs()
        is_integral = value == value.to_integral_value()
        if is_integral and delta >= 1:
            raise PrecisionLossError(
                f"Float coercion would lose precision: {value} -> {result}."
            )
        tolerance = max(Decimal("1e-12"), value.copy_abs() * Decimal("1e-12"))
        if delta > tolerance:
            raise PrecisionLossError(
                f"Float coercion would lose precision: {value} -> {result}."
            )

    return result


def validate_metric_value(value: Number, *, name: str = "value") -> float:
    """Validate a metric before persistence; raises on boundary violations."""
    return _finalize(_to_decimal(value, name))


def checked_mul(
    a: Number,
    b: Number,
    *,
    max_abs: Decimal = MAX_ABS_VALUE,
) -> float:
    """Multiply two operands with overflow checks."""
    product = _to_decimal(a, "a") * _to_decimal(b, "b")
    _assert_within_abs(product, limit=max_abs, label="Product")
    return _finalize(product, max_abs=max_abs)


def checked_div(
    numerator: Number,
    denominator: Number,
    *,
    max_abs: Decimal = MAX_ABS_VALUE,
    min_denominator: Decimal = MIN_DENOMINATOR,
) -> float:
    """Divide two operands with near-zero and overflow guards."""
    num = _to_decimal(numerator, "numerator")
    den = _to_decimal(denominator, "denominator")

    if den.copy_abs() < min_denominator:
        raise BoundaryViolationError(
            f"Denominator {denominator!r} is too close to zero."
        )

    quotient = num / den
    _assert_within_abs(quotient, limit=max_abs, label="Quotient")
    return _finalize(quotient, max_abs=max_abs)


def fractional_metric(
    base: Number,
    numerator: Number,
    denominator: Number,
) -> float:
    """Compute ``base * (numerator / denominator)`` for fractional pricing steps."""
    fraction = checked_div(numerator, denominator)
    return checked_mul(base, fraction)


def percentage(
    part: Number,
    whole: Number,
    *,
    decimal_places: int = 2,
) -> float:
    """Return ``(part / whole) * 100`` rounded safely (0 when *whole* is 0)."""
    whole_decimal = _to_decimal(whole, "whole")
    if whole_decimal == 0:
        return 0.0

    raw = checked_mul(checked_div(part, whole), 100)
    return safe_round(raw, decimal_places)


def safe_round(value: Number, decimal_places: int = 2) -> float:
    """Round to *decimal_places* using half-up, rejecting unsafe truncation."""
    if decimal_places < 0:
        raise BoundaryViolationError(
            f"decimal_places must be non-negative, got {decimal_places}."
        )

    decimal_value = _to_decimal(value, "value")
    quantizer = Decimal(1).scaleb(-decimal_places)
    rounded = decimal_value.quantize(quantizer, rounding=ROUND_HALF_UP)
    return _finalize(rounded)


def multiply_fractional_metrics(
    *factors: Number,
    max_abs: Decimal = MAX_ABS_VALUE,
) -> float:
    """Checked product of multiple fractional metric factors."""
    if not factors:
        raise BoundaryViolationError("At least one factor is required.")

    if len(factors) > MAX_CHAIN_LENGTH:
        raise BoundaryViolationError(
            f"Cannot multiply more than {MAX_CHAIN_LENGTH} factors at once."
        )

    product = Decimal(1)
    for index, factor in enumerate(factors):
        product *= _to_decimal(factor, f"factors[{index}]")
        _assert_within_abs(product, limit=max_abs, label="Intermediate product")

    return _finalize(product, max_abs=max_abs)


def chain_fractional_percentages(
    base: Number,
    adjustments: Iterable[Number],
) -> float:
    """Apply multi-step percentage adjustments: ``base * Π(1 + pct/100)``.

    Each entry in *adjustments* is a percentage delta (e.g. ``2.5`` → +2.5%).
    """
    adjustment_list = list(adjustments)
    if len(adjustment_list) > MAX_CHAIN_LENGTH:
        raise BoundaryViolationError(
            f"Cannot chain more than {MAX_CHAIN_LENGTH} percentage adjustments."
        )

    result = _to_decimal(base, "base")

    for index, pct in enumerate(adjustment_list):
        pct_decimal = _to_decimal(pct, f"adjustments[{index}]")
        if pct_decimal < MIN_PERCENTAGE or pct_decimal > MAX_PERCENTAGE:
            raise BoundaryViolationError(
                f"Percentage {pct} is outside allowed range "
                f"[{MIN_PERCENTAGE}, {MAX_PERCENTAGE}]."
            )

        factor = Decimal(1) + (pct_decimal / Decimal(100))
        if factor <= 0:
            raise BoundaryViolationError(
                f"Percentage adjustment {pct}% would zero or invert the metric."
            )

        result *= factor
        _assert_within_abs(result, limit=MAX_ABS_VALUE, label="Chained result")

    return _finalize(result)


__all__ = [
    "MAX_ABS_VALUE",
    "MIN_DENOMINATOR",
    "MIN_PERCENTAGE",
    "MAX_PERCENTAGE",
    "MAX_CHAIN_LENGTH",
    "CheckedMathError",
    "MathOverflowError",
    "BoundaryViolationError",
    "PrecisionLossError",
    "validate_metric_value",
    "checked_mul",
    "checked_div",
    "fractional_metric",
    "percentage",
    "safe_round",
    "multiply_fractional_metrics",
    "chain_fractional_percentages",
]
