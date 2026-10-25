"""Customer banking service.

Everything a customer can see or do with their own money lives here, and every
query in this module is scoped by :func:`app.db.repositories.accounts.accounts_for_owner`.
That scope is applied *first* and cannot be widened by a query parameter, which is
what makes "a customer only ever sees their own rows" a property of the query rather
than a property of the UI.

Three ideas hold the design together:

**Balances are read, never invented.** The account cards are shaped from the
``accounts`` table, so a transfer that moved money is visible in exactly the same
number on the customer's dashboard, in the ledger and in the analyst's transaction
explorer.

**A transfer goes through the real detection pipeline.** Before any money moves the
debit is scored with the same hybrid rules + supervised + anomaly engine the attack
simulator uses, including security-event correlation. A blocked transfer is blocked
for a reason that is stored on the row.

**Money movement is one transaction.** Both legs and both balance deltas are written
in a single commit, or none of them are.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import ROLE_ANALYST, Account, Transaction, User
from app.ml.features import UserStateStore
from app.security.access import AccessDenied, DuplicateSubmission
from app.security.audit import record_audit
from app.security.validation import ValidationError, sanitise_text
from app.services import event_types as events
from app.services import notifications
from app.services.correlation import (
    CorrelationResult,
    SecurityEventView,
    TransactionView,
    correlate,
)
from app.services.detection import (
    DetectionUnavailable,
    evaluate_transaction,
    models_ready,
)
from app.services.incidents import shape_incident
from app.utils.ids import new_id
from app.utils.money import minor_units_to_float, to_minor_units
from app.utils.timeutils import parse_datetime, utcnow

logger = get_logger(__name__)

CURRENCY = "INR"

#: Device fingerprint the browser sends when it has nothing better. Kept identical
#: to :data:`app.services.auth.DEFAULT_DEVICE` so a sign-in and a transfer from the
#: same browser are attributed to the same device.
WEB_DEVICE = "D-WEB-UNKNOWN"

TRANSFER_CHANNEL = "TRANSFER"

#: Display label per settlement state. Five labels are produced in total: these
#: four, plus ``Flagged`` for value that settled *and* was not allowed.
STATUS_LABELS = {
    "SETTLED": "Completed",
    "PENDING_REVIEW": "Under Review",
    "BLOCKED": "Blocked",
    "REVERSED": "Reversed",
}
FLAGGED_LABEL = "Flagged"

#: Account type shown on the card, derived from the channel the account normally
#: uses. Presentation only: nothing in the ledger depends on it.
ACCOUNT_TYPE_BY_CHANNEL = {
    "UPI_WALLET": "Everyday Savings",
    "CARD_PRESENT": "Everyday Savings",
    "CARD_NOT_PRESENT": "Online Savings",
    "WIRE_TRANSFER": "Current Account",
    "CASH": "Cash Account",
}
DEFAULT_ACCOUNT_TYPE = "Savings Account"
PAYEE_ACCOUNT_TYPE = "Payee Account"

CHANNEL_LABELS = {
    "UPI_WALLET": "UPI",
    "WIRE_TRANSFER": "wire transfer",
    "CARD_PRESENT": "card payment",
    "CARD_NOT_PRESENT": "online card payment",
    "TRANSFER": "bank transfer",
    "CASH": "cash",
}

#: The transfer state labels the confirmation screen uses.
TRANSFER_PENDING_LABEL = "Pending"

#: A repeat of the *same* request inside this window without an idempotency key is
#: treated as a double submission rather than as two deliberate transfers.
DUPLICATE_WINDOW_SECONDS = 120


# ---------------------------------------------------------------------------
# Small derived values
# ---------------------------------------------------------------------------
def masked_account_number(account_id: str) -> str:
    """A stable, non-reversible masked account number.

    Derived by hashing the account id: the same account always renders the same
    number, and no real-looking account number is ever generated or stored.
    """
    digest = hashlib.sha256(f"rupeeshield-account:{account_id}".encode("utf-8")).hexdigest()
    digits = "".join(char for char in digest if char.isdigit())
    digits = (digits + "0000")[:12]
    return f"•••• •••• {digits[-4:]}"


def account_type_for(account: Account) -> str:
    if account.account_kind == "PAYEE":
        return PAYEE_ACCOUNT_TYPE
    return ACCOUNT_TYPE_BY_CHANNEL.get(account.preferred_channel, DEFAULT_ACCOUNT_TYPE)


def risk_level_for(score: float | None) -> str | None:
    """Bucket a risk score with the same thresholds the decision engine uses."""
    if score is None:
        return None
    if score >= settings.thresholds.block_threshold:
        return "HIGH"
    if score >= settings.thresholds.flag_threshold:
        return "MEDIUM"
    return "LOW"


def status_label_for(status: str, decision: str | None) -> str:
    """The customer-facing badge for one ledger row.

    A settled transaction the detector did not allow reads as ``Flagged``: the
    money moved, and the customer must be able to see that it was queried. That is
    the difference between an honest receipt and a reassuring one.
    """
    if status == "SETTLED" and decision is not None and decision != "ALLOW":
        return FLAGGED_LABEL
    return STATUS_LABELS.get(status, status.title())


def describe_transaction(row: Transaction) -> str:
    """Compose a customer-facing memo. A stored description always wins."""
    if row.description:
        return row.description
    counterparty = row.counterparty_label or row.counterparty_id or "an external beneficiary"
    channel = CHANNEL_LABELS.get(row.channel, row.channel.replace("_", " ").title())
    if row.direction == "CREDIT":
        return f"Payment received from {counterparty} · {channel}"
    return f"Payment to {counterparty} · {channel}"


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------
def shape_account_card(
    account: Account,
    *,
    transaction_count: int = 0,
    suspicious_count: int = 0,
    pending_count: int = 0,
    last_activity_at: datetime | None = None,
    is_primary: bool = False,
) -> dict:
    under_review = account.risk_status != "NORMAL"
    return {
        "id": account.id,
        "label": account.label,
        "masked_number": masked_account_number(account.id),
        "account_type": account_type_for(account),
        "currency": CURRENCY,
        "status": "UNDER_REVIEW" if under_review else "ACTIVE",
        "status_label": "Under Review" if under_review else "Active",
        "balance_minor": int(account.balance_minor),
        "balance": minor_units_to_float(account.balance_minor),
        "opening_balance_minor": int(account.opening_balance_minor),
        "home_country": account.home_country,
        "preferred_channel": account.preferred_channel,
        "account_age_days": int(account.account_age_days),
        "risk_status": account.risk_status,
        "transaction_count": int(transaction_count),
        "suspicious_count": int(suspicious_count),
        "pending_count": int(pending_count),
        "last_activity_at": last_activity_at,
        "is_synthetic": bool(account.is_synthetic),
        "is_primary": is_primary,
    }


def shape_customer_transaction(
    row: Transaction,
    *,
    account_label: str | None = None,
    detection=None,
    incident_id: str | None = None,
) -> dict:
    """Shape one ledger row for the customer's own transaction history."""
    debit = row.direction != "CREDIT"
    own_label = account_label or row.account_id
    counterparty = row.counterparty_label or row.counterparty_id
    score = None if detection is None else round(float(detection.fraud_risk_score), 4)
    decision = None if detection is None else detection.decision
    amount_minor = int(row.amount_minor)
    return {
        "id": row.id,
        "timestamp": row.timestamp,
        "description": describe_transaction(row),
        "direction": row.direction,
        "sender": own_label if debit else (counterparty or "External sender"),
        "recipient": (counterparty or "External beneficiary") if debit else own_label,
        "counterparty_id": row.counterparty_id,
        "counterparty_label": row.counterparty_label,
        "account_id": row.account_id,
        "account_label": account_label,
        "account_masked_number": masked_account_number(row.account_id),
        "amount_minor": amount_minor,
        "amount": minor_units_to_float(amount_minor),
        "signed_amount_minor": -amount_minor if debit else amount_minor,
        "signed_amount": minor_units_to_float(-amount_minor if debit else amount_minor),
        "currency": row.currency or CURRENCY,
        "channel": row.channel,
        "status": row.status,
        "status_label": status_label_for(row.status, decision),
        "decision": decision,
        "risk_score": score,
        "risk_level": risk_level_for(score),
        "suspicious": decision is not None and decision != "ALLOW",
        "detection_id": None if detection is None else detection.id,
        "incident_id": incident_id,
        "is_fraud": row.is_fraud,
    }


