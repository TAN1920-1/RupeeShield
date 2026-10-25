"""Demo data seeder — ``python -m app.db.seed``.

Creates the fictional bank the banking experience is demonstrated on: one demo
customer with three accounts, five bank payee accounts, a few weeks of settled
history, and the authentication telemetry that goes with it.

Three rules this script follows:

**It is repeatable.** Every row it writes has a fixed id, so a second run finds the
accounts already present and changes nothing.

**It is fiction.** Names, balances, merchants and devices are invented. No real
person, account number or transaction appears anywhere, and account numbers are never
generated -- the UI derives a masked number by hashing the account id.

**It does not fabricate model output.** The one deliberately risky payment is scored
by the same detection engine a live transfer uses
(:func:`app.services.banking.score_outgoing_payment`), and whatever that engine
decides is what gets stored. If the trained artefacts are absent the run says so and
stores a RULE_BASED decision; it never invents a score.

Usage::

    python -m app.db.seed          # create the demo bank if it is not there yet
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import ACCOUNT_KIND_CUSTOMER, ACCOUNT_KIND_PAYEE
from app.db.session import SessionLocal, init_db
from app.security.audit import record_audit
from app.services import auth, banking
from app.services import event_types as events
from app.services.detection import models_ready
from app.utils.ids import new_id
from app.utils.money import minor_units_to_float, to_minor_units
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

#: Deterministic ids. Their presence is what makes the seeder idempotent.
CUSTOMER_ACCOUNT_IDS = ("ACC-CUST-001", "ACC-CUST-002", "ACC-CUST-003")
PAYEE_ACCOUNT_IDS = (
    "ACC-PAYEE-001",
    "ACC-PAYEE-002",
    "ACC-PAYEE-003",
    "ACC-PAYEE-004",
    "ACC-PAYEE-005",
)
PROBE_ACCOUNT_ID = CUSTOMER_ACCOUNT_IDS[0]

#: The device the customer normally uses, one they have used once, and the device the
#: probe payment arrives from.
HOME_DEVICE = "D-WEB-DEMO-01"
MOBILE_DEVICE = "D-MOB-DEMO-02"
FOREIGN_DEVICE = "D-UNKNOWN-DEMO-9"

CUSTOMER_ACCOUNTS = (
    {
        "id": CUSTOMER_ACCOUNT_IDS[0],
        "label": "Everyday Savings",
        "home_country": "IN",
        "preferred_channel": "UPI_WALLET",
        "avg_txn_amount_minor": 2_400_00,
        "account_age_days": 1480,
        "opening_balance_minor": 4_825_000,
    },
    {
        "id": CUSTOMER_ACCOUNT_IDS[1],
        "label": "Online Savings",
        "home_country": "IN",
        "preferred_channel": "CARD_NOT_PRESENT",
        "avg_txn_amount_minor": 1_150_00,
        "account_age_days": 940,
        "opening_balance_minor": 1_240_000,
    },
    {
        "id": CUSTOMER_ACCOUNT_IDS[2],
        "label": "Current Account",
        "home_country": "IN",
        "preferred_channel": "WIRE_TRANSFER",
        "avg_txn_amount_minor": 18_000_00,
        "account_age_days": 610,
        "opening_balance_minor": 9_675_000,
    },
)

PAYEE_ACCOUNTS = (
    {"id": PAYEE_ACCOUNT_IDS[0], "label": "Metro Utilities", "preferred_channel": "WIRE_TRANSFER"},
    {"id": PAYEE_ACCOUNT_IDS[1], "label": "GreenLeaf Grocers", "preferred_channel": "UPI_WALLET"},
    {"id": PAYEE_ACCOUNT_IDS[2], "label": "Skyline Rentals", "preferred_channel": "WIRE_TRANSFER"},
    {"id": PAYEE_ACCOUNT_IDS[3], "label": "Nimbus Telecom", "preferred_channel": "CARD_NOT_PRESENT"},
    # Never paid in HISTORY: this is the beneficiary the probe payment reaches first.
    {"id": PAYEE_ACCOUNT_IDS[4], "label": "Orion Freight Services", "preferred_channel": "WIRE_TRANSFER"},
)

#: (days_ago, customer account index, direction, amount in INR, channel, payee index or
#: None, memo, device). Oldest first: the balance after each row is the running sum, so
#: the stored balance and the seeded ledger agree exactly.
HISTORY = (
    (42, 2, "CREDIT", "185000.00", "WIRE_TRANSFER", None, "Salary credit — Nimbus Systems", HOME_DEVICE),
    (41, 0, "DEBIT", "3200.00", "UPI_WALLET", 0, "Electricity bill — Metro Utilities", HOME_DEVICE),
    (38, 1, "DEBIT", "1450.00", "CARD_NOT_PRESENT", 1, "Groceries — GreenLeaf", HOME_DEVICE),
    (35, 2, "DEBIT", "32000.00", "WIRE_TRANSFER", 2, "Monthly rent — Skyline Rentals", HOME_DEVICE),
    (31, 0, "DEBIT", "899.00", "UPI_WALLET", 3, "Broadband — Nimbus Telecom", HOME_DEVICE),
    (28, 2, "CREDIT", "4500.00", "WIRE_TRANSFER", None, "Reimbursement credit", HOME_DEVICE),
    (24, 1, "DEBIT", "2260.00", "CARD_NOT_PRESENT", 1, "Groceries — GreenLeaf", HOME_DEVICE),
    (21, 0, "DEBIT", "3200.00", "UPI_WALLET", 0, "Electricity bill — Metro Utilities", HOME_DEVICE),
    (18, 1, "CREDIT", "1200.00", "CARD_NOT_PRESENT", None, "Card refund — GreenLeaf", HOME_DEVICE),
    (14, 2, "DEBIT", "32000.00", "WIRE_TRANSFER", 2, "Monthly rent — Skyline Rentals", HOME_DEVICE),
    (9, 0, "DEBIT", "899.00", "UPI_WALLET", 3, "Broadband — Nimbus Telecom", HOME_DEVICE),
    (5, 1, "DEBIT", "1180.00", "CARD_NOT_PRESENT", 1, "Groceries — GreenLeaf", MOBILE_DEVICE),
    (2, 0, "CREDIT", "2500.00", "UPI_WALLET", None, "Payment from A. Rikame", MOBILE_DEVICE),
)

#: The one payment the seeder scores for real. It is deliberately the pattern the
#: correlation rules exist to catch: a sign-in from an unrecognised device, followed
#: minutes later by a large transfer to a beneficiary never paid before.
PROBE_AMOUNT_INR = Decimal("96500.00")
PROBE_COUNTERPARTY_ID = PAYEE_ACCOUNT_IDS[4]
PROBE_LOGIN_DAYS_AGO = 0.2
PROBE_MINUTES_AFTER_LOGIN = 7


def _account_rows() -> list[dict]:
    rows = [
        {
            **spec,
            "balance_minor": spec["opening_balance_minor"],
            "risk_status": "NORMAL",
            "is_synthetic": True,
            "owner_user_id": auth.DEMO_CUSTOMER_ID,
            "account_kind": ACCOUNT_KIND_CUSTOMER,
        }
        for spec in CUSTOMER_ACCOUNTS
    ]
    rows.extend(
        {
            "id": spec["id"],
            "label": spec["label"],
            "home_country": "IN",
            "preferred_channel": spec["preferred_channel"],
            "avg_txn_amount_minor": 5_000_00,
            "account_age_days": 1_200,
            "opening_balance_minor": 0,
            "balance_minor": 0,
            "risk_status": "NORMAL",
            "is_synthetic": True,
            "owner_user_id": None,
            "account_kind": ACCOUNT_KIND_PAYEE,
        }
        for spec in PAYEE_ACCOUNTS
    )
    return rows


def _seed_login_history(session: Session, *, now: datetime) -> list[dict]:
    """Write the customer's sign-in telemetry."""
    written: list[dict] = []

    def add(days_ago: float, account_id: str, event_type: str, severity: str, device: str) -> None:
        event_id = new_id("SEC")
        repo.detections.add_security_event(
            session,
            {
                "id": event_id,
                "run_id": None,
                "account_id": account_id,
                "timestamp": now - timedelta(days=days_ago),
                "event_type": event_type,
                "severity": severity,
                "country": "SG" if device == FOREIGN_DEVICE else "IN",
                "device_id": device,
                "source_ip_hash": new_id("IPH", 16),
                "related_transaction_id": None,
                "details": {"seeded": True},
            },
        )
        written.append({"id": event_id, "event_type": event_type, "device_id": device})

    add(2.5, PROBE_ACCOUNT_ID, events.LOGIN_SUCCESS, "INFO", HOME_DEVICE)
    add(0.6, CUSTOMER_ACCOUNT_IDS[1], events.LOGIN_SUCCESS, "INFO", MOBILE_DEVICE)
    add(0.4, PROBE_ACCOUNT_ID, events.LOGIN_SUCCESS, "INFO", HOME_DEVICE)
    add(0.3, PROBE_ACCOUNT_ID, events.FAILED_LOGIN, "LOW", FOREIGN_DEVICE)
    add(PROBE_LOGIN_DAYS_AGO, PROBE_ACCOUNT_ID, events.LOGIN_NEW_DEVICE, "MEDIUM", FOREIGN_DEVICE)
    return written


