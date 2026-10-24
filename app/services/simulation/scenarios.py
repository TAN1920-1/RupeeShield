"""Deterministic scenario scripts.

A :class:`ScenarioPlan` is the *script* of an attack: which accounts take part, the
exact transactions, the exact security telemetry, and the timing. It is produced
purely from ``(scenario, seed, n_events)`` and contains no run identifier, no
database row and no dependency on the defense configuration.

That separation is the whole reason the Defense Comparison Lab can be trusted: the
same plan is replayed against "no defense", "rules only" and "rules + ML +
correlation", so the only thing that differs between the three results is the
defense itself.

Every scenario also generates **cover traffic**: ordinary transactions from
untargeted accounts, including the occasional genuine new device. Without cover
traffic, measuring precision and false-positive rate would be meaningless because
every transaction in the run would be an attack.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np

from app.services import event_types as events
from app.services.simulation import catalog
from app.services.simulation.accounts import (
    ROLE_BASELINE,
    ROLE_BENEFICIARY,
    ROLE_VICTIM,
    PlannedAccount,
    build_roster,
)

ATTACK_CHANNELS = ["CARD_NOT_PRESENT", "UPI_WALLET"]
ROUND_NUMBERS_MINOR = [49_900, 99_900, 199_900, 499_900]
#: Share of legitimate cover transactions made from a genuinely new device, which is
#: a normal customer event and a realistic source of false positives for rule-only
#: defenses. Excluding it would make every defense look perfect.
COVER_NEW_DEVICE_RATE = 0.2


def _to_inr(minor: float) -> int:
    """Round to whole rupees (still integer paise) so amounts look human."""
    return int(round(float(minor) / 100.0)) * 100


@dataclass(frozen=True)
class PlannedTransaction:
    key: str
    account_index: int
    counterparty_index: int | None
    amount_minor: int
    offset_seconds: int
    channel: str
    country: str
    device_id: str
    merchant_id: str
    direction: str = "DEBIT"
    is_fraud: int = 0
    fraud_type: str | None = None


@dataclass(frozen=True)
class PlannedSecurityEvent:
    key: str
    account_index: int
    event_type: str
    severity: str
    offset_seconds: int
    country: str | None = None
    device_id: str | None = None
    device_is_new: bool = False
    details: dict = field(default_factory=dict)
    related_transaction_key: str | None = None


@dataclass
class ScenarioPlan:
    scenario: str
    seed: int
    requested_events: int
    accounts: list[PlannedAccount]
    transactions: list[PlannedTransaction]
    security_events: list[PlannedSecurityEvent]
    narrative: list[str]
    fingerprint: str

    @property
    def attack_transactions(self) -> list[PlannedTransaction]:
        return [txn for txn in self.transactions if txn.is_fraud == 1]

    @property
    def cover_transactions(self) -> list[PlannedTransaction]:
        return [txn for txn in self.transactions if txn.is_fraud == 0]

    def account_by_index(self, index: int) -> PlannedAccount:
        return self.accounts[index]

    def to_summary(self) -> dict:
        return {
            "scenario": self.scenario,
            "seed": self.seed,
            "requested_events": self.requested_events,
            "accounts": len(self.accounts),
            "transactions": len(self.transactions),
            "attack_transactions": len(self.attack_transactions),
            "cover_transactions": len(self.cover_transactions),
            "security_events": len(self.security_events),
            "fingerprint": self.fingerprint,
        }


def _fingerprint(plan_payload: dict) -> str:
    canonical = json.dumps(plan_payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Scenario constructors
# ---------------------------------------------------------------------------
def _account_takeover(
    rng: np.random.Generator,
    roster: list[PlannedAccount],
    n_events: int,
) -> tuple[list[PlannedTransaction], list[PlannedSecurityEvent], list[str]]:
    transactions: list[PlannedTransaction] = []
    security: list[PlannedSecurityEvent] = []
    victims = [account for account in roster if account.role == ROLE_VICTIM]
    beneficiaries = [account for account in roster if account.role == ROLE_BENEFICIARY]

    spent = 0
    for position, victim in enumerate(victims):
        if spent >= n_events:
            break
        base = 180 + position * 900
        foreign = str(rng.choice([code for code in _country_pool() if code != victim.home_country]))
        device = f"D-ATK-{int(rng.integers(1000, 9999))}"
        beneficiary = beneficiaries[int(rng.integers(0, len(beneficiaries)))]

        security.extend(
            [
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.FAILED_LOGIN, severity="MEDIUM",
                    offset_seconds=base - 150, country=foreign, device_id=device,
                    device_is_new=True, details={"reason": "wrong password", "attempt": 1},
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.FAILED_LOGIN, severity="MEDIUM",
                    offset_seconds=base - 120, country=foreign, device_id=device,
                    device_is_new=True, details={"reason": "wrong password", "attempt": 2},
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.IMPOSSIBLE_TRAVEL, severity="HIGH",
                    offset_seconds=base - 60, country=foreign, device_id=device, device_is_new=True,
                    details={"previous_country": victim.home_country, "impossible_travel": True},
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.NEW_DEVICE, severity="MEDIUM",
                    offset_seconds=base - 20, country=foreign, device_id=device, device_is_new=True,
                    details={"device_first_seen": True},
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.LOGIN_NEW_DEVICE, severity="HIGH",
                    offset_seconds=base - 10, country=foreign, device_id=device, device_is_new=True,
                    details={"login_success": True},
                ),
            ]
        )
        spent += 5

        count = int(rng.integers(3, 6))
        for step in range(count):
            if spent >= n_events + 8:
                break
            transactions.append(
                PlannedTransaction(
                    key=f"T{len(transactions):04d}",
                    account_index=victim.index,
                    counterparty_index=beneficiary.index,
                    amount_minor=_to_inr(victim.avg_txn_amount_minor * float(rng.uniform(4.0, 12.0))),
                    offset_seconds=base + 60 + step * 180,
                    channel=str(rng.choice(ATTACK_CHANNELS)),
                    country=foreign,
                    device_id=device,
                    merchant_id=f"M{int(rng.integers(1000, 9999))}",
                    is_fraud=1,
                    fraud_type="ACCOUNT_TAKEOVER",
                )
            )
            spent += 1

    narrative = [
        "Credentials for the target accounts were used from a foreign address after two failed attempts.",
        "A new device appeared and an impossible-travel login was recorded before any money moved.",
        "Large transfers then left each account on card-not-present and wallet rails.",
    ]
    return transactions, security, narrative


def _credential_stuffing(
    rng: np.random.Generator,
    roster: list[PlannedAccount],
    n_events: int,
) -> tuple[list[PlannedTransaction], list[PlannedSecurityEvent], list[str]]:
    transactions: list[PlannedTransaction] = []
    security: list[PlannedSecurityEvent] = []
    victims = [account for account in roster if account.role == ROLE_VICTIM]
    beneficiaries = [account for account in roster if account.role == ROLE_BENEFICIARY]

    spent = 0
    attempts_per_account = 6
    for position, victim in enumerate(victims):
        if spent >= n_events:
            break
        base = 120 + position * 420
        foreign = str(rng.choice(_country_pool()))
        device = f"D-STUFF-{int(rng.integers(1000, 9999))}"

        for attempt in range(attempts_per_account):
            security.append(
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.FAILED_LOGIN, severity="MEDIUM",
                    offset_seconds=base + attempt * 15, country=foreign, device_id=device,
                    device_is_new=True,
                    details={"reason": "leaked credential replay", "attempt": attempt + 1},
                )
            )
            spent += 1

        security.append(
            PlannedSecurityEvent(
                key=f"S{len(security):04d}", account_index=victim.index,
                event_type=events.LOGIN_SUCCESS, severity="INFO",
                offset_seconds=base + attempts_per_account * 15, country=foreign, device_id=device,
                device_is_new=True, details={"attempts_before_success": attempts_per_account},
            )
        )
        security.append(
            PlannedSecurityEvent(
                key=f"S{len(security):04d}", account_index=victim.index,
                event_type=events.LOGIN_NEW_DEVICE, severity="HIGH",
                offset_seconds=base + attempts_per_account * 15 + 5, country=foreign,
                device_id=device, device_is_new=True, details={"device_first_seen": True},
            )
        )
        spent += 2

        beneficiary = beneficiaries[int(rng.integers(0, len(beneficiaries)))]
        for step in range(2):
            transactions.append(
                PlannedTransaction(
                    key=f"T{len(transactions):04d}",
                    account_index=victim.index,
                    counterparty_index=beneficiary.index,
                    amount_minor=_to_inr(victim.avg_txn_amount_minor * float(rng.uniform(1.5, 4.0))),
                    offset_seconds=base + 300 + step * 240,
                    channel=str(rng.choice(ATTACK_CHANNELS)),
                    country=victim.home_country,
                    device_id=device,
                    merchant_id=f"M{int(rng.integers(1000, 9999))}",
                    is_fraud=1,
                    fraud_type="CREDENTIAL_STUFFING",
                )
            )
            spent += 1

    narrative = [
        "Each target account received a burst of failed logins from a single unfamiliar device.",
        "One attempt succeeded, which is the moment the credential was proven valid.",
        "Medium-value withdrawals followed immediately from the newly seen device.",
    ]
    return transactions, security, narrative


def _payment_manipulation(
    rng: np.random.Generator,
    roster: list[PlannedAccount],
    n_events: int,
) -> tuple[list[PlannedTransaction], list[PlannedSecurityEvent], list[str]]:
    transactions: list[PlannedTransaction] = []
    security: list[PlannedSecurityEvent] = []
    victims = [account for account in roster if account.role == ROLE_VICTIM]
    beneficiaries = [account for account in roster if account.role == ROLE_BENEFICIARY]

    spent = 0
    for position, victim in enumerate(victims):
        if spent >= n_events:
            break
        base = 240 + position * 780
        beneficiary = beneficiaries[int(rng.integers(0, len(beneficiaries)))]
        device = f"D-BASE-{victim.index:04d}"

        security.extend(
            [
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.BENEFICIARY_ADDED, severity="MEDIUM",
                    offset_seconds=base - 300, country=victim.home_country, device_id=device,
                    details={"beneficiary": f"Account {beneficiary.index:04d}"},
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.BENEFICIARY_CHANGED, severity="HIGH",
                    offset_seconds=base - 120, country=victim.home_country, device_id=device,
                    details={
                        "from": f"Account {beneficiaries[(beneficiary.index + 1) % len(beneficiaries)].index:04d}",
                        "to": f"Account {beneficiary.index:04d}",
                    },
                ),
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.PAYMENT_DETAILS_MODIFIED, severity="HIGH",
                    offset_seconds=base - 60, country=victim.home_country, device_id=device,
                    details={"field": "beneficiary_account", "modified_after_authorisation": True},
                ),
            ]
        )
        spent += 3

        transactions.append(
            PlannedTransaction(
                key=f"T{len(transactions):04d}",
                account_index=victim.index,
                counterparty_index=beneficiary.index,
                amount_minor=_to_inr(victim.avg_txn_amount_minor * float(rng.uniform(3.0, 9.0))),
                offset_seconds=base,
                channel="WIRE_TRANSFER",
                country=victim.home_country,
                device_id=device,
                merchant_id=f"BENEFICIARY_{beneficiary.index:04d}",
                is_fraud=1,
                fraud_type="PAYMENT_MANIPULATION",
            )
        )
        spent += 1

        # Repeated round-number charges to one merchant: the second half of the pattern,
        # designed to sit just under a typical manual-review value.
        merchant = f"M{int(rng.integers(1000, 9999))}"
        for step in range(2):
            transactions.append(
                PlannedTransaction(
                    key=f"T{len(transactions):04d}",
                    account_index=victim.index,
                    counterparty_index=beneficiary.index,
                    amount_minor=int(rng.choice(ROUND_NUMBERS_MINOR)),
                    offset_seconds=base + 600 + step * 600,
                    channel="CARD_NOT_PRESENT",
                    country=victim.home_country,
                    device_id=device,
                    merchant_id=merchant,
                    is_fraud=1,
                    fraud_type="PAYMENT_MANIPULATION",
                )
            )
            spent += 1

    narrative = [
        "A new beneficiary was added to each account, then quietly swapped for another one.",
        "The payment instruction was modified after it had been authorised by the customer.",
        "The released payment left on the wire rail; round-number charges to one merchant followed.",
    ]
    return transactions, security, narrative


def _suspicious_transfer(
    rng: np.random.Generator,
    roster: list[PlannedAccount],
    n_events: int,
) -> tuple[list[PlannedTransaction], list[PlannedSecurityEvent], list[str]]:
    transactions: list[PlannedTransaction] = []
    security: list[PlannedSecurityEvent] = []
    victims = [account for account in roster if account.role == ROLE_VICTIM]
    beneficiaries = [account for account in roster if account.role == ROLE_BENEFICIARY]

    spent = 0
    for position, victim in enumerate(victims):
        if spent >= n_events:
            break
        base = 300 + position * 900
        device = f"D-BASE-{victim.index:04d}"
        inbound = _to_inr(victim.avg_txn_amount_minor * float(rng.uniform(6.0, 18.0)))

        security.append(
            PlannedSecurityEvent(
                key=f"S{len(security):04d}", account_index=victim.index,
                event_type=events.MULE_FUNDING_RECEIVED, severity="HIGH",
                offset_seconds=base - 30, country=victim.home_country, device_id=device,
                details={"amount_minor": inbound, "source": "EXTERNAL"},
            )
        )
        spent += 1

        transactions.append(
            PlannedTransaction(
                key=f"T{len(transactions):04d}",
                account_index=victim.index,
                counterparty_index=None,
                amount_minor=inbound,
                offset_seconds=base,
                channel="WIRE_TRANSFER",
                country=victim.home_country,
                device_id=device,
                merchant_id="EXTERNAL_INBOUND",
                direction="CREDIT",
                is_fraud=1,
                fraud_type="MULE_FANOUT",
            )
        )
        spent += 1

        fanout = int(rng.integers(4, 8))
        share = inbound // fanout
        for step in range(fanout):
            target = beneficiaries[(victim.index + step) % len(beneficiaries)]
            security.append(
                PlannedSecurityEvent(
                    key=f"S{len(security):04d}", account_index=victim.index,
                    event_type=events.NEW_BENEFICIARY, severity="MEDIUM",
                    offset_seconds=base + 120 + step * 90, country=victim.home_country,
                    device_id=device, details={"beneficiary": f"Account {target.index:04d}"},
                )
            )
            transactions.append(
                PlannedTransaction(
                    key=f"T{len(transactions):04d}",
                    account_index=victim.index,
                    counterparty_index=target.index,
                    amount_minor=_to_inr(share * float(rng.uniform(0.85, 1.15))),
                    offset_seconds=base + 150 + step * 90,
                    channel="UPI_WALLET",
                    country=victim.home_country,
                    device_id=device,
                    merchant_id=f"BENEFICIARY_{target.index:04d}",
                    is_fraud=1,
                    fraud_type="MULE_FANOUT",
                )
            )
            spent += 1

        security.append(
            PlannedSecurityEvent(
                key=f"S{len(security):04d}", account_index=victim.index,
                event_type=events.RAPID_FANOUT, severity="HIGH",
                offset_seconds=base + 150 + fanout * 90, country=victim.home_country,
                device_id=device, details={"beneficiaries": fanout, "window_seconds": fanout * 90},
            )
        )
        spent += 1

    narrative = [
        "A large inbound wire landed in each account from an external source.",
        "Within minutes the funds were split across several beneficiaries never seen before.",
        "The outgoing amounts add up to the inbound amount, leaving no value behind.",
    ]
    return transactions, security, narrative


BUILDERS = {
    "ACCOUNT_TAKEOVER": _account_takeover,
    "CREDENTIAL_STUFFING": _credential_stuffing,
    "PAYMENT_MANIPULATION": _payment_manipulation,
    "SUSPICIOUS_TRANSFER": _suspicious_transfer,
}


def _country_pool() -> list[str]:
    from app.services.simulation.accounts import COUNTRIES

    return list(COUNTRIES)


def _cover_traffic(
    rng: np.random.Generator,
    roster: list[PlannedAccount],
    n_events: int,
    offset_start: int,
) -> list[PlannedTransaction]:
    """Ordinary customer activity, so precision and false-positive rate mean something."""
    baseline = [account for account in roster if account.role == ROLE_BASELINE]
    count = max(4, min(len(baseline), n_events // 3))
    transactions: list[PlannedTransaction] = []
    for step in range(count):
        account = baseline[int(rng.integers(0, len(baseline)))]
        new_device = rng.random() < COVER_NEW_DEVICE_RATE
        counterparty = roster[int(rng.integers(0, len(roster)))]
        transactions.append(
            PlannedTransaction(
                key=f"T-C{len(transactions):04d}",
                account_index=account.index,
                counterparty_index=counterparty.index if counterparty.index != account.index else None,
                amount_minor=_to_inr(account.avg_txn_amount_minor * float(rng.uniform(0.3, 1.7))),
                offset_seconds=offset_start + step * 45,
                channel=account.preferred_channel,
                country=account.home_country,
                device_id=(
                    f"D-NEW-{int(rng.integers(1000, 9999))}" if new_device else f"D-BASE-{account.index:04d}"
                ),
                merchant_id=f"M{int(rng.integers(1000, 9999))}",
                is_fraud=0,
                fraud_type=None,
            )
        )
    return transactions


def build_plan(scenario: str, *, seed: int, n_events: int) -> ScenarioPlan:
    """Build the deterministic script for one scenario run.

    Deterministic means: identical ``(scenario, seed, n_events)`` always produces an
    identical plan, so two runs under different defense configurations are directly
    comparable and any result can be reproduced later.
    """
    key = (scenario or "").upper()
    if key not in catalog.SCENARIOS:
        raise KeyError(
            f"Unknown scenario '{scenario}'. Available: {', '.join(catalog.SCENARIO_KEYS)}."
        )
    if n_events < 5:
        raise ValueError("A scenario needs at least 5 events to be meaningful.")

    rng = np.random.default_rng(seed)

    # Victim count scales with the requested size but stays small enough that one run
    # fits comfortably in a laptop's memory and a human can read the incidents.
    victim_count = max(1, min(n_events // 8, 12))
    n_accounts = max(12, victim_count * 3 + 6)
    roster = build_roster(rng, n_accounts, victim_count)

    attack_transactions, security_events, narrative = BUILDERS[key](rng, roster, n_events)

    last_offset = max(
        [txn.offset_seconds for txn in attack_transactions]
        + [event.offset_seconds for event in security_events]
        + [0]
    )
    cover = _cover_traffic(rng, roster, n_events, offset_start=60)

    transactions = sorted(attack_transactions + cover, key=lambda txn: (txn.offset_seconds, txn.key))
    security_events = sorted(security_events, key=lambda event: (event.offset_seconds, event.key))

    narrative = narrative + [
        f"{len(cover)} ordinary customer transactions were interleaved as cover traffic, "
        "including occasional genuinely new devices, so false positives are measurable.",
        f"The scripted attack spans roughly {max(last_offset // 60, 1)} simulated minutes.",
    ]

    payload = {
        "scenario": key,
        "seed": seed,
        "n_events": n_events,
        "accounts": [asdict(account) for account in roster],
        "transactions": [asdict(txn) for txn in transactions],
        "security_events": [asdict(event) for event in security_events],
    }

    return ScenarioPlan(
        scenario=key,
        seed=seed,
        requested_events=n_events,
        accounts=roster,
        transactions=transactions,
        security_events=security_events,
        narrative=narrative,
        fingerprint=_fingerprint(payload),
    )


__all__ = [
    "PlannedSecurityEvent",
    "PlannedTransaction",
    "ScenarioPlan",
    "build_plan",
]
