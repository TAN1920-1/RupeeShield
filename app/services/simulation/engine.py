"""Simulation engine: replays a scenario plan against a defense configuration.

Responsibilities, in order:

1. write the run's synthetic account roster (fresh per run, so runs are independent);
2. warm each account's in-memory behavioural state so its first live transaction is
   not scored against an empty history;
3. walk the scripted security telemetry and transactions in chronological order;
4. score every transaction through :mod:`app.services.detection`, correlate it with
   the security events that preceded it, and act on the decision;
5. open one incident per account that showed suspicious activity and record its
   timeline;
6. compute and store the financial impact for the run and for each incident.

Everything is local, in-process and seeded. Nothing here contacts a real bank, a
real payment rail or any external system, and no real personal data is involved.

Streaming mode is the same loop with a pacing delay and a stop flag, so the Live
Monitoring feed is produced by this engine rather than by the browser.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Event as ThreadEvent

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Account, DefenseAction
from app.ml.features import UserStateStore
from app.services import event_types as events
from app.services.correlation import (
    CorrelationResult,
    SecurityEventView,
    TransactionView,
    correlate,
    dedupe_alerts,
)
from app.services.detection import DecisionRecord, evaluate_transaction, models_ready
from app.services.financial_impact import ImpactBreakdown, compute_impact
from app.services.simulation import catalog
from app.services.simulation.accounts import account_id, account_row, seed_history_for
from app.services.simulation.scenarios import ScenarioPlan
from app.utils.ids import new_id
from app.utils.money import from_minor_units, minor_units_to_float
from app.utils.timeutils import minutes_between

logger = get_logger(__name__)

SECURITY_KIND = "SECURITY_EVENT"
TRANSACTION_KIND = "TRANSACTION"
DETECTION_KIND = "DETECTION"
DEFENSE_KIND = "DEFENSE"

ALERT_SEVERITIES = {"HIGH", "CRITICAL"}


@dataclass
class ExecutionOutcome:
    status: str
    generated_events: int
    incident_ids: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    impact: ImpactBreakdown | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class _AccountRuntime:
    """Mutable per-account bookkeeping while a run is being replayed."""

    index: int
    account_db_id: str
    timeline: list[dict] = field(default_factory=list)
    security_events: list[SecurityEventView] = field(default_factory=list)
    beneficiaries: list[tuple[datetime, str]] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    max_severity: str = "INFO"
    detections: int = 0
    blocked: int = 0
    flagged: int = 0


def _severity_rank(severity: str) -> int:
    order = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return order.get(severity, 0)


def _ratio(numerator: int, denominator: int) -> float | None:
    """Ratio that stays ``None`` when it is undefined instead of pretending to be 0."""
    return round(numerator / denominator, 4) if denominator else None


def execute_plan(
    session: Session,
    plan: ScenarioPlan,
    *,
    run_id: str,
    defense_config: str,
    start_time: datetime,
    stop_flag: ThreadEvent | None = None,
    pace_per_second: float | None = None,
) -> ExecutionOutcome:
    """Replay ``plan`` under ``defense_config`` and persist everything it produces."""
    notes: list[str] = []
    config = (defense_config or "COMBINED").upper()
    if config == "COMBINED" and not models_ready():
        config = "RULE_BASED"
        notes.append(
            "COMBINED was requested but the trained ML artefacts are missing, so this run used the "
            "RULE_BASED configuration instead. Fix with: python scripts/generate_dataset.py then "
            "python -m app.ml.train"
        )
        logger.warning("Run %s downgraded from COMBINED to RULE_BASED (no model artefacts).", run_id)

    # -- 1. account roster -------------------------------------------------
    repo.accounts.add_accounts(session, [account_row(run_id, account) for account in plan.accounts])
    runtimes = {
        account.index: _AccountRuntime(index=account.index, account_db_id=account_id(run_id, account.index))
        for account in plan.accounts
    }

    # -- 2. warm the feature state ----------------------------------------
    store = warm_feature_store(plan, start_time, run_id=run_id)

    # -- 3. chronological queue -------------------------------------------
    queue = chronological_queue(plan)

    # -- 4. replay the scenario -------------------------------------------
    device_accounts: dict[str, set[str]] = {}
    processed = 0
    status = "COMPLETED"
    interval = 1.0 / pace_per_second if pace_per_second else None
    next_deadline = time.monotonic()

    for offset, kind, item in queue:
        if stop_flag is not None and stop_flag.is_set():
            status = "STOPPED"
            notes.append(f"Run stopped by the operator after {processed} of {len(queue)} events.")
            break
        if interval is not None:
            next_deadline += interval
            delay = next_deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)

        timestamp = start_time + timedelta(seconds=offset)
        if kind == 0:
            _record_security_event(session, run_id, plan, runtimes, item, timestamp)
        else:
            _process_transaction(
                session, run_id, plan, runtimes, device_accounts, store, item, timestamp, config
            )

        session.commit()
        processed += 1

    # -- 5. incidents -----------------------------------------------------
    incident_ids = _open_incidents(session, run_id, plan, runtimes, config)

    # -- 6. financial impact ---------------------------------------------
    impact = _compute_run_impact(session, run_id, incident_ids)
    summary = _build_summary(session, run_id, plan, runtimes, config, incident_ids, impact, notes)
    return ExecutionOutcome(
        status=status,
        generated_events=processed,
        incident_ids=incident_ids,
        summary=summary,
        impact=impact,
        notes=notes,
    )


def _stable_rng(seed: int, account_index: int):
    import numpy as np

    # Per-account stream so account N's warm-up history does not depend on how many
    # accounts came before it.
    return np.random.default_rng((seed * 1_000_003 + account_index) % (2**32 - 1))


def warm_feature_store(plan: ScenarioPlan, start_time: datetime, *, run_id: str) -> UserStateStore:
    """Build a :class:`UserStateStore` holding each account's pre-run history.

    Public because the incident replay must start from *exactly* the same feature
    state the original run did. Rebuilding it from the plan is the only way to do
    that: the warm-up history is deliberately never written to the ledger (it would
    pollute the run's statistics), so it cannot be read back from the database.
    """
    store = UserStateStore()
    for account in plan.accounts:
        history = seed_history_for(_stable_rng(plan.seed, account.index), account)
        store.seed_history(
            user_id=account_id(run_id, account.index),
            amounts=[float(from_minor_units(amount)) for _days, amount in history],
            timestamps=[start_time - timedelta(days=days) for days, _amount in history],
            channel=account.preferred_channel,
            country=account.home_country,
            device_id=f"D-BASE-{account.index:04d}",
        )
    return store


def chronological_queue(plan: ScenarioPlan) -> list[tuple[int, int, object]]:
    """The plan as one ordered work queue: ``(offset_seconds, kind, item)``.

    ``kind`` is 0 for a security event and 1 for a transaction. Security telemetry
    is processed before a transaction at the same offset, because the correlation
    rules look *backwards* in time from the transaction.
    """
    queue: list[tuple[int, int, object]] = [
        (event.offset_seconds, 0, event) for event in plan.security_events
    ]
    queue.extend((transaction.offset_seconds, 1, transaction) for transaction in plan.transactions)
    queue.sort(key=lambda item: (item[0], item[1]))
    return queue


def _record_security_event(
    session: Session,
    run_id: str,
    plan: ScenarioPlan,
    runtimes: dict[int, _AccountRuntime],
    event,
    timestamp: datetime,
) -> None:
    runtime = runtimes[event.account_index]
    event_id = new_id("SEC")
    repo.detections.add_security_event(
        session,
        {
            "id": event_id,
            "run_id": run_id,
            "account_id": runtime.account_db_id,
            "timestamp": timestamp,
            "event_type": event.event_type,
            "severity": event.severity,
            "country": event.country,
            "device_id": event.device_id,
            # Only a non-reversible pseudonym is stored; no real addresses exist here.
            "source_ip_hash": new_id("IPH", 16),
            "details": {
                **event.details,
                "new_device": event.device_is_new,
                "simulated": True,
            },
        },
    )
    view = SecurityEventView(
        id=event_id,
        account_id=runtime.account_db_id,
        timestamp=timestamp,
        event_type=event.event_type,
        severity=event.severity,
        country=event.country,
        device_id=event.device_id,
        details=dict(event.details),
    )
    runtime.security_events.append(view)
    if _severity_rank(event.severity) > _severity_rank(runtime.max_severity):
        runtime.max_severity = event.severity

    runtime.timeline.append(
        {
            "timestamp": timestamp,
            "kind": SECURITY_KIND,
            "ref_id": event_id,
            "description": _describe_security_event(event),
            "payload": {
                "event_type": event.event_type,
                "severity": event.severity,
                "device_id": event.device_id,
                "country": event.country,
            },
        }
    )


def _describe_security_event(event) -> str:
    templates = {
        events.FAILED_LOGIN: "Failed login attempt",
        events.LOGIN_SUCCESS: "Successful login after repeated failures",
        events.LOGIN_NEW_DEVICE: "Login from a device not seen before",
        events.LOGIN_FOREIGN_IP: "Login from an unusual country",
        events.IMPOSSIBLE_TRAVEL: "Impossible-travel login detected",
        events.PASSWORD_RESET: "Password reset requested",
        events.NEW_DEVICE: "First appearance of a new device",
        events.DEVICE_REPUTATION_LOW: "Device with poor reputation",
        events.PAYMENT_DETAILS_MODIFIED: "Payment details modified",
        events.BENEFICIARY_ADDED: "New beneficiary added",
        events.BENEFICIARY_CHANGED: "Beneficiary changed",
        events.PAYMENT_AMOUNT_TAMPERED: "Payment amount tampered with",
        events.NEW_BENEFICIARY: "Transfer to a previously unseen beneficiary",
        events.MULE_FUNDING_RECEIVED: "Large inbound transfer received",
        events.RAPID_FANOUT: "Rapid fan-out across many beneficiaries",
        events.CARD_TESTING_PROBE: "Small value probe transaction",
        events.ACCOUNT_SUSPENDED: "Account suspended by the defense",
        events.SESSION_REVOKED: "Session revoked",
    }
    base = templates.get(event.event_type, event.event_type.replace("_", " ").title())
    location = f" from {event.country}" if event.country else ""
    return f"{base}{location}."


def _process_transaction(
    session: Session,
    run_id: str,
    plan: ScenarioPlan,
    runtimes: dict[int, _AccountRuntime],
    device_accounts: dict[str, set[str]],
    store: UserStateStore,
    transaction,
    timestamp: datetime,
    config: str,
) -> DecisionRecord:
    runtime = runtimes[transaction.account_index]
    account = plan.account_by_index(transaction.account_index)
    amount_major = float(from_minor_units(transaction.amount_minor))

    features = store.compute_features(
        user_id=runtime.account_db_id,
        amount=amount_major,
        ts=timestamp,
        channel=transaction.channel,
        country=transaction.country,
        device_id=transaction.device_id,
    )

    correlation = CorrelationResult()
    if config == "COMBINED":
        correlation = correlate(
            TransactionView(
                account_id=runtime.account_db_id,
                timestamp=timestamp,
                amount_minor=transaction.amount_minor,
                amount_over_user_avg=float(features["amount_over_user_avg"]),
                counterparty_id=(
                    account_id(run_id, transaction.counterparty_index)
                    if transaction.counterparty_index is not None
                    else None
                ),
            ),
            runtime.security_events,
            shared_device_accounts={
                other
                for other in device_accounts.get(transaction.device_id, set())
                if other != runtime.account_db_id
            },
            new_beneficiary_ids={
                counterparty
                for seen_at, counterparty in runtime.beneficiaries
                if minutes_between(seen_at, timestamp) <= 30.0
            },
        )

    decision = evaluate_transaction(features, defense_config=config, correlation=correlation)
    transaction_id = new_id("TXN")
    detection_id = new_id("DET")

    counterparty_db_id = (
        account_id(run_id, transaction.counterparty_index)
        if transaction.counterparty_index is not None
        else None
    )
    counterparty_label = (
        plan.account_by_index(transaction.counterparty_index).label
        if transaction.counterparty_index is not None
        else "EXTERNAL"
    )
    repo.transactions.add_transaction(
        session,
        {
            "id": transaction_id,
            "run_id": run_id,
            "account_id": runtime.account_db_id,
            "counterparty_id": counterparty_db_id,
            "counterparty_label": counterparty_label,
            "timestamp": timestamp,
            "amount_minor": transaction.amount_minor,
            "currency": "INR",
            "channel": transaction.channel,
            "country": transaction.country,
            "merchant_id": transaction.merchant_id,
            "device_id": transaction.device_id,
            "direction": transaction.direction,
            "status": decision.response,
            # Synthetic ground truth from the scenario script, used for evaluation only.
            "is_fraud": transaction.is_fraud,
            "fraud_type": transaction.fraud_type,
            "scenario": plan.scenario,
        },
    )
    repo.detections.add_detection(
        session,
        {
            "id": detection_id,
            "run_id": run_id,
            "account_id": runtime.account_db_id,
            "transaction_id": transaction_id,
            "timestamp": timestamp,
            "detection_source": decision.detection_source,
            "fraud_risk_score": decision.fraud_risk_score,
            "decision": decision.decision,
            "ml_supervised_score": decision.ml_supervised_score,
            "ml_anomaly_score": decision.ml_anomaly_score,
            "rule_heuristic_score": decision.rule_heuristic_score,
            "correlation_score": decision.correlation_score,
            "reasons": decision.reasons,
            "rules_triggered": decision.rules_triggered,
            "correlated_event_ids": decision.correlated_event_ids,
            "top_features": decision.top_features,
            "latency_ms": decision.latency_ms,
            "ground_truth_fraud": transaction.is_fraud,
        },
    )

    runtime.detections += 1
    if decision.decision == "BLOCK":
        runtime.blocked += 1
    elif decision.decision == "FLAG":
        runtime.flagged += 1

    # Money only moves when the transaction settles. Blocked and held transactions
    # never leave the account, which is exactly why they are not counted as a loss.
    if decision.response == "SETTLED":
        delta = -transaction.amount_minor if transaction.direction == "DEBIT" else transaction.amount_minor
        repo.accounts.apply_balance_delta(session, session.get(Account, runtime.account_db_id), delta)

    runtime.timeline.append(
        {
            "timestamp": timestamp,
            "kind": TRANSACTION_KIND,
            "ref_id": transaction_id,
            "description": (
                f"{transaction.direction.title()} of {minor_units_to_float(transaction.amount_minor):,.2f} INR "
                f"on {transaction.channel} -> {counterparty_label}"
            ),
            "payload": {
                "amount_minor": transaction.amount_minor,
                "status": decision.response,
                "channel": transaction.channel,
                "is_fraud": transaction.is_fraud,
            },
        }
    )
    runtime.timeline.append(
        {
            "timestamp": timestamp,
            "kind": DETECTION_KIND,
            "ref_id": detection_id,
            "description": (
                f"Detection ({decision.detection_source}): {decision.decision} at risk "
                f"{decision.fraud_risk_score:.2f}"
            ),
            "payload": {
                "decision": decision.decision,
                "fraud_risk_score": decision.fraud_risk_score,
                "transaction_risk_score": decision.transaction_risk_score,
                "correlation_score": decision.correlation_score,
                "rules_triggered": decision.rules_triggered,
                "reasons": decision.reasons,
            },
        }
    )

    if decision.decision != "ALLOW":
        action_id = new_id("DEF")
        action_type = "BLOCK_TRANSACTION" if decision.decision == "BLOCK" else "HOLD_FOR_REVIEW"
        repo.incidents.add_defense_action(
            session,
            {
                "id": action_id,
                "run_id": run_id,
                "account_id": runtime.account_db_id,
                "transaction_id": transaction_id,
                "detection_result_id": detection_id,
                "action_type": action_type,
                "timestamp": timestamp,
                "reason": decision.reasons[0] if decision.reasons else "",
                "amount_minor": transaction.amount_minor,
            },
        )
        runtime.timeline.append(
            {
                "timestamp": timestamp,
                "kind": DEFENSE_KIND,
                "ref_id": action_id,
                "description": (
                    f"Defense action: {action_type.replace('_', ' ').lower()} for "
                    f"{minor_units_to_float(transaction.amount_minor):,.2f} INR"
                ),
                "payload": {"action_type": action_type, "amount_minor": transaction.amount_minor},
            }
        )
        runtime.alerts.append(
            {
                "account_id": runtime.account_db_id,
                "code": (decision.rules_triggered or ["D-MODEL-RISK"])[0],
                "timestamp": timestamp,
                "decision": decision.decision,
                "transaction_id": transaction_id,
                "amount_minor": transaction.amount_minor,
            }
        )

    device_accounts.setdefault(transaction.device_id, set()).add(runtime.account_db_id)
    if counterparty_db_id:
        runtime.beneficiaries.append((timestamp, counterparty_db_id))

    return decision


def _open_incidents(
    session: Session,
    run_id: str,
    plan: ScenarioPlan,
    runtimes: dict[int, _AccountRuntime],
    config: str,
) -> list[str]:
    """Create one incident per account that showed suspicious activity.

    An incident is opened for two independent reasons, because they are different
    observations:

    * the defense acted on a transaction (blocked or held it);
    * security telemetry reached HIGH severity even where no defense acted.

    The second case is why the "no automated defense" configuration still produces
    incidents: the bank's monitoring noticed the foreign login, it simply had nothing
    configured to stop it. That distinction is the point of the whole comparison.
    """
    incident_ids: list[str] = []
    for runtime in runtimes.values():
        suspicious_events = [
            event for event in runtime.security_events if event.severity in ALERT_SEVERITIES
        ]
        if not runtime.alerts and not suspicious_events:
            continue

        opened_at = min(
            [alert["timestamp"] for alert in runtime.alerts]
            + [event.timestamp for event in suspicious_events]
        )
        severity = runtime.max_severity
        if runtime.blocked:
            severity = "CRITICAL"
        elif runtime.flagged and _severity_rank(severity) < 3:
            severity = "HIGH"

        risk_level = "HIGH" if runtime.blocked else ("MEDIUM" if runtime.flagged else "LOW")
        incident_id = new_id("INC")

        # A burst of identical detections is one incident, not forty. Repeats of the
        # same (account, rule) inside the window are folded into the first occurrence
        # and only counted, so the incident queue stays readable.
        alerts = dedupe_alerts(runtime.alerts)
        suppressed = sum(int(alert["duplicates"]) for alert in alerts)
        summary = (
            f"{len(suspicious_events)} security event(s) and {runtime.detections} scored "
            f"transaction(s); the defense blocked {runtime.blocked} and held {runtime.flagged}."
        )
        if suppressed:
            summary += f" {suppressed} repeated alert(s) for the same rule were folded into this incident."

        repo.incidents.add_incident(
            session,
            {
                "id": incident_id,
                "run_id": run_id,
                "scenario": plan.scenario,
                "primary_account_id": runtime.account_db_id,
                "title": f"{catalog.scenario_name(plan.scenario)} observed on {_label(plan, runtime.index)}",
                "summary": summary,
                "severity": severity,
                "risk_level": risk_level,
                "status": "OPEN",
                "opened_at": opened_at,
            },
        )
        _write_timeline(session, incident_id, runtime)

        for detection in repo.detections.detections_for_account(session, runtime.account_db_id, limit=500):
            if detection.run_id == run_id and detection.decision != "ALLOW":
                detection.incident_id = incident_id
        _attach_defense_actions(session, incident_id, runtime)
        if runtime.blocked:
            repo.incidents.add_defense_action(
                session,
                {
                    "id": new_id("DEF"),
                    "run_id": run_id,
                    "incident_id": incident_id,
                    "account_id": runtime.account_db_id,
                    "action_type": "SUSPEND_ACCOUNT",
                    "timestamp": opened_at,
                    "reason": "Repeated blocked transactions on this account during the scenario.",
                    "amount_minor": 0,
                },
            )
            account = session.get(Account, runtime.account_db_id)
            if account is not None:
                repo.accounts.set_risk_status(session, account, "FLAGGED_FOR_INVESTIGATION")
        else:
            account = session.get(Account, runtime.account_db_id)
            if account is not None:
                repo.accounts.set_risk_status(session, account, "ELEVATED_SIMULATED_RISK")

        incident_ids.append(incident_id)

    session.commit()
    if not incident_ids:
        logger.info(
            "Run %s (%s, %s) produced no incidents: no transaction was flagged and no "
            "security event reached HIGH severity.",
            run_id,
            plan.scenario,
            config,
        )
    return incident_ids


def _label(plan: ScenarioPlan, index: int) -> str:
    return plan.account_by_index(index).label


def _write_timeline(session: Session, incident_id: str, runtime: _AccountRuntime) -> None:
    """Persist the account's timeline in ``seq`` order (the order replay will read)."""
    ordered = sorted(runtime.timeline, key=lambda entry: (entry["timestamp"], entry["kind"]))
    rows = []
    for sequence, entry in enumerate(ordered, start=1):
        rows.append(
            {
                "id": new_id("IEV"),
                "incident_id": incident_id,
                "seq": sequence,
                "timestamp": entry["timestamp"],
                "kind": entry["kind"],
                "ref_id": entry["ref_id"],
                "description": entry["description"][:240],
                "payload": entry["payload"],
            }
        )
    repo.incidents.add_timeline(session, rows)


def _attach_defense_actions(session: Session, incident_id: str, runtime: _AccountRuntime) -> None:
    """Link this run's unlinked defense actions for the account to its incident."""
    if not runtime.alerts:
        return
    actions = session.execute(
        select(DefenseAction).where(
            DefenseAction.account_id == runtime.account_db_id,
            DefenseAction.incident_id.is_(None),
        )
    ).scalars()
    for action in actions:
        action.incident_id = incident_id


def _compute_run_impact(session: Session, run_id: str, incident_ids: list[str]) -> ImpactBreakdown:
    """Compute and persist the financial impact for the run and for each incident.

    The three buckets are built from the ledger's response status and the scenario's
    synthetic label, so a blocked transaction can never be counted as a loss.
    """
    transactions = repo.transactions.transactions_for_run(session, run_id)

    def buckets(rows) -> dict[str, int]:
        stopped = ("BLOCKED", "PENDING_REVIEW")
        return {
            "exposure_minor": sum(row.amount_minor for row in rows),
            "unauthorized_minor": sum(
                row.amount_minor for row in rows if row.status == "SETTLED" and row.is_fraud == 1
            ),
            "prevented_minor": sum(
                row.amount_minor for row in rows if row.status in stopped and row.is_fraud == 1
            ),
            "interrupted_minor": sum(
                row.amount_minor for row in rows if row.status in stopped and row.is_fraud == 0
            ),
        }

    for incident_id in incident_ids:
        incident_transactions = repo.incidents.transactions_for_incident(session, incident_id)
        incident_impact = compute_impact(**buckets(incident_transactions), incident_count=1)
        repo.governance.add_financial_impact(
            session,
            {
                "id": new_id("FIN"),
                "run_id": run_id,
                "incident_id": incident_id,
                **incident_impact.column_values(),
            },
        )

    impact = compute_impact(**buckets(transactions), incident_count=len(incident_ids))
    repo.governance.add_financial_impact(
        session,
        {"id": new_id("FIN"), "run_id": run_id, "incident_id": None, **impact.column_values()},
    )
    return impact


def _build_summary(
    session: Session,
    run_id: str,
    plan: ScenarioPlan,
    runtimes: dict[int, _AccountRuntime],
    config: str,
    incident_ids: list[str],
    impact: ImpactBreakdown,
    notes: list[str],
) -> dict:
    """Assemble the run summary. Every number here is read back from persisted rows."""
    transactions = repo.transactions.transactions_for_run(session, run_id)
    detections = repo.detections.detections_for_run(session, run_id)
    decision_by_transaction = {row.transaction_id: row.decision for row in detections if row.transaction_id}

    decisions = {"ALLOW": 0, "FLAG": 0, "BLOCK": 0}
    true_positive = false_positive = false_negative = true_negative = 0
    unlabelled = 0
    for txn in transactions:
        decision = decision_by_transaction.get(txn.id, "ALLOW")
        decisions[decision] = decisions.get(decision, 0) + 1
        detected = decision != "ALLOW"
        if txn.is_fraud is None:
            # Unlabelled rows are excluded from the confusion matrix rather than
            # silently counted as legitimate.
            unlabelled += 1
        elif txn.is_fraud == 1:
            if detected:
                true_positive += 1
            else:
                false_negative += 1
        elif detected:
            false_positive += 1
        else:
            true_negative += 1

    blocked = sum(runtime.blocked for runtime in runtimes.values())
    flagged = sum(runtime.flagged for runtime in runtimes.values())
    latencies = [float(row.latency_ms) for row in detections if row.latency_ms is not None]

    return {
        "scenario_name": catalog.scenario_name(plan.scenario),
        "defense_config": config,
        "fingerprint": plan.fingerprint,
        "accounts": len(plan.accounts),
        "transactions": len(transactions),
        "attack_transactions": len(plan.attack_transactions),
        "cover_transactions": len(plan.cover_transactions),
        "security_events": len(plan.security_events),
        "unlabelled_transactions": unlabelled,
        "decisions": decisions,
        "detections": {
            "blocked": blocked,
            "flagged": flagged,
            "detected": blocked + flagged,
            "true_positives": true_positive,
            "false_positives": false_positive,
            "false_negatives": false_negative,
            "true_negatives": true_negative,
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "false_positive_rate": _ratio(false_positive, false_positive + true_negative),
        },
        "incidents": len(incident_ids),
        "incident_ids": list(incident_ids),
        "exposure_value": minor_units_to_float(impact.exposure_minor),
        "unauthorized_value": minor_units_to_float(impact.unauthorized_minor),
        "prevented_value": minor_units_to_float(impact.prevented_minor),
        "interrupted_value": minor_units_to_float(impact.interrupted_minor),
        "disruption_cost": minor_units_to_float(impact.disruption_cost_minor),
        "recovery_cost": minor_units_to_float(impact.recovery_cost_minor),
        "net_impact": minor_units_to_float(impact.net_impact_minor),
        "detection_latency": {
            "samples": len(latencies),
            "mean_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "max_ms": round(max(latencies), 3) if latencies else None,
        },
        "notes": notes,
    }
