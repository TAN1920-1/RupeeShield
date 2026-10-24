"""Overview dashboard assembly.

Every number on the Overview page is computed from stored rows or from the model's
recorded training metadata. Nothing is hard-coded and nothing is estimated in the
browser.

The four kinds of number are kept apart on purpose, because conflating them is the
most common way a fraud dashboard misleads its reader:

``observed_dataset``
    statistics of the ML dataset as it was when the models were trained;
``simulation``
    counts of rows this platform generated inside the synthetic bank;
``model``
    outputs of the detector (decisions, risk scores, inference latency);
``estimate``
    figures derived from a documented formula (``app/services/financial_impact.py``).

Each metric therefore carries a :class:`~app.schemas.common.Provenance` explaining
where it came from, and an empty state is reported *as an empty state* rather than
as a zero that looks like a real measurement.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.ml.evaluate import latency_stats
from app.ml.model_store import read_metrics
from app.services.incidents import OPEN_STATUSES, shape_incident
from app.services.monitoring import shape_security_event
from app.utils.money import minor_units_to_float
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

RECENT_EVENT_LIMIT = 12
RECENT_INCIDENT_LIMIT = 6


def _provenance(source: str, detail: str, deterministic: bool | None = None) -> dict:
    return {"source": source, "detail": detail, "deterministic": deterministic}


def _observed_dataset() -> dict | None:
    """Dataset statistics recorded at training time.

    Read from ``models/metrics.json`` rather than recomputed: the models were fitted
    on that exact snapshot, so reporting a freshly recomputed summary would describe
    a different dataset than the one the scores came from.
    """
    metrics = read_metrics()
    dataset = metrics.get("dataset")
    if not dataset:
        return None
    return {
        **dataset,
        "trained_at": metrics.get("trained_at"),
        "model_version": metrics.get("version"),
        "train_rows": metrics.get("train_rows"),
        "test_rows": metrics.get("test_rows"),
        "split_strategy": metrics.get("split_strategy", ""),
        "limitations": metrics.get("limitations", []),
    }


def build_overview(session: Session) -> dict:
    totals = repo.transactions.overview_totals(session)
    impacts = repo.governance.sum_financial_impacts(session)
    incident_counts = repo.incidents.status_counts(session)
    run_counts = repo.runs.status_counts(session)

    decisions = totals.get("decisions", {})
    blocked = int(decisions.get("BLOCK", 0))
    flagged = int(decisions.get("FLAG", 0))
    allowed = int(decisions.get("ALLOW", 0))
    suspicious = blocked + flagged

    open_incidents = sum(int(incident_counts.get(status, 0)) for status in OPEN_STATUSES)
    total_incidents = sum(int(value) for value in incident_counts.values())
    active_runs = int(run_counts.get("PENDING", 0)) + int(run_counts.get("RUNNING", 0))
    finished_runs = sum(
        int(count) for status, count in run_counts.items() if status not in {"PENDING", "RUNNING"}
    )

    latency = latency_stats(repo.detections.detection_latencies(session))

    value_at_risk_minor = int(totals.get("value_at_risk_minor", 0))
    prevented_minor = int(impacts.get("prevented_minor", 0))
    realized_minor = int(impacts.get("unauthorized_minor", 0))

    stats = {
        "total_simulated_transactions": int(totals.get("transaction_count", 0)),
        "total_value_minor": int(totals.get("total_value_minor", 0)),
        "total_value": minor_units_to_float(totals.get("total_value_minor", 0)),
        "suspicious_transactions": suspicious,
        "blocked_transactions": blocked,
        "flagged_transactions": flagged,
        "open_incidents": open_incidents,
        "total_incidents": total_incidents,
        "value_at_risk_minor": value_at_risk_minor,
        "value_at_risk": minor_units_to_float(value_at_risk_minor),
        "loss_prevented_minor": prevented_minor,
        "loss_prevented": minor_units_to_float(prevented_minor),
        "realized_loss_minor": realized_minor,
        "realized_loss": minor_units_to_float(realized_minor),
        "detection_latency": latency,
        "active_runs": active_runs,
        "completed_runs": finished_runs,
        "observed_dataset": _observed_dataset(),
        "provenance": _provenance_map(
            suspicious=suspicious,
            blocked=blocked,
            flagged=flagged,
            allowed=allowed,
            prevented_minor=prevented_minor,
            realized_minor=realized_minor,
            latency=latency,
        ),
        "generated_at": utcnow(),
    }

    charts = {
        "volume_over_time": repo.transactions.volume_series(session),
        "alerts_by_severity": [
            {"key": severity, "count": count}
            for severity, count in repo.detections.severity_breakdown(session)
        ],
        "decisions_breakdown": [
            {"key": decision, "count": count}
            for decision, count in repo.detections.decision_breakdown(session)
        ],
        "scenario_breakdown": [
            {"key": scenario, "count": count}
            for scenario, count in repo.transactions.scenario_breakdown(session)
        ],
        "top_risk_accounts": repo.accounts.highest_risk_accounts(session, limit=8),
    }

    recent_events = [
        shape_security_event(row, label, sequence=index)
        for index, (row, label) in enumerate(
            repo.detections.latest_security_events(session, RECENT_EVENT_LIMIT), start=1
        )
    ]

    recent_incidents = []
    for row, label in repo.incidents.recent_incidents(session, RECENT_INCIDENT_LIMIT):
        metrics = repo.incidents.incident_metrics_bulk(session, [row.id]).get(row.id)
        recent_incidents.append(shape_incident(row, label, metrics))

    return {
        "stats": stats,
        "charts": charts,
        "recent_events": recent_events,
        "recent_incidents": recent_incidents,
        "empty_states": _empty_states(session, stats, charts),
    }


def _provenance_map(
    *,
    suspicious: int,
    blocked: int,
    flagged: int,
    allowed: int,
    prevented_minor: int,
    realized_minor: int,
    latency: dict,
) -> dict:
    return {
        "total_simulated_transactions": _provenance(
            "simulation",
            "Count of ledger rows written by the simulation engine. Runs in the "
            "Defense Comparison Lab add three runs, so this counter includes them.",
        ),
        "total_value_minor": _provenance(
            "simulation", "Exact integer sum of the amount of every simulated transaction."
        ),
        "suspicious_transactions": _provenance(
            "model",
            f"Detector decisions other than ALLOW: {blocked} blocked and {flagged} flagged. "
            f"{allowed} were allowed. The detector's conclusion is not evidence of wrongdoing.",
        ),
        "value_at_risk": _provenance(
            "estimate",
            "Transactions that carried a synthetic fraud label OR were not allowed. "
            "One predicate over one row set, so nothing is counted twice.",
        ),
        "loss_prevented": _provenance(
            "estimate",
            f"{minor_units_to_float(prevented_minor):,.2f} INR of fraudulent value stopped before "
            "settlement. Avoided loss, not money that moved.",
        ),
        "realized_loss": _provenance(
            "estimate",
            f"{minor_units_to_float(realized_minor):,.2f} INR of labelled fraud that settled. "
            "This is the only bucket counted as an actual loss.",
        ),
        "detection_latency": _provenance(
            "model",
            f"In-process inference latency over {latency.get('samples', 0)} scored transactions. "
            "Excludes the simulation's pacing delay.",
        ),
        "open_incidents": _provenance(
            "simulation",
            "Incidents in the OPEN or INVESTIGATING state. Counts simulated alerts awaiting review, "
            "not confirmed fraud.",
        ),
        "active_runs": _provenance(
            "simulation", "Simulation runs that are PENDING or RUNNING in this database."
        ),
        "observed_dataset": _provenance(
            "observed_dataset",
            "Statistics of the ML training dataset, read from models/metrics.json as recorded at "
            "training time. Kept separate from simulation output.",
        ),
    }


def _empty_states(session: Session, stats: dict, charts: dict) -> list[str]:
    """Explain any zero that means "no data yet" rather than "nothing happened"."""
    notes: list[str] = []
    if stats["total_simulated_transactions"] == 0:
        notes.append(
            "No simulated transactions exist yet. Open Attack Simulator, choose a scenario and "
            "run it to populate the ledger, the detector and the incident queue."
        )
    if stats["total_incidents"] == 0 and stats["total_simulated_transactions"] > 0:
        notes.append(
            "Transactions exist but no incident was opened: no transaction was flagged or blocked "
            "and no security event reached HIGH severity. Running a scenario at a higher event "
            "count, or lowering the flag threshold in Settings, will produce incidents."
        )
    if stats["suspicious_transactions"] == 0 and stats["total_simulated_transactions"] > 0:
        notes.append(
            "Every simulated transaction was allowed by the detector. If the runs used the NONE "
            "defense configuration, that is expected: nothing was scored."
        )
    if not charts["volume_over_time"]:
        notes.append("The volume chart is empty because no transaction has a recorded timestamp yet.")
    if stats["observed_dataset"] is None:
        notes.append(
            "No dataset statistics are available because the models have not been trained in this "
            "workspace. Run `python scripts/generate_dataset.py` then `python -m app.ml.train`."
        )
    if stats["detection_latency"]["samples"] == 0:
        notes.append(
            "No inference latency has been recorded: latency is only measured for transactions the "
            "detector actually scored, so runs under the NONE configuration contribute none."
        )
    return notes


__all__ = ["build_overview"]
