import logging

logger = logging.getLogger("Analytics.MathScaler")

# Uniform fixed-point scaling factor (10^7 footprint)
SCALE = 10_000_000

def to_fixed(value: float) -> int:
    """Converts a standard float value into a scaled 10^7 fixed-point integer."""
    return int(round(value * SCALE))

def from_fixed(value: int) -> float:
    """Converts a scaled 10^7 fixed-point integer back into a standard float."""
    return float(value) / SCALE

def fixed_point_sqrt(x: int) -> int:
    """
    Computes the square root of a 10^7 scaled fixed-point integer using an
    integer-only binary search strategy to prevent floating-point drift.
    
    Formula: target = sqrt(x * SCALE)
    """
    if x < 0:
        logger.error(f"Mathematical domain error: Attempted sqrt calculation on negative variance: {x}")
        raise ValueError("Cannot compute the fixed-point square root of a negative value.")
    if x == 0:
        return 0

    # Adjust the radicand by the scaling footprint to maintain 10^7 scale in the result
    target = x * SCALE
    
    # Binary search boundaries for integer square root evaluation
    low = 1
    high = target
    ans = 1

    while low <= high:
        mid = (low + high) // 2
        mid_squared = mid * mid

        if mid_squared == target:
            return mid
        elif mid_squared < target:
            low = mid + 1
            ans = mid  # Keep track of the closest floor integer match
        else:
            high = mid - 1

    # Rounding step: check if the next consecutive integer is closer to the true value
    if (ans + 1) * (ans + 1) - target < target - ans * ans:
        ans += 1

    return ans

def calculate_slippage_variance(current_price: int, expected_price: int) -> int:
    """
    Calculates slippage variance tracking metrics using the fixed-point square root.
    Returns the deviation metric cleanly within the uniform 10^7 boundary.
    """
    if expected_price <= 0:
        raise ValueError("Expected price must be greater than zero for variance analytics.")
        
    # Scale difference: delta = ((current - expected) / expected) ^ 2
    delta = ((current_price - expected_price) * SCALE) // expected_price
    variance_input = (delta * delta) // SCALE
    
    return fixed_point_sqrt(variance_input)