def _seed_history(session: Session, *, now: datetime) -> tuple[list[dict], dict[str, int]]:
    """Write the settled history and return it with the resulting balances."""
    balances = {
        spec["id"]: int(spec["opening_balance_minor"]) for spec in CUSTOMER_ACCOUNTS
    }
    written: list[dict] = []
    for index, row in enumerate(HISTORY, start=1):
        days_ago, account_index, direction, amount, channel, payee_index, memo, device = row
        account_id = CUSTOMER_ACCOUNT_IDS[account_index]
        amount_minor = to_minor_units(Decimal(amount))
        counterparty_id = PAYEE_ACCOUNT_IDS[payee_index] if payee_index is not None else None
        transaction_id = f"TXN-SEED-{index:04d}"
        repo.transactions.add_transaction(
            session,
            {
                "id": transaction_id,
                "run_id": None,
                "account_id": account_id,
                "counterparty_id": counterparty_id,
                "counterparty_label": (
                    PAYEE_ACCOUNTS[payee_index]["label"] if payee_index is not None else "External sender"
                ),
                "timestamp": now - timedelta(days=days_ago),
                "amount_minor": amount_minor,
                "currency": "INR",
                "channel": channel,
                "country": "IN",
                "merchant_id": f"PAYEE-{counterparty_id or 'EXTERNAL'}",
                "device_id": device,
                "direction": direction,
                "status": "SETTLED",
                "is_fraud": None,
                "scenario": None,
                "description": memo,
                "transfer_id": None,
            },
        )
        balances[account_id] += amount_minor if direction == "CREDIT" else -amount_minor
        written.append({"id": transaction_id, "direction": direction, "amount_minor": amount_minor})
    return written, balances


