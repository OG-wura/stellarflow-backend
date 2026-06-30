from decimal import Decimal

from src.analytics.variance import IntegerVarianceEngine, parse_consensus_variance


def test_integer_variance_engine_preserves_precision_for_consensus_inputs() -> None:
    engine = IntegerVarianceEngine()
    values = [Decimal("1.2345678"), Decimal("1.2345679"), Decimal("1.2345680")]

    params = engine.compute(values)

    assert params.count == 3
    assert params.mean_scaled == 12_345_679
    assert params.variance_numerator == 2
    assert params.variance_denominator == 3
    assert params.mean == Decimal("1.2345679")
    assert params.variance == Decimal("6.666666666666666666666666667e-15")


def test_parse_consensus_variance_alias_returns_same_engine_result() -> None:
    values = [Decimal("0.1"), Decimal("0.2"), Decimal("0.3")]

    params = parse_consensus_variance(values)

    assert params.count == 3
    assert params.mean_scaled == 200_000_0
    assert params.variance_numerator == 2
    assert params.variance_denominator == 3
