import pytest
from src.analytics.math_scaler import fixed_point_sqrt, to_fixed, from_fixed, calculate_slippage_variance

def test_fixed_point_sqrt_precision_matches():
    """
    Verifies that the integer-only square root accurately matches mathematical
    square roots without relying on floating-point primitives.
    """
    # Test flat integer square root of 4 (scaled) -> should be 2 (scaled)
    val_4 = to_fixed(4.0)
    res_2 = fixed_point_sqrt(val_4)
    assert from_fixed(res_2) == 2.0

    # Test irrational root: sqrt(2.0) -> ~1.4142135
    val_2 = to_fixed(2.0)
    res_sqrt2 = fixed_point_sqrt(val_2)
    assert from_fixed(res_sqrt2) == pytest.approx(1.4142135, abs=1e-7)

    # Test small fractional numbers: sqrt(0.09) -> 0.30
    val_09 = to_fixed(0.09)
    res_03 = fixed_point_sqrt(val_09)
    assert from_fixed(res_03) == pytest.approx(0.30, abs=1e-7)

def test_fixed_point_sqrt_edge_cases():
    """Ensures boundary constraints handle zero and throw error exceptions on negative inputs."""
    assert fixed_point_sqrt(0) == 0
    
    with pytest.raises(ValueError, match="Cannot compute the fixed-point square root"):
        fixed_point_sqrt(-to_fixed(5.0))

def test_slippage_variance_calculation():
    """Asserts that calculated price variations match strict expected fixed-space outputs."""
    expected = to_fixed(100.0)
    current = to_fixed(105.0) # 5% upward slippage trend deviation
    
    variance = calculate_slippage_variance(current, expected)
    assert from_fixed(variance) == pytest.approx(0.05, abs=1e-7)