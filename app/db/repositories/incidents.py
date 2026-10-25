"""Read/write helpers for incidents, their timelines and defensive actions."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    DefenseAction,
    DetectionResult,
    Incident,
    IncidentEvent,
    Transaction,
)


def add_incident(session: Session, row: dict) -> Incident:
    incident = Incident(**row)
    session.add(incident)
    session.flush()
    return incident


def get_incident(session: Session, incident_id: str) -> Incident | None:
    return session.get(Incident, incident_id)


def incident_with_label(session: Session, incident_id: str) -> tuple[Incident, str | None] | None:
    row = session.execute(
        select(Incident, Account.label)
        .join(Account, Account.id == Incident.primary_account_id, isouter=True)
        .where(Incident.id == incident_id)
    ).first()
    return (row[0], row[1]) if row else None


def list_incidents(
    session: Session,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    scenario: str | None = None,
) -> tuple[list[tuple[Incident, str | None]], int]:
    statement = select(Incident, Account.label).join(
        Account, Account.id == Incident.primary_account_id, isouter=True
    )
    counter = select(func.count()).select_from(Incident)

    conditions = []
    if status:
        conditions.append(Incident.status == status)
    if scenario:
        conditions.append(Incident.scenario == scenario)
    for condition in conditions:
        statement = statement.where(condition)
        counter = counter.where(condition)

    total = int(session.execute(counter).scalar() or 0)
    rows = session.execute(
        statement.order_by(Incident.opened_at.desc(), Incident.id).limit(limit).offset(offset)
    ).all()
    return [(row[0], row[1]) for row in rows], total


def incidents_for_account(session: Session, account_id: str, limit: int = 20) -> list[Incident]:
    return list(
        session.execute(
            select(Incident)
            .where(Incident.primary_account_id == account_id)
            .order_by(Incident.opened_at.desc())
            .limit(limit)
        ).scalars()
    )


def incidents_for_run(session: Session, run_id: str) -> list[Incident]:
    return list(
        session.execute(
            select(Incident).where(Incident.run_id == run_id).order_by(Incident.opened_at, Incident.id)
        ).scalars()
    )


def recent_incidents(session: Session, limit: int = 6) -> list[tuple[Incident, str | None]]:
    rows = session.execute(
        select(Incident, Account.label)
        .join(Account, Account.id == Incident.primary_account_id, isouter=True)
        .order_by(Incident.opened_at.desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def add_timeline(session: Session, rows: list[dict]) -> None:
    if not rows:
        return
    session.add_all(IncidentEvent(**row) for row in rows)
    session.flush()


def timeline_for_incident(session: Session, incident_id: str) -> list[IncidentEvent]:
    return list(
        session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.seq)
        ).scalars()
    )


def next_sequence(session: Session, incident_id: str) -> int:
    current = session.execute(
        select(func.max(IncidentEvent.seq)).where(IncidentEvent.incident_id == incident_id)
    ).scalar()
    return int(current or 0) + 1


def transactions_for_incident(session: Session, incident_id: str) -> list[Transaction]:
    return list(
        session.execute(
            select(Transaction)
            .join(DetectionResult, DetectionResult.transaction_id == Transaction.id)
            .where(DetectionResult.incident_id == incident_id)
            .order_by(Transaction.timestamp, Transaction.id)
            .distinct()
        ).scalars()
    )


def add_defense_action(session: Session, row: dict) -> DefenseAction:
    action = DefenseAction(**row)
    session.add(action)
    session.flush()
    return action


def defense_actions_for_incident(session: Session, incident_id: str) -> list[DefenseAction]:
    return list(
        session.execute(
            select(DefenseAction)
            .where(DefenseAction.incident_id == incident_id)
            .order_by(DefenseAction.timestamp, DefenseAction.id)
        ).scalars()
    )


def incident_metrics(session: Session, incident_id: str) -> dict:
    """Counts and value attached to one incident, used by the list view."""
    event_count = int(
        session.execute(
            select(func.count()).select_from(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
        ).scalar()
        or 0
    )
    row = session.execute(
        select(func.count(func.distinct(Transaction.id)), func.coalesce(func.sum(Transaction.amount_minor), 0))
        .join(DetectionResult, DetectionResult.transaction_id == Transaction.id)
        .where(DetectionResult.incident_id == incident_id, Transaction.status != "BLOCKED")
    ).one()
    return {
        "event_count": event_count,
        "transaction_count": int(row[0] or 0),
        "suspected_loss_minor": int(row[1] or 0),
    }


def incident_metrics_bulk(session: Session, incident_ids: list[str]) -> dict[str, dict]:
    """Batch version of :func:`incident_metrics` so a list page stays at 2 queries."""
    if not incident_ids:
        return {}

    counts: dict[str, dict] = {
        incident_id: {"event_count": 0, "transaction_count": 0, "suspected_loss_minor": 0}
        for incident_id in incident_ids
    }

    for incident_id, count in session.execute(
        select(IncidentEvent.incident_id, func.count())
        .where(IncidentEvent.incident_id.in_(incident_ids))
        .group_by(IncidentEvent.incident_id)
    ).all():
        counts[incident_id]["event_count"] = int(count)

    for incident_id, transactions, amount in session.execute(
        select(
            DetectionResult.incident_id,
            func.count(func.distinct(Transaction.id)),
            func.coalesce(func.sum(Transaction.amount_minor), 0),
        )
        .join(Transaction, Transaction.id == DetectionResult.transaction_id)
        .where(DetectionResult.incident_id.in_(incident_ids), Transaction.status != "BLOCKED")
        .group_by(DetectionResult.incident_id)
    ).all():
        counts[incident_id]["transaction_count"] = int(transactions)
        counts[incident_id]["suspected_loss_minor"] = int(amount)

    return counts


def status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Incident.status, func.count()).group_by(Incident.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


# ---------------------------------------------------------------------------
# Customer-scoped reads
# ---------------------------------------------------------------------------
def incidents_for_accounts(
    session: Session, account_ids: list[str], *, limit: int = 20, status: str | None = None
) -> list[tuple[Incident, str | None]]:
    """Incidents raised on any of the customer's own accounts."""
    if not account_ids:
        return []
    statement = (
        select(Incident, Account.label)
        .join(Account, Account.id == Incident.primary_account_id, isouter=True)
        .where(Incident.primary_account_id.in_(list(account_ids)))
    )
    if status:
        statement = statement.where(Incident.status == status)
    rows = session.execute(
        statement.order_by(Incident.opened_at.desc(), Incident.id).limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def open_incident_counts(session: Session, account_ids: list[str]) -> int:
    if not account_ids:
        return 0
    return int(
        session.execute(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.primary_account_id.in_(list(account_ids)),
                Incident.status != "CLOSED",
            )
        ).scalar()
        or 0
    )
