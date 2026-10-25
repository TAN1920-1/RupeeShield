"""Read/write helpers for detection results and security events."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Account, DetectionResult, SecurityEvent, Transaction


def add_detection(session: Session, row: dict) -> DetectionResult:
    detection = DetectionResult(**row)
    session.add(detection)
    session.flush()
    return detection


def add_security_event(session: Session, row: dict) -> SecurityEvent:
    event = SecurityEvent(**row)
    session.add(event)
    session.flush()
    return event


def security_event_by_id(session: Session, event_id: str) -> SecurityEvent | None:
    """One security event by primary key, for callers that just wrote it.

    Needed because "the most recent event for this account" is not the same thing
    as "the event I created": simulated scenario telemetry on the same account can
    carry a later timestamp.
    """
    return session.get(SecurityEvent, event_id)


def detection_for_transaction(session: Session, transaction_id: str) -> DetectionResult | None:
    return session.execute(
        select(DetectionResult)
        .where(DetectionResult.transaction_id == transaction_id)
        .order_by(DetectionResult.timestamp.desc())
    ).scalars().first()


def detections_for_account(session: Session, account_id: str, limit: int = 50) -> list[DetectionResult]:
    return list(
        session.execute(
            select(DetectionResult)
            .where(DetectionResult.account_id == account_id)
            .order_by(DetectionResult.timestamp.desc())
            .limit(limit)
        ).scalars()
    )


def detections_for_incident(session: Session, incident_id: str) -> list[DetectionResult]:
    return list(
        session.execute(
            select(DetectionResult)
            .where(DetectionResult.incident_id == incident_id)
            .order_by(DetectionResult.timestamp, DetectionResult.id)
        ).scalars()
    )


def detections_for_run(session: Session, run_id: str) -> list[DetectionResult]:
    return list(
        session.execute(
            select(DetectionResult)
            .where(DetectionResult.run_id == run_id)
            .order_by(DetectionResult.timestamp, DetectionResult.id)
        ).scalars()
    )


def latest_detections(session: Session, limit: int = 10) -> list[tuple[DetectionResult, str | None]]:
    rows = session.execute(
        select(DetectionResult, Account.label)
        .join(Account, Account.id == DetectionResult.account_id, isouter=True)
        .order_by(DetectionResult.timestamp.desc(), DetectionResult.id.desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def security_events_for_account(session: Session, account_id: str, limit: int = 40) -> list[SecurityEvent]:
    return list(
        session.execute(
            select(SecurityEvent)
            .where(SecurityEvent.account_id == account_id)
            .order_by(SecurityEvent.timestamp.desc())
            .limit(limit)
        ).scalars()
    )


def security_events_for_run(session: Session, run_id: str) -> list[SecurityEvent]:
    return list(
        session.execute(
            select(SecurityEvent)
            .where(SecurityEvent.run_id == run_id)
            .order_by(SecurityEvent.timestamp, SecurityEvent.id)
        ).scalars()
    )


def security_events_for_transaction(session: Session, transaction_id: str) -> list[SecurityEvent]:
    """Security telemetry the simulator explicitly attached to one transaction.

    The link is the ``related_transaction_id`` column rather than a time window, so
    the association is the one the scenario script actually declared and not an
    inferred one.
    """
    return list(
        session.execute(
            select(SecurityEvent)
            .where(SecurityEvent.related_transaction_id == transaction_id)
            .order_by(SecurityEvent.timestamp, SecurityEvent.id)
        ).scalars()
    )


def latest_security_events(session: Session, limit: int = 12) -> list[tuple[SecurityEvent, str | None]]:
    rows = session.execute(
        select(SecurityEvent, Account.label)
        .join(Account, Account.id == SecurityEvent.account_id, isouter=True)
        .order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id.desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def severity_breakdown(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(SecurityEvent.severity, func.count())
        .group_by(SecurityEvent.severity)
        .order_by(func.count().desc())
    ).all()
    return [(str(severity), int(count)) for severity, count in rows]


def decision_breakdown(session: Session, detection_source: str | None = None) -> list[tuple[str, int]]:
    statement = select(DetectionResult.decision, func.count()).group_by(DetectionResult.decision)
    if detection_source:
        statement = statement.where(DetectionResult.detection_source == detection_source)
    return [(str(decision), int(count)) for decision, count in session.execute(statement).all()]


def detection_latencies(session: Session) -> list[float]:
    return [
        float(value)
        for value in session.execute(select(DetectionResult.latency_ms)).scalars()
        if value is not None
    ]


def flagged_edges(session: Session, run_id: str | None = None) -> dict[tuple[str, str], float]:
    """``(source, target) -> max risk score`` for transactions that were not allowed.

    Used by the network graph to mark an edge as suspicious. The value is a model
    observation; it is never presented as evidence of wrongdoing.
    """
    statement = (
        select(
            DetectionResult.account_id,
            Transaction.counterparty_id,
            func.max(DetectionResult.fraud_risk_score),
        )
        .join(Transaction, Transaction.id == DetectionResult.transaction_id)
        .where(DetectionResult.decision != "ALLOW", Transaction.counterparty_id.is_not(None))
        .group_by(DetectionResult.account_id, Transaction.counterparty_id)
    )
    if run_id:
        statement = statement.where(DetectionResult.run_id == run_id)
    return {(row[0], row[1]): float(row[2]) for row in session.execute(statement).all()}


def count_suspicious(session: Session, run_id: str | None = None) -> int:
    statement = select(func.count()).select_from(DetectionResult).where(DetectionResult.decision != "ALLOW")
    if run_id:
        statement = statement.where(DetectionResult.run_id == run_id)
    return int(session.execute(statement).scalar() or 0)


def rules_for_incident(session: Session, incident_id: str) -> list[str]:
    """Distinct rule / correlation codes that fired inside one incident."""
    codes: set[str] = set()
    for detection in detections_for_incident(session, incident_id):
        codes.update(detection.rules_triggered or [])
    return sorted(codes)


def security_events_for_accounts(
    session: Session, account_ids: list[str], *, limit: int = 40, event_types: list[str] | None = None
) -> list[SecurityEvent]:
    """Recent security telemetry across the customer's own accounts."""
    if not account_ids:
        return []
    statement = select(SecurityEvent).where(SecurityEvent.account_id.in_(list(account_ids)))
    if event_types:
        statement = statement.where(SecurityEvent.event_type.in_(list(event_types)))
    return list(
        session.execute(
            statement.order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id.desc()).limit(limit)
        ).scalars()
    )


