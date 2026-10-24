"""Financial-impact engine invariants.

These tests pin the *arithmetic* of the cost model, not its inputs: the whole
point of the engine is that a blocked transaction is a *prevented* loss and never
an *unauthorized* one, that no rupee is counted twice, and that every figure is an
exact integer of paise rather than an accumulated float. A silent regression here
would make the Defense Comparison Lab's headline numbers wrong while every other
test still passed, which is exactly why the formula is re-derived here by hand.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.services.financial_impact import (
    ASSUMPTIONS,
    DISRUPTION_RATE,
    INCIDENT_HANDLING_MINOR,
    RECOVERY_RATE,
    compute_impact,
)
from app.utils.money import MINOR_UNITS_PER_MAJOR, minor_units_to_float

#: The seven monetary metrics, each of which must appear in both minor and major units.
METRIC_KEYS = [
    "exposure",
    "unauthorized",
    "prevented",
    "interrupted",
    "disruption_cost",
    "recovery_cost",
    "net_impact",
]


def _expected_net(unauthorized: int, prevented: int, interrupted: int, incidents: int) -> int:
    """The documented formula, recomputed independently with Decimal half-up rounding."""
    disruption = int(
        (Decimal(unauthorized + prevented + interrupted) * DISRUPTION_RATE).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    recovery = int(
        (Decimal(unauthorized) * RECOVERY_RATE).to_integral_value(rounding=ROUND_HALF_UP)
    ) + incidents * INCIDENT_HANDLING_MINOR
    return unauthorized + disruption + recovery - prevented


def test_blocked_fraud_is_prevented_and_never_unauthorized():
    fraud_value = 500_000

    blocked = compute_impact(
        exposure_minor=fraud_value,
        unauthorized_minor=0,
        prevented_minor=fraud_value,
        incident_count=1,
    )
    # A fraudulent transaction stopped before settlement appears only in prevented.
    assert blocked.unauthorized_minor == 0
    assert blocked.prevented_minor == fraud_value

    settled = compute_impact(
        exposure_minor=fraud_value,
        unauthorized_minor=fraud_value,
        prevented_minor=0,
        incident_count=1,
    )
    assert settled.unauthorized_minor == fraud_value
    assert settled.prevented_minor == 0

    # Same fraud value, but stopping it is strictly cheaper than letting it settle.
    assert blocked.net_impact_minor < settled.net_impact_minor


def test_buckets_never_exceed_total_exposure():
    # Deliberately overlapping buckets: the engine must clamp rather than double count.
    overlapping = compute_impact(
        exposure_minor=1_000,
        unauthorized_minor=600,
        prevented_minor=600,
        interrupted_minor=0,
    )
    assert (
        overlapping.unauthorized_minor
        + overlapping.prevented_minor
        + overlapping.interrupted_minor
        <= overlapping.exposure_minor
    )
    assert overlapping.unauthorized_minor == 600
    assert overlapping.prevented_minor == 400
    assert overlapping.interrupted_minor == 0

    # Three stopped buckets that together overshoot the unaccounted exposure.
    over_interrupted = compute_impact(
        exposure_minor=1_000,
        unauthorized_minor=200,
        prevented_minor=400,
        interrupted_minor=800,
    )
    assert (
        over_interrupted.unauthorized_minor
        + over_interrupted.prevented_minor
        + over_interrupted.interrupted_minor
        == 1_000
    )
    assert over_interrupted.interrupted_minor == 400


def test_nonsensical_inputs_never_produce_negative_buckets():
    result = compute_impact(
        exposure_minor=-10_000,
        unauthorized_minor=-1,
        prevented_minor=-1,
        interrupted_minor=-1,
        incident_count=-3,
    )
    for key in METRIC_KEYS:
        # net_impact may legitimately be negative, but never a *bucket*.
        if key == "net_impact":
            continue
        assert getattr(result, f"{key}_minor") >= 0, key
    assert result.exposure_minor == 0
    assert result.unauthorized_minor == 0
    assert result.prevented_minor == 0
    assert result.interrupted_minor == 0
    # No incidents means no flat incident-handling cost.
    assert result.recovery_cost_minor == 0


def test_every_monetary_output_is_an_integer_paise_with_a_major_twin():
    breakdown = compute_impact(
        exposure_minor=1_234_567,
        unauthorized_minor=200_000,
        prevented_minor=300_000,
        interrupted_minor=50_000,
        incident_count=2,
    )
    payload = breakdown.to_dict()

    for key in METRIC_KEYS:
        minor_value = payload[f"{key}_minor"]
        assert isinstance(minor_value, int)
        # net_impact is a balance that can legitimately be negative; the buckets cannot.
        if key != "net_impact":
            assert minor_value >= 0, key
        # The major-unit twin is exactly minor / 100 as computed by the money helper.
        assert payload[key] == approx_minor(minor_value)
        assert payload[key] == minor_units_to_float(minor_value)


def approx_minor(minor: int) -> float:
    """``minor / 100`` as a float, matching the money helper's contract exactly."""
    return minor / MINOR_UNITS_PER_MAJOR