def _seed_probe_payment(session: Session, *, now: datetime) -> dict | None:
    """Score one deliberately risky payment through the real engine, then store it.

    Scoring happens *before* the row is written so the payment cannot appear in its own
    feature history. Whatever the engine returns is what the row records.
    """
    account = repo.accounts.get_account(session, PROBE_ACCOUNT_ID)
    recipient = repo.accounts.get_account(session, PROBE_COUNTERPARTY_ID)
    if account is None or recipient is None:  # pragma: no cover - ordering guard
        return None

    amount_minor = to_minor_units(PROBE_AMOUNT_INR)
    when = now - timedelta(days=PROBE_LOGIN_DAYS_AGO) + timedelta(minutes=PROBE_MINUTES_AFTER_LOGIN)
    decision, config = banking.score_outgoing_payment(
        session,
        account,
        amount_major=minor_units_to_float(amount_minor),
        when=when,
        device_id=FOREIGN_DEVICE,
        counterparty_id=recipient.id,
    )
    status = str(decision.response)
    transaction_id = "TXN-SEED-PROBE"
    repo.transactions.add_transaction(
        session,
        {
            "id": transaction_id,
            "run_id": None,
            "account_id": account.id,
            "counterparty_id": recipient.id,
            "counterparty_label": recipient.label,
            "timestamp": when,
            "amount_minor": amount_minor,
            "currency": "INR",
            "channel": banking.TRANSFER_CHANNEL,
            "country": account.home_country,
            "merchant_id": f"PAYEE-{recipient.id}",
            "device_id": FOREIGN_DEVICE,
            "direction": "DEBIT",
            "status": status,
            "is_fraud": None,
            "scenario": None,
            "description": f"Transfer to {recipient.label}",
            "transfer_id": None,
        },
    )
    detection_id = new_id("DET")
    repo.detections.add_detection(
        session,
        {
            "id": detection_id,
            "run_id": None,
            "account_id": account.id,
            "transaction_id": transaction_id,
            "timestamp": when,
            "detection_source": config,
            "fraud_risk_score": float(decision.fraud_risk_score),
            "decision": str(decision.decision),
            "ml_supervised_score": decision.ml_supervised_score,
            "ml_anomaly_score": decision.ml_anomaly_score,
            "rule_heuristic_score": decision.rule_heuristic_score,
            "correlation_score": decision.correlation_score,
            "reasons": list(decision.reasons),
            "rules_triggered": list(decision.rules_triggered),
            "correlated_event_ids": list(decision.correlated_event_ids),
            "top_features": list(decision.top_features),
            "latency_ms": float(decision.latency_ms or 0.0),
            "ground_truth_fraud": None,
        },
    )
    if status == "SETTLED":
        # Only settled value moved, so only settled value changes a balance.
        repo.accounts.apply_balance_delta(session, account, -amount_minor)
        repo.accounts.apply_balance_delta(session, recipient, amount_minor)
    session.commit()
    return {
        "transaction_id": transaction_id,
        "detection_id": detection_id,
        "when": when,
        "decision": str(decision.decision),
        "status": status,
        "risk_score": round(float(decision.fraud_risk_score), 4),
        "defense_config": config,
        "rules_triggered": list(decision.rules_triggered),
        "correlated_event_ids": list(decision.correlated_event_ids),
    }


