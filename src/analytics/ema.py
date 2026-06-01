from __future__ import annotations

from typing import Iterable, Iterator, Optional

DEFAULT_SMOOTHING_PERIOD = 10


def smoothing_factor(period: int = DEFAULT_SMOOTHING_PERIOD) -> float:
    """Return the EMA smoothing factor for the given period."""
    if period <= 0:
        raise ValueError("smoothing period must be a positive integer")

    return 2.0 / (period + 1.0)


def progressive_smoothing_factor(sample_count: int) -> float:
    """Return a progressive smoothing factor based on the number of samples seen."""
    if sample_count < 1:
        raise ValueError("sample_count must be at least 1")

    return 2.0 / (sample_count + 1.0)


def update_ema(
    price: float,
    previous_ema: Optional[float] = None,
    smoothing_period: int = DEFAULT_SMOOTHING_PERIOD,
    progressive: bool = False,
    sample_count: Optional[int] = None,
) -> float:
    """Update an exponential moving average for a new incoming price point.

    This function operates inline and does not require access to the full
    historical price pool. Pass `previous_ema=None` to initialize the EMA
    with the first price point.

    If `progressive=True`, smoothing is recomputed for each update using the
    total sample count. Otherwise, a fixed smoothing period is used.
    """
    if previous_ema is None:
        return price

    if progressive:
        if sample_count is None:
            raise ValueError("sample_count is required for progressive EMA updates")
        alpha = progressive_smoothing_factor(sample_count)
    else:
        alpha = smoothing_factor(smoothing_period)

    return alpha * price + (1.0 - alpha) * previous_ema


class RollingEMA:
    """Inline rolling EMA helper that updates with each incoming price."""

    def __init__(
        self,
        smoothing_period: int = DEFAULT_SMOOTHING_PERIOD,
        progressive: bool = False,
        initial_value: Optional[float] = None,
    ) -> None:
        self.smoothing_period = smoothing_period
        self.progressive = progressive
        self.alpha = smoothing_factor(smoothing_period)
        self._value = initial_value
        self.count = 1 if initial_value is not None else 0

    def update(self, price: float) -> float:
        """Apply the next price point and return the updated EMA."""
        if self._value is None:
            self._value = price
            self.count = 1
            return self._value

        self.count += 1
        alpha = (
            progressive_smoothing_factor(self.count)
            if self.progressive
            else self.alpha
        )
        self._value = alpha * price + (1.0 - alpha) * self._value
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value


def ema_sequence(
    prices: Iterable[float],
    smoothing_period: int = DEFAULT_SMOOTHING_PERIOD,
    progressive: bool = False,
) -> Iterator[float]:
    """Yield rolling EMA values for an iterable of prices."""
    ema: Optional[float] = None
    count = 0

    for price in prices:
        count += 1
        if ema is None:
            ema = price
        else:
            alpha = (
                progressive_smoothing_factor(count) if progressive else smoothing_factor(smoothing_period)
            )
            ema = alpha * price + (1.0 - alpha) * ema
        yield ema


__all__ = [
    "DEFAULT_SMOOTHING_PERIOD",
    "smoothing_factor",
    "progressive_smoothing_factor",
    "update_ema",
    "RollingEMA",
    "ema_sequence",
]


if __name__ == "__main__":
    example_prices = [100.0, 102.5, 101.0, 103.2, 104.0]
    ema = RollingEMA(progressive=True)

    print("Rolling EMA values for example prices:")
    for price in example_prices:
        print(f"price={price:.2f}, ema={ema.update(price):.4f}")
