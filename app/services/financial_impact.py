"""Financial impact engine.

This module answers "what did the attack cost, and what did the defense save?" — a
question that is easy to get wrong. Three rules keep the numbers honest:

1. **Transaction value is not loss.** Only value that left the bank is a realized
   loss. A blocked transaction never settles, so it is *prevented*, not lost.
2. **Nothing is counted twice.** ``exposure`` (everything the scenario touched),
   ``unauthorized`` (settled fraudulent transfers) and ``prevented`` (blocked or
   held before settlement) are disjoint sets.
3. **Every rupee is an integer of paise.** All arithmetic is done on integers of
   minor units and converted with :mod:`app.utils.money`, never with binary floats.

Costs that are *not* transfers are modelled as explicit rates, documented in
:data:`ASSUMPTIONS` and shown in the UI next to the number they produced:

``DISRUPTION_RATE``
    Friction caused by the defense stopping traffic: manual review time and customer
    contact. Modelled at 0.5% of everything the defense held or blocked — fraudulent
    value it prevented *and* legitimate value it interrupted.
``RECOVERY_RATE``
    10% of the realized unauthorized value spent on chargebacks, forensics and
    customer remediation.
``INCIDENT_HANDLING_MINOR``
    A flat 250.00 INR of analyst time per incident, regardless of size.

The fourth bucket, ``interrupted``, is what makes the comparison honest: a defense
that blocks everything prevents every loss but interrupts a lot of legitimate
business, and that friction has a cost. Leaving it out would make "block everything"
look free.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.core.logging import get_logger
from app.utils.money import MINOR_UNITS_PER_MAJOR, minor_units_to_float

logger = get_logger(__name__)

DISRUPTION_RATE = Decimal("0.005")
RECOVERY_RATE = Decimal("0.10")
INCIDENT_HANDLING_MINOR = 25_000  # 250.00 INR

ASSUMPTIONS = [
    "All amounts are integers of paise (1 INR = 100 paise); no binary floating point is used.",
    "exposure = value of every transaction the scenario touched, whether it settled or not.",
    "unauthorized = value of transactions that settled and carry a fraud label. This is the realized loss.",
    "prevented = value of fraudulent transactions blocked or held for review before settlement. "
    "It is a simulated avoided loss, not money that moved.",
    "interrupted = value of legitimate transactions blocked or held for review before settlement. "
    "It is the false-positive cost, measured in value rather than in counts.",
    f"disruption_cost = {DISRUPTION_RATE * 100:.1f}% x (unauthorized + prevented + interrupted): review and "
    "customer-friction cost of operating the defense.",
    f"recovery_cost = {RECOVERY_RATE * 100:.0f}% x unauthorized + "
    f"{INCIDENT_HANDLING_MINOR / MINOR_UNITS_PER_MAJOR:.2f} INR per incident.",
    "net_impact = unauthorized + disruption_cost + recovery_cost - prevented. "
    "A negative value means the defense saved more than the event cost.",
    "A blocked-before-settlement transaction is NOT counted as a loss; it appears only in "
    "prevented value (if fraudulent) or interrupted value (if legitimate).",
    "The four buckets are disjoint, so no value is counted twice.",
    "These are modelled estimates from a synthetic scenario, not audited financial statements.",
]


@dataclass
class ImpactBreakdown:
    exposure_minor: int = 0
    unauthorized_minor: int = 0
    prevented_minor: int = 0
    interrupted_minor: int = 0
    disruption_cost_minor: int = 0
    recovery_cost_minor: int = 0
    net_impact_minor: int = 0
    assumptions: list[str] = field(default_factory=lambda: list(ASSUMPTIONS))

    def to_dict(self) -> dict:
        """Serialisable form. Adds human-readable major-unit twins of every ``_minor`` key."""
        payload = asdict(self)
        for key in (
            "exposure",
            "unauthorized",
            "prevented",
            "interrupted",
            "disruption_cost",
            "recovery_cost",
            "net_impact",
        ):
            payload[key] = minor_units_to_float(payload[f"{key}_minor"])
        return payload

    def column_values(self) -> dict:
        """Only the ``_minor`` columns of ``FinancialImpact``, plus the assumptions.

        :meth:`to_dict` also returns display fields, which are not database columns;
        spreading it into the ORM constructor would fail. This is the safe accessor
        for persistence.
        """
        return {
            "exposure_minor": self.exposure_minor,
            "unauthorized_minor": self.unauthorized_minor,
            "prevented_minor": self.prevented_minor,
            "interrupted_minor": self.interrupted_minor,
            "disruption_cost_minor": self.disruption_cost_minor,
            "recovery_cost_minor": self.recovery_cost_minor,
            "net_impact_minor": self.net_impact_minor,
            "assumptions": list(self.assumptions),
        }


def _apply_rate(amount_minor: int, rate: Decimal) -> int:
    """Multiply an integer amount by a rate, rounding half-up to the nearest paisa."""
    return int((Decimal(int(amount_minor)) * rate).to_integral_value(rounding=ROUND_HALF_UP))


def compute_impact(
    *,
    exposure_minor: int,
    unauthorized_minor: int,
    prevented_minor: int,
    interrupted_minor: int = 0,
    incident_count: int = 0,
) -> ImpactBreakdown:
    """Compute the impact breakdown from the four disjoint value buckets.

    ``unauthorized`` and ``prevented`` are fraudulent value (settled vs stopped);
    ``interrupted`` is legitimate value the defense stopped by mistake. All three are
    subsets of ``exposure`` and none overlap, so nothing is counted twice.
    """
    exposure = max(int(exposure_minor), 0)
    unauthorized = max(int(unauthorized_minor), 0)
    prevented = max(int(prevented_minor), 0)
    interrupted = max(int(interrupted_minor), 0)
    incidents = max(int(incident_count), 0)

    # Guard against a caller double counting: prevented and interrupted value is, by
    # definition, value that did not settle, so the three stopped buckets may never
    # add up to more than the exposure.
    if unauthorized + prevented + interrupted > exposure:
        logger.warning(
            "Stopped value (%s + %s + %s) exceeded exposure (%s); clamping the stopped buckets. "
            "This usually means the caller passed overlapping buckets.",
            unauthorized,
            prevented,
            interrupted,
            exposure,
        )
        allowed = max(exposure - unauthorized, 0)
        if prevented > allowed:
            prevented = allowed
            interrupted = 0
        else:
            interrupted = min(interrupted, allowed - prevented)

    disruption = _apply_rate(unauthorized + prevented + interrupted, DISRUPTION_RATE)
    recovery = _apply_rate(unauthorized, RECOVERY_RATE) + incidents * INCIDENT_HANDLING_MINOR
    net = unauthorized + disruption + recovery - prevented

    return ImpactBreakdown(
        exposure_minor=exposure,
        unauthorized_minor=unauthorized,
        prevented_minor=prevented,
        interrupted_minor=interrupted,
        disruption_cost_minor=disruption,
        recovery_cost_minor=recovery,
        net_impact_minor=net,
    )


def impact_row_to_dict(row) -> dict:
    """Serialise a persisted ``FinancialImpact`` row, adding its display twins.

    The database stores only the ``_minor`` (paise) columns. The major-unit values
    are derived here so neither the API layer nor the browser has to divide by 100.
    """
    payload = {
        "id": row.id,
        "run_id": row.run_id,
        "incident_id": row.incident_id,
        "assumptions": list(row.assumptions or []),
    }
    for key in (
        "exposure",
        "unauthorized",
        "prevented",
        "interrupted",
        "disruption_cost",
        "recovery_cost",
        "net_impact",
    ):
        payload[f"{key}_minor"] = int(getattr(row, f"{key}_minor"))
        payload[key] = minor_units_to_float(payload[f"{key}_minor"])
    return payload


def formula_reference() -> list[dict]:
    """Formula + assumption list, surfaced in Settings -> Documentation."""
    return [
        {
            "metric": "total value exposed",
            "formula": "sum(amount) over every transaction the scenario touched",
            "assumption": "Includes transactions that were blocked, so it is an upper bound on what "
                          "could have been lost.",
        },
        {
            "metric": "estimated unauthorized transfer value",
            "formula": "sum(amount) where status = SETTLED and the synthetic label is fraud",
            "assumption": "Counted as realized loss. Ground truth comes from the scenario generator, "
                          "never from the detector.",
        },
        {
            "metric": "simulated loss prevented",
            "formula": "sum(amount) where status = BLOCKED or PENDING_REVIEW and the synthetic label is fraud",
            "assumption": "Blocked before settlement, therefore never a realized loss. Reported as "
                          "avoided loss, not as money moved.",
        },
        {
            "metric": "interrupted legitimate value",
            "formula": "sum(amount) where status = BLOCKED or PENDING_REVIEW and the synthetic label is not fraud",
            "assumption": "The value cost of false positives. Counted in the disruption base, never as a loss.",
        },
        {
            "metric": "estimated service disruption cost",
            "formula": f"{DISRUPTION_RATE * 100:.1f}% x (unauthorized + prevented + interrupted)",
            "assumption": "Modelled friction: manual review and customer contact caused by the defense.",
        },
        {
            "metric": "estimated recovery cost",
            "formula": f"{RECOVERY_RATE * 100:.0f}% x unauthorized + "
                       f"{INCIDENT_HANDLING_MINOR / MINOR_UNITS_PER_MAJOR:.2f} INR per incident",
            "assumption": "Chargebacks, forensics and customer remediation, plus flat analyst time.",
        },
        {
            "metric": "net simulated impact",
            "formula": "unauthorized + disruption + recovery - prevented",
            "assumption": "Negative means the defense saved more than the incident cost. Positive "
                          "means it did not.",
        },
    ]
