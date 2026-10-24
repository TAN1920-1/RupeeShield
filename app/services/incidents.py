"""Incident investigation and read-only replay.

Two responsibilities:

**Assembly.** Turning persisted incident rows into the payloads the API returns.
The list view needs per-incident counts and a suspected-loss figure, which is why
:func:`list_incident_page` uses the *bulk* metrics query: a page of 25 incidents
costs two SQL statements, not fifty.

**Replay.** Recomputing the decisions an incident produced, from the scenario
script, and reporting where the recomputed decision differs from the recorded one.
Replay is deliberately read-only:

* it never writes a transaction, detection or defense action;
* it never edits the incident timeline;
* the only row it writes is a :class:`~app.db.models.ReplayRun` record of the
  replay itself, which is why ``mutates_history`` is always ``False``.

Divergence is expected to be zero. A non-zero count means the thresholds or the
model artefacts changed after the incident was recorded, which is exactly the kind
of thing an investigator needs to be told rather than have hidden.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Incident, IncidentEvent, ReplayRun
from app.ml.features import UserStateStore
from app.security.audit import record_audit
from app.services.correlation import SecurityEventView, TransactionView, correlate
from app.services.detection import evaluate_transaction
from app.services.financial_impact import impact_row_to_dict
from app.services.simulation import catalog
from app.services.simulation.accounts import account_id
from app.services.simulation.engine import chronological_queue, warm_feature_store
from app.services.simulation.scenarios import build_plan
from app.utils.ids import new_id
from app.utils.money import minor_units_to_float
from app.utils.timeutils import iso_utc, minutes_between, utcnow

logger = get_logger(__name__)

INCIDENT_STATUSES = ["OPEN", "INVESTIGATING", "CONTAINED", "CLOSED"]
OPEN_STATUSES = ("OPEN", "INVESTIGATING")

#: A new beneficiary seen inside this window counts as "recent" for the fan-out rule.
NEW_BENEFICIARY_WINDOW_MINUTES = 30.0


class IncidentNotFound(LookupError):
    """Raised when an incident id does not exist (mapped to HTTP 404)."""


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------
def shape_incident(row: Incident, label: str | None, metrics: dict | None = None) -> dict:
    """Shape one ``Incident`` row into the ``IncidentOut`` payload."""
    counts = metrics or {"event_count": 0, "transaction_count": 0, "suspected_loss_minor": 0}
    suspected_minor = int(counts.get("suspected_loss_minor", 0))
    return {
        "id": row.id,
        "run_id": row.run_id,
        "scenario": row.scenario,
        "scenario_name": catalog.scenario_name(row.scenario),
        "primary_account_id": row.primary_account_id,
        "primary_account_label": label,
        "title": row.title,
        "summary": row.summary or "",
        "severity": row.severity,
        "risk_level": row.risk_level,
        "status": row.status,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
        "event_count": int(counts.get("event_count", 0)),
        "transaction_count": int(counts.get("transaction_count", 0)),
        "suspected_loss_minor": suspected_minor,
        "suspected_loss": minor_units_to_float(suspected_minor),
    }


def shape_timeline(entry: IncidentEvent) -> dict:
    return {
        "id": entry.id,
        "seq": int(entry.seq),
        "timestamp": entry.timestamp,
        "kind": entry.kind,
        "ref_id": entry.ref_id,
        "description": entry.description,
        "payload": dict(entry.payload or {}),
    }


def shape_transaction(row, detection=None) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp,
        "amount_minor": int(row.amount_minor),
        "amount": minor_units_to_float(row.amount_minor),
        "direction": row.direction,
        "channel": row.channel,
        "country": row.country,
        "device_id": row.device_id,
        "merchant_id": row.merchant_id,
        "counterparty_id": row.counterparty_id,
        "counterparty_label": row.counterparty_label,
        "status": row.status,
        "is_fraud": row.is_fraud,
        "decision": None if detection is None else detection.decision,
        "risk_score": None if detection is None else round(float(detection.fraud_risk_score), 4),
    }


def shape_detection(row) -> dict:
    return {
        "id": row.id,
        "transaction_id": row.transaction_id,
        "timestamp": row.timestamp,
        "detection_source": row.detection_source,
        "fraud_risk_score": round(float(row.fraud_risk_score), 4),
        "decision": row.decision,
        "ml_supervised_score": row.ml_supervised_score,
        "ml_anomaly_score": row.ml_anomaly_score,
        "rule_heuristic_score": row.rule_heuristic_score,
        "correlation_score": row.correlation_score,
        "reasons": list(row.reasons or []),
        "rules_triggered": list(row.rules_triggered or []),
        "correlated_event_ids": list(row.correlated_event_ids or []),
        "latency_ms": round(float(row.latency_ms or 0.0), 3),
    }


def shape_defense_action(row) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "incident_id": row.incident_id,
        "account_id": row.account_id,
        "transaction_id": row.transaction_id,
        "action_type": row.action_type,
        "timestamp": row.timestamp,
        "reason": row.reason or "",
        "amount_minor": int(row.amount_minor or 0),
        "amount": minor_units_to_float(row.amount_minor or 0),
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_incident_page(
    session: Session,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    scenario: str | None = None,
) -> dict:
    rows, total = repo.incidents.list_incidents(
        session, limit=limit, offset=offset, status=status, scenario=scenario
    )
    metrics = repo.incidents.incident_metrics_bulk(session, [row.id for row, _ in rows])
    items = [shape_incident(row, label, metrics.get(row.id)) for row, label in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "returned": len(items)}


def incident_detail(session: Session, incident_id: str) -> dict:
    """Full investigation payload for one incident.

    Raises :class:`IncidentNotFound` rather than returning an empty shell, so the
    API can answer 404 instead of pretending the incident exists and is empty.
    """
    found = repo.incidents.incident_with_label(session, incident_id)
    if found is None:
        raise IncidentNotFound(f"No incident with id {incident_id}.")
    row, label = found

    metrics = repo.incidents.incident_metrics_bulk(session, [incident_id]).get(incident_id)
    payload = shape_incident(row, label, metrics)
    payload["notes"] = row.notes or ""
    payload["timeline"] = [shape_timeline(entry) for entry in repo.incidents.timeline_for_incident(session, incident_id)]

    detections = repo.detections.detections_for_incident(session, incident_id)
    detection_by_transaction = {row.transaction_id: row for row in detections if row.transaction_id}
    transactions = repo.incidents.transactions_for_incident(session, incident_id)
    payload["transactions"] = [
        shape_transaction(transaction, detection_by_transaction.get(transaction.id))
        for transaction in transactions
    ]
    payload["detections"] = [shape_detection(row) for row in detections]
    payload["defense_actions"] = [
        shape_defense_action(action) for action in repo.incidents.defense_actions_for_incident(session, incident_id)
    ]

    impact = repo.governance.financial_impact_for_incident(session, incident_id)
    payload["financial_impact"] = impact_row_to_dict(impact) if impact else None
    payload["triggered_rules"] = repo.detections.rules_for_incident(session, incident_id)
    return payload


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def change_status(session: Session, incident_id: str, status: str) -> dict:
    """Move an incident through its lifecycle and record who did it."""
    row = repo.incidents.get_incident(session, incident_id)
    if row is None:
        raise IncidentNotFound(f"No incident with id {incident_id}.")

    previous = row.status
    row.status = status
    row.closed_at = utcnow() if status == "CLOSED" else None
    session.add(row)
    session.commit()

    record_audit(
        session,
        action="INCIDENT_STATUS_CHANGED",
        entity_type="incident",
        entity_id=incident_id,
        details={"from": previous, "to": status},
    )
    return incident_detail(session, incident_id)


def add_note(session: Session, incident_id: str, note: str) -> dict:
    """Append an investigator note. Notes are appended, never rewritten."""
    row = repo.incidents.get_incident(session, incident_id)
    if row is None:
        raise IncidentNotFound(f"No incident with id {incident_id}.")

    stamp = iso_utc(utcnow())
    entry = f"[{stamp}] {note}"
    row.notes = f"{row.notes}\n{entry}".strip() if row.notes else entry
    session.add(row)
    session.commit()

    record_audit(
        session,
        action="INCIDENT_NOTE_ADDED",
        entity_type="incident",
        entity_id=incident_id,
        details={"length": len(note)},
    )
    return incident_detail(session, incident_id)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def _recompute(session: Session, row: Incident, run) -> tuple[list[dict], list[str]]:
    """Replay the scenario and return the recomputed decisions for this account.

    Returns ``(recomputed, notes)`` where ``recomputed`` is a chronological list of
    ``{"decision", "risk_score", "kind"}`` entries for the incident's primary
    account. Nothing is written to the ledger.
    """
    notes: list[str] = []
    plan = build_plan(run.scenario, seed=run.seed, n_events=run.requested_events)
    recorded_fingerprint = (run.summary or {}).get("fingerprint")
    if recorded_fingerprint and recorded_fingerprint != plan.fingerprint:
        notes.append(
            "The scenario script rebuilt from (scenario, seed, n_events) has fingerprint "
            f"{plan.fingerprint} but the recorded run had {recorded_fingerprint}. The scenario "
            "builder changed since this run, so a divergence is expected."
        )
    else:
        notes.append(f"Scenario script rebuilt with fingerprint {plan.fingerprint}.")

    # The engine's first timestamp is derived the same way for every run: the run's
    # own start time when it started, or the earliest recorded event if it did not.
    start_time = run.started_at or _earliest_event_time(session, row)

    index_by_db_id = {
        account_id(run.id, account.index): account.index for account in plan.accounts
    }
    primary_index = index_by_db_id.get(row.primary_account_id)
    if primary_index is None:
        raise IncidentNotFound(
            "The incident's primary account is not part of the rebuilt scenario script, "
            "so it cannot be replayed."
        )

    store: UserStateStore = warm_feature_store(plan, start_time, run_id=run.id)
    config = (run.defense_config or "COMBINED").upper()

    device_accounts: dict[str, set[str]] = {}
    security_views: dict[int, list[SecurityEventView]] = defaultdict(list)
    beneficiaries: dict[int, list[tuple[datetime, str]]] = defaultdict(list)
    recomputed: list[dict] = []

    for offset, kind, item in chronological_queue(plan):
        timestamp = start_time + timedelta(seconds=offset)
        account = plan.account_by_index(item.account_index)
        db_id = account_id(run.id, item.account_index)

        if kind == 0:
            security_views[item.account_index].append(
                SecurityEventView(
                    id=item.key,
                    account_id=db_id,
                    timestamp=timestamp,
                    event_type=item.event_type,
                    severity=item.severity,
                    country=item.country,
                    device_id=item.device_id,
                    details=dict(item.details),
                )
            )
            if item.account_index == primary_index:
                recomputed.append(
                    {
                        "kind": "SECURITY_EVENT",
                        "description": f"{item.event_type} ({item.severity})",
                        "decision": None,
                        "risk_score": None,
                    }
                )
            continue

        features = store.compute_features(
            user_id=db_id,
            amount=float(minor_units_to_float(item.amount_minor)),
            ts=timestamp,
            channel=item.channel,
            country=item.country,
            device_id=item.device_id,
        )

        if item.account_index == primary_index:
            correlation = None
            if config == "COMBINED":
                counterparty = (
                    account_id(run.id, item.counterparty_index)
                    if item.counterparty_index is not None
                    else None
                )
                correlation = correlate(
                    TransactionView(
                        account_id=db_id,
                        timestamp=timestamp,
                        amount_minor=item.amount_minor,
                        amount_over_user_avg=float(features["amount_over_user_avg"]),
                        counterparty_id=counterparty,
                    ),
                    security_views[item.account_index],
                    shared_device_accounts={
                        other for other in device_accounts.get(item.device_id, set()) if other != db_id
                    },
                    new_beneficiary_ids={
                        seen for seen, _ in beneficiaries[item.account_index]
                        if minutes_between(seen, timestamp) <= NEW_BENEFICIARY_WINDOW_MINUTES
                    },
                )
            decision = evaluate_transaction(features, defense_config=config, correlation=correlation)
            recomputed.append(
                {
                    "kind": "TRANSACTION",
                    "decision": decision.decision,
                    "risk_score": round(float(decision.fraud_risk_score), 4),
                    "description": f"{item.direction} {item.amount_minor / 100:,.2f} INR on {item.channel}",
                }
            )

        device_accounts.setdefault(item.device_id, set()).add(db_id)
        if item.counterparty_index is not None:
            beneficiaries[item.account_index].append((timestamp, account_id(run.id, item.counterparty_index)))

    return recomputed, notes


def _earliest_event_time(session: Session, row: Incident) -> datetime:
    """Fallback reference time for a run that never recorded ``started_at``."""
    if row.run_id:
        events_for_run = repo.detections.security_events_for_run(session, row.run_id)
        transactions = repo.transactions.transactions_for_run(session, row.run_id)
        candidates = [event.timestamp for event in events_for_run] + [txn.timestamp for txn in transactions]
        if candidates:
            return min(candidates)
    return row.opened_at


def replay_incident(session: Session, incident_id: str) -> dict:
    """Recompute an incident's decisions and report any divergence.

    Read-only with respect to the incident: the timeline, the ledger and the
    detection rows are all left exactly as they were.
    """
    row = repo.incidents.get_incident(session, incident_id)
    if row is None:
        raise IncidentNotFound(f"No incident with id {incident_id}.")

    run = repo.runs.get_run(session, row.run_id) if row.run_id else None
    if run is None:
        raise IncidentNotFound(
            f"Incident {incident_id} has no simulation run attached, so there is no script to replay."
        )

    started_at = utcnow()
    recomputed, notes = _recompute(session, row, run)
    finished_at = utcnow()

    timeline = repo.incidents.timeline_for_incident(session, incident_id)

    # Pair the recomputed detections with the recorded DETECTION entries in time
    # order. Both lists come from the same script, so they line up positionally;
    # any surplus on either side is reported rather than silently dropped.
    recomputed_detections = [entry for entry in recomputed if entry["kind"] == "TRANSACTION"]
    recorded_detections = [entry for entry in timeline if entry.kind == "DETECTION"]

    divergence = 0
    steps: list[dict] = []
    detection_position = 0
    for entry in timeline:
        step = {
            "seq": int(entry.seq),
            "timestamp": entry.timestamp,
            "kind": entry.kind,
            "description": entry.description,
            "decision": None,
            "risk_score": None,
            "matches_original": True,
        }
        if entry.kind == "DETECTION" and detection_position < len(recomputed_detections):
            fresh = recomputed_detections[detection_position]
            detection_position += 1
            recorded_decision = (entry.payload or {}).get("decision")
            step["decision"] = fresh["decision"]
            step["risk_score"] = fresh["risk_score"]
            step["matches_original"] = recorded_decision == fresh["decision"]
            if not step["matches_original"]:
                divergence += 1
        steps.append(step)

    unpaired = abs(len(recomputed_detections) - len(recorded_detections))
    if unpaired:
        notes.append(
            f"{unpaired} transaction(s) could not be paired with a recorded detection entry; "
            "the recorded timeline may be incomplete."
        )
        divergence += unpaired

    summary = {
        "defense_config": run.defense_config,
        "recorded_detections": len(recorded_detections),
        "recomputed_detections": len(recomputed_detections),
        "divergence_count": divergence,
        "notes": notes,
    }
    replay = ReplayRun(
        id=new_id("RPL"),
        incident_id=incident_id,
        started_at=started_at,
        finished_at=finished_at,
        summary=summary,
    )
    session.add(replay)
    session.commit()

    record_audit(
        session,
        action="INCIDENT_REPLAYED",
        entity_type="incident",
        entity_id=incident_id,
        details={"steps": len(steps), "divergence": divergence},
    )

    return {
        "replay_id": replay.id,
        "incident_id": incident_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": steps,
        "steps_replayed": len(steps),
        "divergence_count": divergence,
        "mutates_history": False,
        "summary": summary,
    }


__all__ = [
    "INCIDENT_STATUSES",
    "OPEN_STATUSES",
    "IncidentNotFound",
    "add_note",
    "change_status",
    "incident_detail",
    "list_incident_page",
    "replay_incident",
    "shape_incident",
    "shape_timeline",
]
