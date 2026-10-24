"""Scenario catalogue.

Four attack scenarios, each with a distinct behavioural signature. They are
**simulated** attacks against a **synthetic** bank inside this process: no external
system is contacted, no real credentials are used, and nothing leaves the machine.

The catalogue is data, not code, so the API, the UI and the tests all describe the
scenarios the same way.
"""
from __future__ import annotations

SCENARIOS: dict[str, dict] = {
    "ACCOUNT_TAKEOVER": {
        "name": "Account takeover",
        "description": (
            "A victim's credentials are used from an unfamiliar location and device. The attacker "
            "authenticates, then moves large amounts out through card-not-present and wallet rails."
        ),
        "objective": "Move as much value as possible out of a hijacked account before anyone notices.",
        "risk_level": "HIGH",
        "default_events": 40,
        "signature": [
            "Repeated failed logins from a foreign address",
            "Impossible-travel login",
            "First appearance of a new device",
            "Several large transfers inconsistent with the account's history",
        ],
    },
    "CREDENTIAL_STUFFING": {
        "name": "Credential stuffing",
        "description": (
            "Credentials leaked elsewhere are replayed across many accounts. Most attempts fail; the "
            "successes are followed by quick, medium-sized withdrawals."
        ),
        "objective": "Find working credentials at scale and cash out before the pattern is noticed.",
        "risk_level": "HIGH",
        "default_events": 60,
        "signature": [
            "Bursts of failed logins across many accounts",
            "A successful login from a new device shortly after",
            "Immediate medium-value withdrawals",
        ],
    },
    "PAYMENT_MANIPULATION": {
        "name": "Payment manipulation",
        "description": (
            "Payment details are altered mid-flow: a beneficiary is changed or an amount is tampered "
            "with, then a payment is released to the attacker's account."
        ),
        "objective": "Redirect a legitimate payment to a controlled beneficiary without changing the total.",
        "risk_level": "MEDIUM",
        "default_events": 30,
        "signature": [
            "Beneficiary details changed just before payment",
            "Payment instruction modified after authorisation",
            "Repeated round-number charges to one merchant",
        ],
    },
    "SUSPICIOUS_TRANSFER": {
        "name": "Suspicious transfer activity",
        "description": (
            "A large incoming transfer lands in an account and is immediately split across several "
            "previously unseen beneficiaries: classic mule behaviour."
        ),
        "objective": "Obscure the origin of funds by fanning them out through many accounts.",
        "risk_level": "MEDIUM",
        "default_events": 35,
        "signature": [
            "Incoming wire immediately followed by outgoing transfers",
            "Many new beneficiaries added in a short window",
            "Fan-out amounts that add up to the inbound amount",
        ],
    },
}

SCENARIO_KEYS = list(SCENARIOS)


def is_valid_scenario(key: str) -> bool:
    return (key or "").upper() in SCENARIOS


def scenario_list() -> list[dict]:
    """Every scenario, in the shape of the ``ScenarioInfo`` schema."""
    return [{"key": key, **payload} for key, payload in SCENARIOS.items()]


def scenario_info(key: str) -> dict:
    """One scenario's metadata. Raises ``KeyError`` for an unknown key."""
    normalised = (key or "").upper()
    return {"key": normalised, **SCENARIOS[normalised]}


def scenario_name(key: str) -> str:
    return SCENARIOS.get((key or "").upper(), {}).get("name", key)
