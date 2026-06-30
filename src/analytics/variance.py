from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence, Union

from .math_scaler import SCALE_14, SCALE_7, scale_up

Number = Union[int, float, Decimal]


@dataclass(frozen=True)
class VarianceParameters:
    """Integer-backed variance descriptors for consensus inputs."""

    count: int
    mean_scaled: int
    variance_numerator: int
    variance_denominator: int
    mean: Decimal
    variance: Decimal


class IntegerVarianceEngine:
    """Compute variance from decimal inputs without exposing floating-point math."""

    def __init__(self, scale: int = SCALE_7) -> None:
        if scale <= 0:
            raise ValueError("scale must be a positive integer")
        self.scale = scale

    def _to_scaled_int(self, value: Number) -> int:
        return scale_up(value, self.scale)

    def compute(self, values: Iterable[Number]) -> VarianceParameters:
        values_list = list(values)
        if not values_list:
            raise ValueError("at least one value is required")

        scaled_values = [self._to_scaled_int(value) for value in values_list]
        count = len(scaled_values)
        sum_scaled = sum(scaled_values)
        mean_scaled = sum_scaled // count

        mean = Decimal(mean_scaled) / Decimal(self.scale)
        variance_numerator = sum((scaled - mean_scaled) ** 2 for scaled in scaled_values)
        variance_denominator = count
        variance = (
            Decimal(variance_numerator)
            / Decimal(variance_denominator)
            / Decimal(self.scale * self.scale)
        )

        return VarianceParameters(
            count=count,
            mean_scaled=mean_scaled,
            variance_numerator=variance_numerator,
            variance_denominator=variance_denominator,
            mean=mean,
            variance=variance,
        )


def parse_consensus_variance(values: Sequence[Number] | Iterable[Number]) -> VarianceParameters:
    """Compatibility entry point for consensus variance parsing."""

    return IntegerVarianceEngine().compute(values)


__all__ = [
    "SCALE_14",
    "SCALE_7",
    "IntegerVarianceEngine",
    "VarianceParameters",
    "parse_consensus_variance",
]