def test_net_impact_matches_the_documented_formula_exactly():
    unauthorized = 777_777
    prevented = 111_111
    interrupted = 33_333
    incidents = 4

    result = compute_impact(
        exposure_minor=unauthorized + prevented + interrupted,
        unauthorized_minor=unauthorized,
        prevented_minor=prevented,
        interrupted_minor=interrupted,
        incident_count=incidents,
    )

    expected = _expected_net(unauthorized, prevented, interrupted, incidents)
    assert result.net_impact_minor == expected
    assert result.disruption_cost_minor == int(
        (Decimal(unauthorized + prevented + interrupted) * DISRUPTION_RATE).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    assert result.recovery_cost_minor == int(
        (Decimal(unauthorized) * RECOVERY_RATE).to_integral_value(rounding=ROUND_HALF_UP)
    ) + incidents * INCIDENT_HANDLING_MINOR


def test_defended_scenario_has_strictly_lower_net_impact_than_undefended():
    fraud_value = 900_000
    no_defense = compute_impact(
        exposure_minor=fraud_value,
        unauthorized_minor=fraud_value,
        prevented_minor=0,
        incident_count=1,
    )
    with_defense = compute_impact(
        exposure_minor=fraud_value,
        unauthorized_minor=0,
        prevented_minor=fraud_value,
        incident_count=1,
    )
    assert with_defense.unauthorized_minor == 0
    assert with_defense.prevented_minor == fraud_value
    assert with_defense.net_impact_minor < no_defense.net_impact_minor
    # The undefended run's net impact is positive: the fraud was a real cost.
    assert no_defense.net_impact_minor > 0


def test_serialised_payload_exposes_minor_and_major_units_and_caveats():
    payload = compute_impact(
        exposure_minor=10_000, unauthorized_minor=10_000, prevented_minor=0
    ).to_dict()

    for key in METRIC_KEYS:
        assert f"{key}_minor" in payload
        assert key in payload

    assumptions = payload["assumptions"]
    assert assumptions == ASSUMPTIONS
    assert any("paise" in text for text in assumptions)
    assert any("no value is counted twice" in text for text in assumptions)
    assert any(text.startswith("net_impact =") for text in assumptions)
    assert any("not audited financial statements" in text for text in assumptions)


def test_apply_rate_rounds_half_up_to_nearest_paisa():
    # 0.5% of 100 paise is exactly 0.5 paise; half-up rounds it to 1, not to 0 (banker's).
    half = compute_impact(
        exposure_minor=100, unauthorized_minor=100, prevented_minor=0
    )
    assert half.disruption_cost_minor == 1

    # 0.5% of 99 paise is 0.495 paise, which rounds down to 0.
    below_half = compute_impact(
        exposure_minor=99, unauthorized_minor=99, prevented_minor=0
    )
    assert below_half.disruption_cost_minor == 0

    assert half.disruption_cost_minor >= 0
    assert below_half.disruption_cost_minor >= 0