def shape_customer_incident(session: Session, row, label: str | None = None) -> dict:
    """The customer-safe incident view: no investigation notes, no analyst payload."""
    metrics = repo.incidents.incident_metrics(session, row.id)
    return shape_incident(row, label, metrics)


def _balance_summary(accounts: list[Account], held_minor: int) -> dict:
    total_minor = sum(int(account.balance_minor) for account in accounts)
    available_minor = max(total_minor - int(held_minor), 0)
    return {
        "account_count": len(accounts),
        "currency": CURRENCY,
        "total_balance_minor": total_minor,
        "total_balance": minor_units_to_float(total_minor),
        "held_minor": int(held_minor),
        "held": minor_units_to_float(held_minor),
        "available_minor": available_minor,
        "available": minor_units_to_float(available_minor),
    }


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def accounts_overview(session: Session, user: User) -> dict:
    """The dashboard's account cards plus the balance summary.

    ``held`` is the value of outgoing transfers that are awaiting a decision. It is
    *not* subtracted from the stored balance (the money has not moved), so the two
    numbers are reported separately instead of being blended into one figure that
    would not match the ledger.
    """
    accounts = repo.accounts.accounts_for_owner(session, user.id)
    account_ids = [account.id for account in accounts]
    counts = repo.accounts.transaction_counts(session, account_ids)
    decisions = repo.transactions.decision_counts_by_account(session, account_ids)
    statuses = repo.transactions.status_counts_by_account(session, account_ids)
    suspicious_total = sum(
        count
        for per_account in decisions.values()
        for decision, count in per_account.items()
        if decision != "ALLOW"
    )
    primary_id = account_ids[0] if account_ids else None

    def _queried(account_id: str) -> int:
        return sum(
            count
            for decision, count in decisions.get(account_id, {}).items()
            if decision != "ALLOW"
        )

    items = [
        shape_account_card(
            account,
            transaction_count=counts.get(account.id, 0),
            suspicious_count=_queried(account.id),
            pending_count=statuses.get(account.id, {}).get("PENDING_REVIEW", 0),
            last_activity_at=repo.transactions.last_transaction_at(session, [account.id]),
            is_primary=account.id == primary_id,
        )
        for account in accounts
    ]

    held = repo.transactions.pending_outgoing_total(session, account_ids)
    return {
        "items": items,
        "summary": _balance_summary(accounts, held),
        "total": len(items),
        "limit": len(items),
        "offset": 0,
        "returned": len(items),
        "suspicious_total": suspicious_total,
    }


