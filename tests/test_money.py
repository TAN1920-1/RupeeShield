"""Currency-safe arithmetic.

Money in this project is integer paise and ``Decimal``; these tests pin that down,
because a silent float regression would corrupt every financial figure in the UI
without anything failing visibly.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils.money import (
    MINOR_UNITS_PER_MAJOR,
    clamp_minor_units,
    from_minor_units,
    minor_units_to_float,
    quantize_amount,
    sum_minor_units,
    to_decimal,
    to_minor_units,
)


def test_binary_float_error_is_avoided_in_minor_units():
    # The classic failure case: as floats this is 0.30000000000000004.
    assert 0.1 + 0.2 != 0.3

    total = to_minor_units(0.1) + to_minor_units(0.2)
    assert total == to_minor_units(0.3) == 30


def test_minor_unit_round_trip_is_exact():
    for amount in (0.01, 1.0, 19.99, 1234.56, 100_000.00):
        assert from_minor_units(to_minor_units(amount)) == quantize_amount(amount)


def test_major_and_minor_units_are_related_by_the_defined_factor():
    assert MINOR_UNITS_PER_MAJOR == 100
    assert to_minor_units(12.34) == 1234
    assert from_minor_units(1234) == Decimal("12.34")
    assert minor_units_to_float(1234) == 12.34


def test_quantize_rounds_half_up_not_to_even():
    assert quantize_amount("2.005") == Decimal("2.01")
    assert quantize_amount("2.015") == Decimal("2.02")


@pytest.mark.parametrize("value", [None, "not a number", object()])
def test_invalid_values_fall_back_instead_of_raising(value):
    assert to_decimal(value) == Decimal("0.00")


def test_float_input_is_routed_through_str_so_decimals_stay_decimal():
    # Decimal(0.1) would be 0.1000000000000000055511151231257827...
    assert to_decimal(0.1) == Decimal("0.1")


def test_sum_of_minor_units_is_exact():
    amounts = [to_minor_units(0.1) for _ in range(10)]
    assert sum_minor_units(amounts) == 100
    assert from_minor_units(sum_minor_units(amounts)) == Decimal("1.00")


def test_clamp_never_returns_a_negative_value():
    assert clamp_minor_units(-500) == 0
    assert clamp_minor_units(500) == 500
    assert clamp_minor_units(-500, minimum=-1000) == -500
