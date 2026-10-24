"""Security-event correlation.

The research question this module exists to answer is:

    Does combining transaction anomaly detection with security-event correlation
    reduce simulated financial losses compared with transaction-only detection?

Correlation is therefore a **separate, testable component**, not a hidden tweak
inside the scorer. It takes the security telemetry that happened *before and around*
one transaction, evaluates a small set of readable rules with configurable
thresholds, and returns a score plus the evidence that produced it.

Design rules that matter:

* **Transactional (no global state).** :func:`correlate` is a pure function of its
  arguments, so a test can pin an exact expected score.
* **Evidence, not conclusions.** Every rule returns the ids of the events that
  triggered it. The UI shows those events so a human can disagree with the rule.
* **Only raises risk.** Correlation can push a borderline transaction over the
  block threshold; it can never mark something as safe.
* **No duplicate alerts.** :func:`dedupe_alerts` collapses repeats of the same
  (account, rule) inside a window, because a burst of 40 failed logins is one
  incident, not forty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import ThresholdSettings, settings
from app.services import event_types as events
from app.utils.timeutils import minutes_between

#: Each rule's contribution to the correlation score. Weights are additive and
#: clamped at 1.0, so two strong independent signals are stronger than one.
RULE_WEIGHTS = {
    "C-FAILED-LOGIN-BURST": 0.45,
    "C-CREDENTIAL-COMPROMISE": 0.30,
    "C-NEW-DEVICE-HIGH-VALUE": 0.30,
    "C-PAYMENT-TAMPERING": 0.40,
    "C-COORDINATED-FANOUT": 0.35,
    "C-SHARED-DEVICE": 0.35,
    "C-MULE-FUNDING": 0.35,
    "C-CARD-TESTING": 0.25,
}

RULE_DESCRIPTIONS = {
    "C-FAILED-LOGIN-BURST": "Repeated failed logins immediately before the transaction.",
    "C-CREDENTIAL-COMPROMISE": "A login from an unusual place or device preceded the transaction.",
    "C-NEW-DEVICE-HIGH-VALUE": "A previously unseen device was used for a high-value transaction.",
    "C-PAYMENT-TAMPERING": "Payment details changed shortly before the transaction.",
    "C-COORDINATED-FANOUT": "Money is being spread across several new beneficiaries at once.",
    "C-SHARED-DEVICE": "The same device is active on more than one account.",
    "C-MULE-FUNDING": "An incoming wire was immediately followed by outgoing transfers.",
    "C-CARD-TESTING": "A burst of tiny probe transactions precedes this one.",
}


@dataclass(frozen=True)
class SecurityEventView:
    """Framework-free view of one security event, so rules are unit-testable."""

    id: str
    account_id: str
    timestamp: datetime
    event_type: str
    severity: str = "INFO"
    country: str | None = None
    device_id: str | None = None
    details: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row) -> "SecurityEventView":
        """Adapt a ``SecurityEvent`` ORM row (or anything with the same attributes)."""
        return cls(
            id=row.id,
            account_id=row.account_id,
            timestamp=row.timestamp,
            event_type=row.event_type,
            severity=row.severity or "INFO",
            country=row.country,
            device_id=row.device_id,
            details=dict(row.details or {}),
        )


@dataclass(frozen=True)
class TransactionView:
    """The bits of a transaction the correlation rules are allowed to look at."""

    account_id: str
    timestamp: datetime
    amount_minor: int
    amount_over_user_avg: float = 1.0
    counterparty_id: str | None = None


@dataclass
class CorrelationResult:
    score: float = 0.0
    rules: list[str] = field(default_factory=list)
    evidence_event_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.rules)


def _within(event: SecurityEventView, reference: datetime, lookback_minutes: float) -> bool:
    """True when ``event`` happened at or before ``reference`` inside the window."""
    delta = minutes_between(event.timestamp, reference)
    return -1e-6 <= delta <= lookback_minutes


def correlate(
    transaction: TransactionView,
    security_events: list[SecurityEventView],
    *,
    thresholds: ThresholdSettings | None = None,
    shared_device_accounts: set[str] | None = None,
    new_beneficiary_ids: set[str] | None = None,
) -> CorrelationResult:
    """Evaluate every correlation rule for one transaction.

    ``security_events`` is expected to already be scoped to this account (the
    caller filters by account and time). ``shared_device_accounts`` and
    ``new_beneficiary_ids`` carry the two signals that need a wider view than a
    single account; the engine computes them and passes them in, which keeps this
    function pure.
    """
    config = thresholds or settings.thresholds
    result = CorrelationResult()

    window = float(config.correlation_lookback_minutes)
    relevant = [event for event in security_events if _within(event, transaction.timestamp, window)]

    def record(
        code: str, evidence: list[SecurityEventView], reason: str, *, require_evidence: bool = True
    ) -> None:
        """Record a triggered rule.

        ``require_evidence`` is ``False`` for the two rules that can also be
        triggered by a wider-view signal instead of by security events (fan-out to
        many new beneficiaries, and a device shared across accounts). Those signals
        arrive as id sets rather than event rows, so there is no evidence row to
        attach — the reason text carries the justification instead. Without this
        escape hatch the rule was silently dropped whenever the wider-view signal
        was the one that fired.
        """
        if require_evidence and not evidence:
            return
        result.rules.append(code)
        result.evidence_event_ids.extend(event.id for event in evidence)
        result.reasons.append(reason)

    # 1. Repeated failed logins immediately before the transaction.
    failed_logins = [e for e in relevant if e.event_type == events.FAILED_LOGIN]
    burst = [
        event
        for event in failed_logins
        if minutes_between(event.timestamp, transaction.timestamp) <= config.failed_login_window_minutes
    ]
    if len(burst) >= config.failed_login_burst:
        record(
            "C-FAILED-LOGIN-BURST",
            burst,
            f"{len(burst)} failed logins within {config.failed_login_window_minutes} minutes "
            "before this transaction",
        )

    # 2. A suspicious login (new device / new country / impossible travel).
    compromise = [
        event
        for event in relevant
        if event.event_type in (events.LOGIN_NEW_DEVICE, events.LOGIN_FOREIGN_IP, events.IMPOSSIBLE_TRAVEL)
    ]
    if compromise:
        record(
            "C-CREDENTIAL-COMPROMISE",
            compromise,
            f"Unusual login activity recorded before this transaction "
            f"({', '.join(sorted({event.event_type for event in compromise}))})",
        )

    # 3. A never-before-seen device combined with a high-value amount.
    new_device = [event for event in relevant if event.event_type == events.NEW_DEVICE]
    if new_device and transaction.amount_minor >= config.new_device_high_value_minor:
        record(
            "C-NEW-DEVICE-HIGH-VALUE",
            new_device,
            "Transaction above the high-value threshold from a previously unseen device",
        )

    # 4. Payment details changed shortly before the transaction.
    tampering = [event for event in relevant if event.event_type in events.PAYMENT_INTEGRITY_EVENTS]
    if tampering:
        record(
            "C-PAYMENT-TAMPERING",
            tampering,
            f"Payment details were modified before this transaction "
            f"({', '.join(sorted({event.event_type for event in tampering}))})",
        )

    # 5. Coordinated fan-out to many new beneficiaries.
    fanout = [event for event in relevant if event.event_type in (events.NEW_BENEFICIARY, events.RAPID_FANOUT)]
    distinct_beneficiaries = set(new_beneficiary_ids or ())
    if len(fanout) >= config.coordinated_fanout_beneficiaries or (
        len(distinct_beneficiaries) >= config.coordinated_fanout_beneficiaries
    ):
        record(
            "C-COORDINATED-FANOUT",
            fanout,
            f"Transfers to {max(len(fanout), len(distinct_beneficiaries))} new beneficiaries "
            f"within {int(window)} minutes",
            require_evidence=False,
        )

    # 6. The same device active on several accounts.
    if shared_device_accounts:
        shared = [
            event
            for event in relevant
            if event.device_id and event.account_id in shared_device_accounts
        ]
        if shared or (transaction.account_id in shared_device_accounts):
            record(
                "C-SHARED-DEVICE",
                shared,
                "This device has been observed on more than one account in the window",
                require_evidence=False,
            )

    # 7. Incoming wire immediately split into outgoing transfers (mule behaviour).
    funding = [event for event in relevant if event.event_type == events.MULE_FUNDING_RECEIVED]
    if funding:
        record("C-MULE-FUNDING", funding, "An incoming transfer was immediately followed by outgoing transfers")

    # 8. Tiny probe transactions preceding a larger one (card testing).
    probes = [event for event in relevant if event.event_type == events.CARD_TESTING_PROBE]
    if probes:
        record("C-CARD-TESTING", probes, "Burst of very small transactions preceded this transaction")

    total = sum(RULE_WEIGHTS.get(code, 0.0) for code in result.rules)
    result.score = round(min(total, 1.0), 4)
    result.evidence_event_ids = sorted(set(result.evidence_event_ids))
    return result


def rule_reference() -> list[dict]:
    """The rule catalogue, exposed through the UI so thresholds are not a black box."""
    return [
        {
            "code": code,
            "weight": weight,
            "description": RULE_DESCRIPTIONS.get(code, ""),
        }
        for code, weight in RULE_WEIGHTS.items()
    ]


def dedupe_alerts(alerts: list[dict], *, window_minutes: float = 15.0) -> list[dict]:
    """Collapse repeated alerts for the same account and rule inside a window.

    A credential-stuffing run against one account produces dozens of identical
    detections. Raising one alert per repeat would flood the incident queue and
    hide the incidents that do need attention, so repeats are folded into the first
    occurrence. ``alerts`` entries must carry ``account_id``, ``code`` and
    ``timestamp`` (naive UTC); the returned list keeps the first occurrence and
    counts the suppressed duplicates in ``duplicates``.
    """
    kept: list[dict] = []
    for alert in sorted(alerts, key=lambda item: item["timestamp"]):
        duplicate_of = None
        for existing in kept:
            if existing["account_id"] != alert["account_id"] or existing["code"] != alert["code"]:
                continue
            if minutes_between(existing["timestamp"], alert["timestamp"]) <= window_minutes:
                duplicate_of = existing
                break
        if duplicate_of is None:
            kept.append({**alert, "duplicates": 0})
        else:
            duplicate_of["duplicates"] = int(duplicate_of.get("duplicates", 0)) + 1
    return kept