def account_card(session: Session, user: User, account_id: str) -> dict:
    """One account card, or a 404 when it is not the customer's."""
    account = _owned_account(session, user, account_id)
    counts = repo.accounts.transaction_counts(session, [account.id])
    decisions = repo.transactions.decision_counts_by_account(session, [account.id])
    statuses = repo.transactions.status_counts_by_account(session, [account.id])
    per_account = decisions.get(account.id, {})
    suspicious = sum(count for decision, count in per_account.items() if decision != "ALLOW")
    primary_id = repo.accounts.owned_account_ids(session, user.id)
    return shape_account_card(
        account,
        transaction_count=counts.get(account.id, 0),
        suspicious_count=suspicious,
        pending_count=statuses.get(account.id, {}).get("PENDING_REVIEW", 0),
        last_activity_at=repo.transactions.last_transaction_at(session, [account.id]),
        is_primary=bool(primary_id) and primary_id[0] == account.id,
    )


def _owned_account(session: Session, user: User, account_id: str) -> Account:
    account = repo.accounts.get_account(session, account_id)
    if account is None:
        raise LookupError(f"No account with id {account_id}.")
    if account.owner_user_id != user.id:
        # Deliberately a 404 rather than a 403: the answer must not reveal that
        # another customer's account id exists.
        raise LookupError(f"No account with id {account_id}.")
    return account