def _open_probe_incident(session: Session, *, now: datetime, probe: dict | None) -> str | None:
    """Open one incident for the probe payment, but only if the engine queried it.

    An incident is an observation that something needs looking at. If the detection
    engine allowed the payment outright there is nothing to open, and inventing one
    would be exactly the kind of fabricated alert this project forbids.
    """
    if probe is None or probe["decision"] == "ALLOW":
        return None
    account = repo.accounts.get_account(session, PROBE_ACCOUNT_ID)
    blocked = probe["status"] == "BLOCKED"
    incident_id = new_id("INC")
    opened_at = now - timedelta(days=PROBE_LOGIN_DAYS_AGO)
    repo.incidents.add_incident(
        session,
        {
            "id": incident_id,
            "run_id": None,
            "scenario": "SEEDED_REVIEW",
            "primary_account_id": PROBE_ACCOUNT_ID,
            "title": f"Unrecognised device followed by a large transfer on {account.label}",
            "summary": (
                f"A sign-in from device {FOREIGN_DEVICE} was followed "
                f"{PROBE_MINUTES_AFTER_LOGIN} minutes later by a transfer of "
                f"{PROBE_AMOUNT_INR:,.2f} INR to a beneficiary this account has not paid "
                f"before. The detection engine scored it {probe['risk_score']:.2f} and "
                f"returned {probe['decision']}."
            ),
            "severity": "HIGH" if blocked else "MEDIUM",
            "risk_level": "HIGH" if blocked else "MEDIUM",
            "status": "OPEN",
            "opened_at": opened_at,
        },
    )
    repo.incidents.add_timeline(
        session,
        [
            {
                "id": new_id("IEV"),
                "incident_id": incident_id,
                "seq": 1,
                "timestamp": opened_at,
                "kind": "SECURITY_EVENT",
                "ref_id": None,
                "description": f"Sign-in from an unrecognised device ({FOREIGN_DEVICE}, SG).",
                "payload": {"event_type": events.LOGIN_NEW_DEVICE, "seeded": True},
            },
            {
                "id": new_id("IEV"),
                "incident_id": incident_id,
                "seq": 2,
                "timestamp": probe["when"],
                "kind": "TRANSACTION",
                "ref_id": probe["transaction_id"],
                "description": f"Wire transfer of {PROBE_AMOUNT_INR:,.2f} INR to Orion Freight Services.",
                "payload": {"amount_minor": to_minor_units(PROBE_AMOUNT_INR)},
            },
            {
                "id": new_id("IEV"),
                "incident_id": incident_id,
                "seq": 3,
                "timestamp": probe["when"],
                "kind": "DETECTION",
                "ref_id": probe["detection_id"],
                "description": (
                    f"Scored {probe['risk_score']:.2f} by the {probe['defense_config']} "
                    f"configuration, decision {probe['decision']}."
                ),
                "payload": {"rules_triggered": probe["rules_triggered"]},
            },
        ],
    )
    # Link the detection to the incident so the customer's transaction detail and the
    # analyst's incident view point at the same rows.
    detection = repo.detections.detection_for_transaction(session, probe["transaction_id"])
    if detection is not None:
        detection.incident_id = incident_id
        session.add(detection)
    account.risk_status = "FLAGGED_FOR_INVESTIGATION" if blocked else "ELEVATED_SIMULATED_RISK"
    session.add(account)
    if blocked:
        repo.incidents.add_defense_action(
            session,
            {
                "id": new_id("DEF"),
                "run_id": None,
                "incident_id": incident_id,
                "account_id": PROBE_ACCOUNT_ID,
                "action_type": "SUSPEND_ACCOUNT",
                "timestamp": opened_at,
                "reason": "A large transfer from an unrecognised device was blocked.",
                "amount_minor": to_minor_units(PROBE_AMOUNT_INR),
            },
        )
    return incident_id