def count_events_for_accounts(
    session: Session, account_ids: list[str], event_types: list[str] | None = None
) -> int:
    if not account_ids:
        return 0
    statement = (
        select(func.count())
        .select_from(SecurityEvent)
        .where(SecurityEvent.account_id.in_(list(account_ids)))
    )
    if event_types:
        statement = statement.where(SecurityEvent.event_type.in_(list(event_types)))
    return int(session.execute(statement).scalar() or 0)


def known_devices(session: Session, account_ids: list[str]) -> list[tuple[str, str | None, str | None]]:
    """Distinct ``(device_id, country, last_seen)`` seen on the customer's accounts.

    Derived from recorded telemetry rather than from a hand-written device list,
    so a device only appears here because an event actually mentioned it.
    """
    if not account_ids:
        return []
    rows = session.execute(
        select(
            SecurityEvent.device_id,
            func.max(SecurityEvent.country),
            func.max(SecurityEvent.timestamp),
        )
        .where(
            SecurityEvent.account_id.in_(list(account_ids)),
            SecurityEvent.device_id.is_not(None),
        )
        .group_by(SecurityEvent.device_id)
    ).all()
    return [(str(device), country, seen_at) for device, country, seen_at in rows]


def latest_events_for_accounts(session: Session, account_ids: list[str], limit: int = 8):
    if not account_ids:
        return []
    return session.execute(
        select(SecurityEvent, Account.label)
        .join(Account, Account.id == SecurityEvent.account_id, isouter=True)
        .where(SecurityEvent.account_id.in_(list(account_ids)))
        .order_by(SecurityEvent.timestamp.desc())
        .limit(limit)
    ).all()
