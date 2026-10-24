"""Read/write helpers for simulation runs and replays."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DefenseAction,
    DetectionResult,
    FinancialImpact,
    Incident,
    IncidentEvent,
    ReplayRun,
    SecurityEvent,
    SimulationRun,
    Transaction,
)
from app.utils.timeutils import utcnow


def create_run(
    session: Session,
    *,
    run_id: str,
    scenario: str,
    defense_config: str,
    seed: int,
    requested_events: int,
    mode: str,
    params: dict,
) -> SimulationRun:
    run = SimulationRun(
        id=run_id,
        scenario=scenario,
        defense_config=defense_config,
        seed=seed,
        requested_events=requested_events,
        mode=mode,
        params=params,
        status="PENDING",
    )
    session.add(run)
    session.commit()
    return run


def get_run(session: Session, run_id: str) -> SimulationRun | None:
    return session.get(SimulationRun, run_id)


def list_runs(
    session: Session,
    *,
    limit: int,
    offset: int,
    scenario: str | None = None,
) -> tuple[list[SimulationRun], int]:
    statement = select(SimulationRun)
    counter = select(func.count()).select_from(SimulationRun)
    if scenario:
        statement = statement.where(SimulationRun.scenario == scenario)
        counter = counter.where(SimulationRun.scenario == scenario)
    total = int(session.execute(counter).scalar() or 0)
    rows = list(
        session.execute(
            statement.order_by(SimulationRun.created_at.desc(), SimulationRun.id).limit(limit).offset(offset)
        ).scalars()
    )
    return rows, total


def mark_started(session: Session, run: SimulationRun) -> None:
    run.status = "RUNNING"
    run.started_at = utcnow()
    session.commit()


def mark_finished(
    session: Session,
    run: SimulationRun,
    *,
    status: str,
    generated_events: int,
    summary: dict,
    error: str | None = None,
) -> None:
    run.status = status
    run.generated_events = generated_events
    run.summary = summary
    run.error = error
    run.finished_at = utcnow()
    session.commit()


def set_run_param(session: Session, run: SimulationRun, key: str, value: object) -> None:
    """Merge one key into the run's params JSON without clobbering the rest."""
    params = dict(run.params or {})
    params[key] = value
    run.params = params
    session.commit()


def count_active_runs(session: Session) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(SimulationRun).where(
                SimulationRun.status.in_(("PENDING", "RUNNING"))
            )
        ).scalar()
        or 0
    )


def status_counts(session: Session) -> dict[str, int]:
    """Run count per status, for the Overview run counters."""
    rows = session.execute(
        select(SimulationRun.status, func.count()).group_by(SimulationRun.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def delete_run(session: Session, run: SimulationRun) -> None:
    """Remove a run and everything derived from it (reset / replay support).

    Deletion order matters because SQLite enforces the foreign keys declared in
    ``app/db/models.py``.
    """
    run_id = run.id
    session.query(IncidentEvent).filter(
        IncidentEvent.incident_id.in_(select(Incident.id).where(Incident.run_id == run_id))
    ).delete(synchronize_session=False)
    for model in (DefenseAction, FinancialImpact, DetectionResult, SecurityEvent):
        session.query(model).filter(model.run_id == run_id).delete(synchronize_session=False)
    session.query(ReplayRun).filter(
        ReplayRun.incident_id.in_(select(Incident.id).where(Incident.run_id == run_id))
    ).delete(synchronize_session=False)
    session.query(Incident).filter(Incident.run_id == run_id).delete(synchronize_session=False)
    session.query(Transaction).filter(Transaction.run_id == run_id).delete(synchronize_session=False)
    session.query(SimulationRun).filter(SimulationRun.id == run_id).delete(synchronize_session=False)
    session.commit()


def latest_finished_at(session: Session) -> datetime | None:
    return session.execute(select(func.max(SimulationRun.finished_at))).scalar()