def seed_demo_data(session: Session) -> dict:
    """Create the demo bank. Idempotent: a second call changes nothing."""
    auth.ensure_demo_users(session)
    if repo.accounts.get_account(session, CUSTOMER_ACCOUNT_IDS[0]) is not None:
        session.commit()
        return {"created": False, "reason": "Demo accounts already exist."}

    customer = repo.users.get_user(session, auth.DEMO_CUSTOMER_ID)
    if customer is None:  # pragma: no cover - ensure_demo_users just ran
        raise RuntimeError("The demo customer could not be created.")

    now = utcnow().replace(microsecond=0)
    for row in _account_rows():
        repo.accounts.add_account(session, row)
    session.commit()

    login_rows = _seed_login_history(session, now=now)
    history_rows, balances = _seed_history(session, now=now)
    for account_id, balance in balances.items():
        account = repo.accounts.get_account(session, account_id)
        if account is not None:
            account.balance_minor = int(balance)
            session.add(account)
    session.commit()

    probe = _seed_probe_payment(session, now=now)
    incident_id = _open_probe_incident(session, now=now, probe=probe)
    session.commit()

    record_audit(
        session,
        action="DEMO_DATA_SEEDED",
        actor=customer.email,
        entity_type="seed",
        entity_id="demo-bank",
        details={
            "accounts": len(CUSTOMER_ACCOUNT_IDS) + len(PAYEE_ACCOUNT_IDS),
            "transactions": len(history_rows) + (1 if probe else 0),
            "security_events": len(login_rows),
            "incident": incident_id,
        },
    )
    session.commit()

    return {
        "created": True,
        "customer": customer.email,
        "accounts": len(CUSTOMER_ACCOUNT_IDS),
        "payees": len(PAYEE_ACCOUNT_IDS),
        "transactions": len(history_rows) + (1 if probe else 0),
        "security_events": len(login_rows),
        "incident_id": incident_id,
        "probe": probe,
    }


def main() -> int:
    init_db()
    session = SessionLocal()
    try:
        report = seed_demo_data(session)
    finally:
        session.close()

    if not report["created"]:
        print(f"RupeeShield demo data: nothing to do — {report['reason']}")
        return 0

    print("RupeeShield demo data created.")
    print(f"  customer     : {report['customer']}")
    print(f"  accounts     : {report['accounts']} customer + {report['payees']} payee")
    print(f"  transactions : {report['transactions']}")
    print(f"  security     : {report['security_events']} sign-in events")
    if report["incident_id"]:
        print(f"  incident     : {report['incident_id']} (open, awaiting review)")
    probe = report["probe"]
    if probe:
        source = "trained models" if models_ready() else "rules only (ML artefacts absent)"
        print(
            f"  probe payment: {probe['decision']} at risk {probe['risk_score']:.2f} "
            f"using {probe['defense_config']} / {source}"
        )
        if probe["rules_triggered"]:
            print(f"                 rules: {', '.join(probe['rules_triggered'])}")
    print()
    print("Sign in with the demo credentials published by GET /api/auth/demo-credentials.")
    if not settings.demo_mode_enabled:  # pragma: no cover - demo mode is on by default
        print("NOTE: demo mode is disabled in configuration, so no demo logins were created.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
