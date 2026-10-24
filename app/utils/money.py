"""Currency-safe arithmetic helpers.

Money is never stored or summed as ``float`` anywhere in this project:

* in Python, all monetary maths is done with :class:`decimal.Decimal`;
* in the database, monetary columns are ``INTEGER`` **minor units** (paise,
  i.e. 1 INR = 100 paise).

Why this matters: ``0.1 + 0.2 != 0.3`` in binary floating point, so summing a few
thousand simulated transfers as floats accumulates error that would make the
financial-impact numbers in the Defense Comparison Lab quietly wrong. Integers of
minor units are exact, and ``Decimal`` keeps the boundary conversions exact too.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

MINOR_UNITS_PER_MAJOR = 100
TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value: object, default: Decimal = ZERO) -> Decimal:
    """Coerce a value to ``Decimal``, falling back to ``default`` when it is invalid.

    Accepts ``Decimal``, ``int``, ``str`` and ``float`` (floats are routed through
    ``str()`` so that ``0.1`` becomes ``Decimal("0.1")`` rather than the full binary
    expansion of the float).
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return default
    try:
        if isinstance(value, float):
            return Decimal(str(value))
        return Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError):
        return default


def quantize_amount(value: object) -> Decimal:
    """Round to 2 decimal places using banker-free half-up rounding."""
    return to_decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def to_minor_units(value: object) -> int:
    """Convert a major-unit amount to integer minor units (paise)."""
    return int((quantize_amount(value) * MINOR_UNITS_PER_MAJOR).to_integral_value(rounding=ROUND_HALF_UP))


def from_minor_units(minor: object) -> Decimal:
    """Convert integer minor units (paise) back to a 2dp ``Decimal`` amount."""
    if minor is None:
        return ZERO
    return (Decimal(int(minor)) / MINOR_UNITS_PER_MAJOR).quantize(TWO_PLACES)


def minor_units_to_float(minor: object) -> float:
    """Presentation-only helper for JSON responses / charts."""
    return float(from_minor_units(minor))


def sum_minor_units(values: Iterable[int]) -> int:
    """Exact sum of minor-unit values."""
    return sum(int(v) for v in values)


def clamp_minor_units(value: int, minimum: int = 0) -> int:
    return max(int(value), minimum)