def list_payees(session: Session, user: User) -> list[dict]:
    """The customer's own other accounts plus the bank's payee accounts."""
    own = repo.accounts.accounts_for_owner(session, user.id)
    payees = repo.accounts.payee_accounts(session)
    seen: set[str] = set()
    items: list[dict] = []
    for account in [*own, *payees]:
        if account.id in seen:
            continue
        seen.add(account.id)
        items.append(
            {
                "id": account.id,
                "label": account.label,
                "masked_number": masked_account_number(account.id),
                "account_type": account_type_for(account),
                "currency": CURRENCY,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------
def _parse_bound(value: str | None, *, end_of_day: bool) -> datetime | None:
    """Parse a date-range bound. A bare ``YYYY-MM-DD`` covers the whole day."""
    if not value:
        return None
    cleaned = value.strip()
    parsed = parse_datetime(cleaned)
    if len(cleaned) <= 10 and end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed


def transaction_page(
    session: Session,
    user: User,
    *,
    limit: int,
    offset: int,
    account_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    direction: str | None = None,
    suspicious: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """The customer's own transaction history, filtered and paginated."""
    account_ids = repo.accounts.owned_account_ids(session, user.id)
    if account_id:
        # Selecting an account that is not yours is a scope violation, not an empty page.
        scope = [_owned_account(session, user, account_id).id]
    else:
        scope = account_ids

    rows, total = repo.transactions.owned_transactions(
        session,
        account_ids=scope,
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        direction=direction,
        suspicious=suspicious,
        date_from=_parse_bound(date_from, end_of_day=False),
        date_to=_parse_bound(date_to, end_of_day=True),
    )
    items = []
    for row, label, detection in rows:
        incident_id = getattr(detection, "incident_id", None) if detection else None
        items.append(shape_customer_transaction(row, account_label=label, detection=detection, incident_id=incident_id))
    return {"items": items, "total": total, "limit": limit, "offset": offset, "returned": len(items)}


def _related_rows(session: Session, row: Transaction, limit: int = 8) -> list[dict]:
    """Recent activity on the same account and with the same counterparty."""
    related: list[dict] = []
    seen = {row.id}
    for other in repo.transactions.account_history(session, row.account_id, limit=limit + 1):
        if other.id in seen:
            continue
        seen.add(other.id)
        related.append(
            {
                "id": other.id,
                "relation": "SAME_ACCOUNT",
                "timestamp": other.timestamp,
                "description": describe_transaction(other),
                "amount_minor": int(other.amount_minor),
                "amount": minor_units_to_float(other.amount_minor),
                "direction": other.direction,
                "status": other.status,
            }
        )
    related.sort(key=lambda item: item["timestamp"], reverse=True)
    return related[:limit]


def transaction_detail(session: Session, user: User, transaction_id: str) -> dict:
    """One ledger entry with the detection behind it and the incident it belongs to."""
    row = repo.transactions.get_transaction(session, transaction_id)
    if row is None or row.account_id not in repo.accounts.owned_account_ids(session, user.id):
        raise LookupError(f"No transaction with id {transaction_id}.")

    label = repo.accounts.get_accounts(session, [row.account_id]).get(row.account_id)
    detection = repo.detections.detection_for_transaction(session, transaction_id)
    incident = None
    incident_id = getattr(detection, "incident_id", None) if detection else None
    if incident_id:
        found = repo.incidents.incident_with_label(session, incident_id)
        if found is not None:
            incident = shape_customer_incident(session, found[0], found[1])

    payload = shape_customer_transaction(
        row,
        account_label=label.label if label else None,
        detection=detection,
        incident_id=incident_id,
    )
    payload["detection"] = (
        None
        if detection is None
        else {
            "id": detection.id,
            "decision": detection.decision,
            "fraud_risk_score": round(float(detection.fraud_risk_score), 4),
            "detection_source": detection.detection_source,
            "reasons": list(detection.reasons or []),
            "rules_triggered": list(detection.rules_triggered or []),
            "correlated_event_ids": list(detection.correlated_event_ids or []),
        }
    )
    payload["explanation"] = list(detection.top_features or []) if detection else []
    payload["security_events"] = [
        {
            "id": event.id,
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "severity": event.severity,
            "device_id": event.device_id,
            "country": event.country,
            "source_ip_hash": event.source_ip_hash,
            "details": dict(event.details or {}),
        }
        for event in repo.detections.security_events_for_transaction(session, transaction_id)
    ]
    payload["incident"] = incident
    payload["related"] = _related_rows(session, row)
    return payload


# ---------------------------------------------------------------------------
# Account security centre
# ---------------------------------------------------------------------------
LOGIN_EVENT_TYPES = [events.LOGIN_SUCCESS, events.LOGIN_NEW_DEVICE]
FAILED_EVENT_TYPES = [events.FAILED_LOGIN]

SECURITY_EVENT_DESCRIPTIONS = {
    events.FAILED_LOGIN: "Failed sign-in attempt",
    events.LOGIN_SUCCESS: "Signed in successfully",
    events.LOGIN_NEW_DEVICE: "Signed in from a device not seen before",
    events.LOGIN_FOREIGN_IP: "Signed in from an unusual location",
    events.IMPOSSIBLE_TRAVEL: "Two sign-ins from distant locations too close together",
    events.PASSWORD_RESET: "Password change requested",
    events.NEW_DEVICE: "A device not seen before was observed on the account",
    events.DEVICE_REPUTATION_LOW: "Sign-in from a device with a poor reputation record",
    events.BENEFICIARY_ADDED: "A new beneficiary was added",
    events.BENEFICIARY_CHANGED: "Beneficiary details were changed",
    events.PAYMENT_DETAILS_MODIFIED: "Payment details were modified while the payment was in progress",
    events.PAYMENT_AMOUNT_TAMPERED: "The payment amount was altered in transit",
    events.NEW_BENEFICIARY: "First payment to a new beneficiary",
    events.MULE_FUNDING_RECEIVED: "Incoming funds followed by rapid outgoing transfers",
    events.RAPID_FANOUT: "Many outgoing payments in a short period",
    events.CARD_TESTING_PROBE: "Small-value probe payment",
    events.ACCOUNT_SUSPENDED: "Account suspended by the automated defense",
    events.SESSION_REVOKED: "Session ended",
}


def _shape_event(row) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp,
        "event_type": row.event_type,
        "description": SECURITY_EVENT_DESCRIPTIONS.get(
            row.event_type, row.event_type.replace("_", " ").title()
        ),
        "severity": row.severity,
        "device_id": row.device_id,
        "country": row.country,
        "source_ip_hash": row.source_ip_hash,
    }


def _security_factors(
    *,
    sign_ins: int,
    failed: int,
    devices: int,
    suspicious: int,
    open_incidents: int,
    total_events: int,
) -> list[dict]:
    """The numbers behind the posture indicator, each with its own explanation."""
    return [
        {
            "key": "sign_ins",
            "label": "Successful sign-ins",
            "value": sign_ins,
            "tone": "GOOD",
            "detail": "Sign-ins recorded on your accounts since this database was seeded.",
        },
        {
            "key": "failed_sign_ins",
            "label": "Failed sign-in attempts",
            "value": failed,
            "tone": "DANGER" if failed >= settings.max_failed_logins else ("ATTENTION" if failed else "GOOD"),
            "detail": (
                f"{settings.max_failed_logins} consecutive failures raise an alert and a notification."
            ),
        },
        {
            "key": "devices",
            "label": "Devices seen",
            "value": devices,
            "tone": "GOOD",
            "detail": "Derived from recorded telemetry, not from a stored device list.",
        },
        {
            "key": "suspicious_transactions",
            "label": "Transactions queried by the detector",
            "value": suspicious,
            "tone": "DANGER" if suspicious >= 3 else ("ATTENTION" if suspicious else "GOOD"),
            "detail": "Rows the detection engine did not allow outright. Not an assertion of wrongdoing.",
        },
        {
            "key": "open_incidents",
            "label": "Open incidents",
            "value": open_incidents,
            "tone": "DANGER" if open_incidents else "GOOD",
            "detail": "Investigations still open on your accounts.",
        },
        {
            "key": "security_events",
            "label": "Security events recorded",
            "value": total_events,
            "tone": "GOOD",
            "detail": "Authentication, device and payment-integrity telemetry on your accounts.",
        },
    ]


def security_centre(session: Session, user: User) -> dict:
    """The Account Security Center payload."""
    account_ids = repo.accounts.owned_account_ids(session, user.id)

    sign_ins = repo.detections.count_events_for_accounts(session, account_ids, LOGIN_EVENT_TYPES)
    failed = repo.detections.count_events_for_accounts(session, account_ids, FAILED_EVENT_TYPES)
    total_events = repo.detections.count_events_for_accounts(session, account_ids)
    decisions = repo.transactions.decision_counts_for_accounts(session, account_ids)
    suspicious_count = sum(count for decision, count in decisions.items() if decision != "ALLOW")
    open_incidents = repo.incidents.open_incident_counts(session, account_ids)

    device_rows = repo.detections.known_devices(session, account_ids)
    pending_devices = {
        event.device_id
        for event in repo.detections.security_events_for_accounts(
            session, account_ids, limit=50, event_types=[events.LOGIN_NEW_DEVICE]
        )
        if event.device_id
    }
    devices = [
        {
            "device_id": device,
            "country": country,
            "last_seen": last_seen,
            "recognised": device not in pending_devices,
            "detail": (
                "Seen on your accounts."
                if device not in pending_devices
                else "This device has only been seen once. Confirm it is yours."
            ),
        }
        for device, country, last_seen in device_rows
        if device
    ]

    factors = _security_factors(
        sign_ins=sign_ins,
        failed=failed,
        devices=len(devices),
        suspicious=suspicious_count,
        open_incidents=open_incidents,
        total_events=total_events,
    )

    if open_incidents or suspicious_count >= 3:
        status = {
            "level": "ELEVATED",
            "label": "Needs attention",
            "summary": (
                f"{open_incidents} open incident(s) and {suspicious_count} transaction(s) the detector "
                "did not allow outright were recorded on your accounts."
            ),
        }
    elif failed or suspicious_count:
        status = {
            "level": "ATTENTION",
            "label": "Review suggested",
            "summary": (
                f"{failed} failed sign-in attempt(s) and {suspicious_count} queried transaction(s) "
                "were recorded. Nothing requires action, but both are listed below."
            ),
        }
    else:
        status = {
            "level": "GOOD",
            "label": "Nothing unusual",
            "summary": "No failed sign-ins, queried transactions or open incidents on your accounts.",
        }

    suspicious_rows = repo.transactions.suspicious_for_accounts(session, account_ids, limit=8)
    suspicious = []
    for row, detection, label in suspicious_rows:
        suspicious.append(
            shape_customer_transaction(
                row,
                account_label=label,
                detection=detection,
                incident_id=getattr(detection, "incident_id", None),
            )
        )

    incident_rows = repo.incidents.incidents_for_accounts(session, account_ids, limit=12)
    open_rows = [(row, label) for row, label in incident_rows if row.status != "CLOSED"]

    return {
        "status": status,
        "factors": factors,
        "recent_sign_ins": [
            _shape_event(event)
            for event in repo.detections.security_events_for_accounts(
                session, account_ids, limit=8, event_types=LOGIN_EVENT_TYPES
            )
        ],
        "failed_sign_ins": [
            _shape_event(event)
            for event in repo.detections.security_events_for_accounts(
                session, account_ids, limit=8, event_types=FAILED_EVENT_TYPES
            )
        ],
        "devices": devices,
        "suspicious_transactions": suspicious,
        "open_incidents": [
            shape_customer_incident(session, row, label) for row, label in open_rows[:6]
        ],
        "security_events": [
            _shape_event(event)
            for event in repo.detections.security_events_for_accounts(session, account_ids, limit=10)
        ],
        "generated_at": utcnow(),
    }


SIMULATED_DEVICES = ("D-SIM-LAPTOP-01", "D-SIM-PHONE-02", "D-SIM-TABLET-03")


def simulate_login(session: Session, user: User, *, device_id: str | None = None) -> dict:
    """Record one clearly-labelled simulated sign-in from a new device.

    This is an explicit demo action, not a random generator: it writes one security
    event, raises one notification, and returns both. Nothing on a page ever invents
    an alert on its own.
    """
    from app.services.auth import record_login_event

    account_ids = repo.accounts.owned_account_ids(session, user.id)
    if not account_ids:
        raise ValidationError(
            "This account has no accounts attached yet, so there is nothing to record a login "
            "against. Run the seeder: python -m app.db.seed"
        )

    known = {
        device
        for device, _country, _seen in repo.detections.known_devices(session, account_ids)
        if device
    }
    candidate = (device_id or "").strip()
    if not candidate:
        candidate = next(
            (device for device in SIMULATED_DEVICES if device not in known),
            f"D-SIM-{new_id('', 6)}",
        )
    country = "SG" if candidate.endswith("01") else "AE"

    event_id = record_login_event(
        session,
        user,
        event_type=events.LOGIN_NEW_DEVICE,
        severity="MEDIUM",
        device_id=candidate[:40],
        country=country,
        details={"simulated": True, "requested_by": "customer"},
    )
    session.commit()

    notification = notifications.notify(
        session,
        user_id=user.id,
        kind="SECURITY_NEW_DEVICE",
        severity="MEDIUM",
        title="New device signed in to your account",
        body=f"A sign-in from device {candidate} was recorded for this demonstration.",
        entity_type="security_event",
        entity_id=event_id,
        dedupe_key=f"simulated-device:{user.id}:{candidate}",
    )
    session.commit()

    record_audit(
        session,
        action="SIMULATE_LOGIN",
        actor=user.email,
        entity_type="security_event",
        entity_id=event_id,
        details={"device_id": candidate, "country": country},
    )

    row = repo.detections.security_event_by_id(session, event_id)
    shaped = _shape_event(row) if row is not None else {
        "id": event_id,
        "timestamp": utcnow(),
        "event_type": events.LOGIN_NEW_DEVICE,
        "description": SECURITY_EVENT_DESCRIPTIONS[events.LOGIN_NEW_DEVICE],
        "severity": "MEDIUM",
        "device_id": candidate,
        "country": country,
        "source_ip_hash": None,
    }
    return {
        "message": f"A simulated sign-in from {candidate} was recorded.",
        "event": shaped,
        "device_id": candidate,
        "country": country,
        "notification_id": None if notification is None else notification.id,
    }


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
def _features_for_transfer(
    session: Session,
    account: Account,
    *,
    amount_major: float,
    when: datetime,
    channel: str,
    country: str,
    device_id: str,
) -> dict:
    """Build the 15 model features for one transfer from the account's real history.

    The history is read from the ledger rather than invented, so "is this device
    new?" and "how does this amount compare with the customer's usual spending?"
    are answered from what actually happened on this account.
    """
    store = UserStateStore()
    history = repo.transactions.account_history(session, account.id, limit=120)
    if history:
        store.seed_history(
            user_id=account.id,
            amounts=[minor_units_to_float(row.amount_minor) for row in history],
            timestamps=[row.timestamp for row in history],
            channel=history[-1].channel,
            country=history[-1].country,
            device_id=history[-1].device_id,
        )
        state = store.get(account.id)
        state.known_devices.update(row.device_id for row in history if row.device_id)
        state.known_countries.update(row.country for row in history if row.country)
    return store.compute_features(
        user_id=account.id,
        amount=amount_major,
        ts=when,
        channel=channel,
        country=country,
        device_id=device_id,
    )


def _correlation_for_transfer(
    session: Session,
    account: Account,
    *,
    counterparty_id: str,
    when: datetime,
    features: dict,
) -> CorrelationResult:
    """Correlate this transfer with the customer's own recent security telemetry.

    The same rules the attack simulator uses are applied to what this account has
    actually recorded: a sign-in from a new device immediately followed by a large
    transfer raises the score here for the same reason it does in a scripted
    scenario.
    """
    lookback = when - timedelta(minutes=settings.thresholds.correlation_lookback_minutes * 2)
    recent = repo.detections.security_events_for_accounts(session, [account.id], limit=30)
    views = [
        SecurityEventView.from_row(event)
        for event in recent
        if event.timestamp >= lookback
    ]

    seen_before = any(
        row.counterparty_id == counterparty_id
        for row in repo.transactions.account_history(session, account.id, limit=200)
    )
    return correlate(
        TransactionView(
            account_id=account.id,
            timestamp=when,
            amount_minor=to_minor_units(features["amount"]),
            amount_over_user_avg=float(features["amount_over_user_avg"]),
            counterparty_id=counterparty_id,
        ),
        views,
        new_beneficiary_ids=set() if seen_before else {counterparty_id},
    )


def score_outgoing_payment(
    session: Session,
    source: Account,
    *,
    amount_major: float,
    when: datetime,
    device_id: str,
    counterparty_id: str,
) -> tuple[object, str]:
    """Score one outgoing payment with the platform's real detection engine.

    Returns ``(decision_record, configuration_used)``. This is the *only* way a
    customer transfer is scored, and the seeder calls it too, so a seeded payment and
    a live transfer produce the same kind of detection row from the same code.
    """
    config = "COMBINED" if models_ready() else "RULE_BASED"
    features = _features_for_transfer(
        session,
        source,
        amount_major=amount_major,
        when=when,
        channel=TRANSFER_CHANNEL,
        country=source.home_country,
        device_id=device_id,
    )
    correlation = _correlation_for_transfer(
        session, source, counterparty_id=counterparty_id, when=when, features=features
    )
    try:
        return evaluate_transaction(
            features, defense_config=config, correlation=correlation, explain=True
        ), config
    except DetectionUnavailable:
        # The artefacts vanished between the readiness check and the call. Falling
        # back is stated, never silent.
        logger.warning("COMBINED became unavailable during a transfer; using RULE_BASED.")
        return evaluate_transaction(
            features, defense_config="RULE_BASED", correlation=correlation, explain=True
        ), "RULE_BASED"


def create_transfer(
    session: Session,
    user: User,
    *,
    source_account_id: str,
    recipient_account_id: str,
    amount: Decimal,
    description: str = "",
    idempotency_key: str | None = None,
    device_id: str = WEB_DEVICE,
) -> dict:
    """Move money between two accounts, in one commit, after a real risk assessment."""
    amount_minor = to_minor_units(amount)
    if amount_minor < settings.transfer_min_minor:
        raise ValidationError(
            f"amount must be at least {minor_units_to_float(settings.transfer_min_minor):,.2f} INR."
        )
    if amount_minor > settings.transfer_max_minor:
        raise ValidationError(
            f"amount must be at most {minor_units_to_float(settings.transfer_max_minor):,.2f} INR."
        )

    memo = (description or "").strip()
    memo = sanitise_text(memo, max_length=140, field="description") if memo else ""

    key = (idempotency_key or "").strip()[:80] or None
    if key:
        existing = repo.transactions.by_idempotency_key(session, key)
        if existing is not None:
            return _replay_transfer(session, user, existing)

    source = repo.accounts.get_account(session, source_account_id)
    if source is None:
        raise LookupError(f"No account with id {source_account_id}.")
    if source.owner_user_id != user.id:
        raise AccessDenied("You can only send money from an account you own.")

    if recipient_account_id == source_account_id:
        raise ValidationError("Choose a recipient account that is different from the source.")
    recipient = repo.accounts.get_account(session, recipient_account_id)
    if recipient is None:
        raise LookupError(f"No recipient account with id {recipient_account_id}.")
    if recipient.owner_user_id is not None and recipient.owner_user_id != user.id:
        raise AccessDenied("That recipient account belongs to another customer.")

    if int(source.balance_minor) < amount_minor:
        raise ValidationError(
            f"Insufficient available balance: {minor_units_to_float(source.balance_minor):,.2f} INR "
            f"available, {minor_units_to_float(amount_minor):,.2f} INR requested."
        )

    window_start = utcnow() - timedelta(hours=24)
    already_moved = repo.transactions.debits_since(session, source.id, window_start)
    if already_moved + amount_minor > settings.transfer_daily_limit_minor:
        raise ValidationError(
            "This transfer would exceed the 24-hour transfer limit of "
            f"{minor_units_to_float(settings.transfer_daily_limit_minor):,.2f} INR "
            f"({minor_units_to_float(already_moved):,.2f} INR already sent)."
        )

    if not key:
        recent = _duplicate_in_window(
            session, source.id, recipient.id, amount_minor, memo, window_start
        )
        if recent is not None:
            return _replay_transfer(session, user, recent, duplicate=True)

    transfer_id = new_id("TRF")
    when = utcnow()
    decision, config = score_outgoing_payment(
        session,
        source,
        amount_major=minor_units_to_float(amount_minor),
        when=when,
        device_id=(device_id or WEB_DEVICE)[:40],
        counterparty_id=recipient.id,
    )
    status = str(decision.response)

    debit_id = new_id("TXN")
    credit_id = new_id("TXN")
    repo.transactions.add_transaction(
        session,
        {
            "id": debit_id,
            "run_id": None,
            "account_id": source.id,
            "counterparty_id": recipient.id,
            "counterparty_label": recipient.label,
            "timestamp": when,
            "amount_minor": amount_minor,
            "currency": CURRENCY,
            "channel": TRANSFER_CHANNEL,
            "country": source.home_country,
            "merchant_id": f"PAYEE-{recipient.id}",
            "device_id": (device_id or WEB_DEVICE)[:40],
            "direction": "DEBIT",
            "status": status,
            "is_fraud": None,
            "scenario": None,
            "idempotency_key": key,
            "description": memo or f"Transfer to {recipient.label}",
            "transfer_id": transfer_id,
        },
    )
    repo.transactions.add_transaction(
        session,
        {
            "id": credit_id,
            "run_id": None,
            "account_id": recipient.id,
            "counterparty_id": source.id,
            "counterparty_label": source.label,
            "timestamp": when,
            "amount_minor": amount_minor,
            "currency": CURRENCY,
            "channel": TRANSFER_CHANNEL,
            "country": recipient.home_country,
            "merchant_id": f"PAYEE-{source.id}",
            "device_id": (device_id or WEB_DEVICE)[:40],
            "direction": "CREDIT",
            "status": status,
            "is_fraud": None,
            "scenario": None,
            "description": memo or f"Transfer from {source.label}",
            "transfer_id": transfer_id,
        },
    )
    repo.detections.add_detection(
        session,
        {
            "id": new_id("DET"),
            "run_id": None,
            "account_id": source.id,
            "transaction_id": debit_id,
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

    # Money moves only when the transfer settled. Both deltas are applied to the
    # same session, so the pair commits together.
    if status == "SETTLED":
        repo.accounts.apply_balance_delta(session, source, -amount_minor)
        repo.accounts.apply_balance_delta(session, recipient, amount_minor)

    try:
        session.commit()
    except IntegrityError:
        # Two identical submissions raced past the lookup above. The unique index on
        # ``idempotency_key`` is the real guarantee; the loser returns the winner.
        session.rollback()
        if key:
            existing = repo.transactions.by_idempotency_key(session, key)
            if existing is not None:
                return _replay_transfer(session, user, existing, duplicate=True)
        raise

    record_audit(
        session,
        action="TRANSFER_SUBMITTED",
        actor=user.email,
        entity_type="transfer",
        entity_id=transfer_id,
        outcome="SUCCESS" if status == "SETTLED" else "HELD",
        details={
            "source_account": source.id,
            "recipient_account": recipient.id,
            "amount_minor": amount_minor,
            "status": status,
            "decision": str(decision.decision),
            "risk_score": float(decision.fraud_risk_score),
            "defense_config": config,
        },
    )

    notification_id = _announce_transfer(
        session, user, transfer_id=transfer_id, status=status, decision=decision,
        amount_minor=amount_minor, source=source, recipient=recipient,
    )
    return _transfer_payload(
        session,
        user,
        transfer_id=transfer_id,
        status=status,
        decision=decision,
        amount_minor=amount_minor,
        memo=memo,
        when=when,
        source=source,
        recipient=recipient,
        duplicate=False,
        notification_id=notification_id,
    )


def _duplicate_in_window(
    session: Session,
    source_id: str,
    recipient_id: str,
    amount_minor: int,
    memo: str,
    window_start: datetime,
) -> Transaction | None:
    """An identical submission from the same account inside the duplicate window."""
    for row in repo.transactions.account_history(session, source_id, limit=25):
        if (
            row.direction == "DEBIT"
            and row.counterparty_id == recipient_id
            and int(row.amount_minor) == amount_minor
            and (row.description or "") == (memo or f"Transfer to {row.counterparty_label or recipient_id}")
            and row.timestamp >= window_start
            and row.transfer_id
        ):
            return row
    return None


def _replay_transfer(
    session: Session, user: User, debit: Transaction, *, duplicate: bool = True
) -> dict:
    """Rebuild the original result for a replayed submission."""
    transfer_id = debit.transfer_id or ""
    legs = repo.transactions.transfer_legs(session, transfer_id) if transfer_id else [debit]
    debit = next((leg for leg in legs if leg.direction == "DEBIT"), debit)
    credit = next((leg for leg in legs if leg.direction == "CREDIT"), None)
    detection = repo.detections.detection_for_transaction(session, debit.id)
    source = repo.accounts.get_account(session, debit.account_id)
    recipient = repo.accounts.get_account(session, credit.account_id) if credit else None
    if source is None or recipient is None:
        raise LookupError(f"No transfer with id {transfer_id}.")
    from app.services.detection import DecisionRecord

    decision = DecisionRecord(
        detection_source=(detection.detection_source if detection else "UNKNOWN"),
        transaction_risk_score=float(detection.fraud_risk_score) if detection else 0.0,
        fraud_risk_score=float(detection.fraud_risk_score) if detection else 0.0,
        decision=(detection.decision if detection else "ALLOW"),
        response=debit.status,
        reasons=list(detection.reasons or []) if detection else [],
        rules_triggered=list(detection.rules_triggered or []) if detection else [],
    )
    return _transfer_payload(
        session,
        user,
        transfer_id=transfer_id,
        status=debit.status,
        decision=decision,
        amount_minor=int(debit.amount_minor),
        memo=debit.description or "",
        when=debit.timestamp,
        source=source,
        recipient=recipient,
        duplicate=duplicate,
        notification_id=None,
    )


def _transfer_message(status: str, decision) -> str:
    if status == "SETTLED":
        if str(decision.decision) == "ALLOW":
            return "Transfer completed."
        return "Transfer completed. The risk engine queried this payment; the details are on the transaction."
    if status == "BLOCKED":
        return (
            "Transfer blocked by the automated defense. No money left your account. "
            "The decision and the rules behind it are on the transaction."
        )
    return (
        "Transfer is under review. No money has left your account yet; the payment will settle or "
        "be released once the review completes."
    )


def _announce_transfer(
    session: Session,
    user: User,
    *,
    transfer_id: str,
    status: str,
    decision,
    amount_minor: int,
    source: Account,
    recipient: Account,
) -> str | None:
    """Raise the notifications a transfer genuinely warrants. Nothing else."""
    if status == "SETTLED" and str(decision.decision) == "ALLOW":
        return None
    amount = minor_units_to_float(amount_minor)
    if status == "BLOCKED":
        title = f"Transfer of {amount:,.2f} INR blocked"
        body = (
            f"The payment from {source.label} to {recipient.label} was blocked by the automated "
            "defense. No money left your account."
        )
        severity = "HIGH"
    elif status == "PENDING_REVIEW":
        title = f"Transfer of {amount:,.2f} INR under review"
        body = (
            f"The payment from {source.label} to {recipient.label} was held for review. "
            "No money has left your account yet."
        )
        severity = "MEDIUM"
    else:
        title = f"Transfer of {amount:,.2f} INR queried"
        body = (
            f"The payment from {source.label} to {recipient.label} settled and was queried by the "
            "risk engine."
        )
        severity = "LOW"

    own = notifications.notify(
        session,
        user_id=user.id,
        kind="TRANSFER_ALERT",
        severity=severity,
        title=title,
        body=body,
        entity_type="transfer",
        entity_id=transfer_id,
        dedupe_key=f"transfer:{transfer_id}:customer",
    )
    notifications.notify(
        session,
        role=ROLE_ANALYST,
        kind="TRANSFER_ALERT",
        severity=severity,
        title=title,
        body=body,
        entity_type="transfer",
        entity_id=transfer_id,
        dedupe_key=f"transfer:{transfer_id}:analyst",
    )
    session.commit()
    return None if own is None else own.id


def _transfer_payload(
    session: Session,
    user: User,
    *,
    transfer_id: str,
    status: str,
    decision,
    amount_minor: int,
    memo: str,
    when: datetime,
    source: Account,
    recipient: Account,
    duplicate: bool,
    notification_id: str | None,
) -> dict:
    """Assemble the result screen, including both balances as they now stand."""
    session.refresh(source)
    session.refresh(recipient)
    debit = next(
        (
            leg
            for leg in repo.transactions.transfer_legs(session, transfer_id)
            if leg.direction == "DEBIT"
        ),
        None,
    )
    detection = (
        repo.detections.detection_for_transaction(session, debit.id) if debit is not None else None
    )
    label = "Pending" if status == "PENDING_REVIEW" else STATUS_LABELS.get(status, status.title())
    transactions = [
        shape_customer_transaction(
            leg,
            account_label=(source.label if leg.direction == "DEBIT" else recipient.label),
            detection=detection if leg.direction == "DEBIT" else None,
        )
        for leg in repo.transactions.transfer_legs(session, transfer_id)
    ]
    return {
        "transfer_id": transfer_id,
        "status": status,
        "status_label": label,
        "decision": str(decision.decision),
        "risk_score": round(float(decision.fraud_risk_score), 4),
        "reasons": list(decision.reasons),
        "rules_triggered": list(decision.rules_triggered),
        "message": _transfer_message(status, decision),
        "duplicate": duplicate,
        "settled": status == "SETTLED",
        "amount_minor": int(amount_minor),
        "amount": minor_units_to_float(amount_minor),
        "currency": CURRENCY,
        "description": memo,
        "created_at": when,
        "source": _leg(source, "DEBIT", amount_minor, user.id),
        "recipient": _leg(recipient, "CREDIT", amount_minor, user.id),
        "transactions": transactions,
        "notification_id": notification_id,
    }


def _leg(account: Account, direction: str, amount_minor: int, owner_id: str) -> dict:
    return {
        "account_id": account.id,
        "label": account.label,
        "direction": direction,
        "amount_minor": int(amount_minor),
        "amount": minor_units_to_float(amount_minor),
        "balance_minor": int(account.balance_minor),
        "balance": minor_units_to_float(account.balance_minor),
        "masked_number": masked_account_number(account.id),
        "is_own": account.owner_user_id == owner_id,
    }


# ---------------------------------------------------------------------------
# Linking a simulation run to the customer
# ---------------------------------------------------------------------------
def link_run_accounts(session: Session, run_id: str, victim_account_ids: list[str]) -> int:
    """Point the demo customer(s) at the roster of the run that just finished.

    This is what makes the two halves of the platform describe one ledger: the
    account the scenario attacked becomes the account the customer sees, so its
    transactions, security events, detections and incidents appear on both sides
    without a single duplicated row.

    Only accounts the *last* run attached are released, so the customer's own seeded
    accounts are never touched and the list does not grow with the number of runs.
    """
    if not victim_account_ids:
        return 0
    customers = repo.users.list_users(session, role="CUSTOMER")
    if not customers:
        return 0
    for user in customers:
        repo.accounts.detach_scenario_accounts(session, user.id)

    linked = 0
    for position, victim_id in enumerate(victim_account_ids):
        owner = customers[position % len(customers)]
        if repo.accounts.attach_owner(session, victim_id, owner.id):
            linked += 1
    if linked:
        session.commit()
        logger.info("Linked %d scenario account(s) from run %s to demo customers.", linked, run_id)
    return linked


__all__ = [
    "DUPLICATE_WINDOW_SECONDS",
    "FLAGGED_LABEL",
    "STATUS_LABELS",
    "WEB_DEVICE",
    "account_card",
    "account_type_for",
    "accounts_overview",
    "create_transfer",
    "describe_transaction",
    "link_run_accounts",
    "list_payees",
    "masked_account_number",
    "risk_level_for",
    "score_outgoing_payment",
    "security_centre",
    "shape_account_card",
    "shape_customer_incident",
    "shape_customer_transaction",
    "simulate_login",
    "status_label_for",
    "transaction_detail",
    "transaction_page",
]
