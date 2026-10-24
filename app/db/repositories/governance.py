"""Read/write helpers for governance data: model versions, audit trail,
financial-impact rows and saved defense comparisons."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, DefenseComparisonRecord, FinancialImpact, ModelVersion


def add_financial_impact(session: Session, row: dict) -> FinancialImpact:
    impact = FinancialImpact(**row)
    session.add(impact)
    session.commit()
    return impact


def financial_impact_for_run(session: Session, run_id: str) -> FinancialImpact | None:
    return session.execute(
        select(FinancialImpact)
        .where(FinancialImpact.run_id == run_id, FinancialImpact.incident_id.is_(None))
        .order_by(FinancialImpact.created_at.desc())
    ).scalars().first()


def financial_impact_for_incident(session: Session, incident_id: str) -> FinancialImpact | None:
    return session.execute(
        select(FinancialImpact)
        .where(FinancialImpact.incident_id == incident_id)
        .order_by(FinancialImpact.created_at.desc())
    ).scalars().first()


def sum_financial_impacts(session: Session, run_id: str | None = None) -> dict[str, int]:
    statement = select(
        func.coalesce(func.sum(FinancialImpact.exposure_minor), 0),
        func.coalesce(func.sum(FinancialImpact.unauthorized_minor), 0),
        func.coalesce(func.sum(FinancialImpact.prevented_minor), 0),
        func.coalesce(func.sum(FinancialImpact.interrupted_minor), 0),
        func.coalesce(func.sum(FinancialImpact.disruption_cost_minor), 0),
        func.coalesce(func.sum(FinancialImpact.recovery_cost_minor), 0),
        func.coalesce(func.sum(FinancialImpact.net_impact_minor), 0),
    ).where(FinancialImpact.incident_id.is_(None))
    if run_id:
        statement = statement.where(FinancialImpact.run_id == run_id)
    row = session.execute(statement).one()
    keys = (
        "exposure_minor",
        "unauthorized_minor",
        "prevented_minor",
        "interrupted_minor",
        "disruption_cost_minor",
        "recovery_cost_minor",
        "net_impact_minor",
    )
    return {key: int(value or 0) for key, value in zip(keys, row)}


def add_comparison(session: Session, row: dict) -> DefenseComparisonRecord:
    record = DefenseComparisonRecord(**row)
    session.add(record)
    session.commit()
    return record


def get_comparison(session: Session, comparison_id: str) -> DefenseComparisonRecord | None:
    return session.get(DefenseComparisonRecord, comparison_id)


def list_comparisons(session: Session, *, limit: int) -> list[DefenseComparisonRecord]:
    return list(
        session.execute(
            select(DefenseComparisonRecord)
            .order_by(DefenseComparisonRecord.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def register_model_versions(session: Session, rows: list[dict]) -> None:
    session.query(ModelVersion).filter(ModelVersion.is_active.is_(True)).update({"is_active": False})
    session.add_all(ModelVersion(**row) for row in rows)
    session.commit()


def list_model_versions(session: Session, limit: int = 10) -> list[ModelVersion]:
    return list(
        session.execute(
            select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(limit)
        ).scalars()
    )


def list_audit(
    session: Session,
    *,
    limit: int,
    offset: int,
    action: str | None = None,
) -> tuple[list[AuditLog], int]:
    statement = select(AuditLog)
    counter = select(func.count()).select_from(AuditLog)
    if action:
        statement = statement.where(AuditLog.action == action)
        counter = counter.where(AuditLog.action == action)
    total = int(session.execute(counter).scalar() or 0)
    rows = list(
        session.execute(
            statement.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    return rows, total
