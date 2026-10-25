"""Simulation run orchestration.

The engine (:mod:`app.services.simulation.engine`) knows how to replay a script; this
module owns the *lifecycle* around it — creating the run row, choosing between
synchronous and paced execution, letting an operator stop a paced run, and shaping
the result for the API.

Two modes:

``batch``
    Replay the whole script and answer once it has finished. Simple, deterministic,
    and what the Attack Simulator uses by default.

``stream``
    Replay the script with a delay between events on a worker thread, answering
    immediately. This is what makes Live Monitoring a real feed: the browser watches
    rows arrive as the engine writes them, instead of animating data it already has.

Stop flags live in this process only. If the process restarts mid-run the run row is
orphaned, and :func:`stop_run` resolves that explicitly rather than pretending a
thread is still working.
"""
from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.session import SessionLocal
from app.security.audit import record_audit
from app.security.validation import ValidationError
from app.services import banking
from app.services.financial_impact import ASSUMPTIONS, impact_row_to_dict
from app.services.incidents import shape_incident
from app.services.simulation import catalog
from app.services.simulation.accounts import ROLE_VICTIM, account_id
from app.services.simulation.engine import execute_plan
from app.services.simulation.scenarios import build_plan
from app.utils.ids import new_id
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

MODES = ("batch", "stream")
ACTIVE_STATUSES = ("PENDING", "RUNNING")

_stop_flags: dict[str, threading.Event] = {}
_flags_lock = threading.Lock()


class RunNotFound(LookupError):
    """Raised when a run id does not exist (mapped to HTTP 404)."""


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------
def list_scenarios() -> list[dict]:
    return catalog.scenario_list()


def scenario_info(key: str) -> dict:
    return catalog.scenario_info(key)


def list_defense_configs() -> list[dict]:
    from app.services.detection import config_catalog

    return config_catalog()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def create_run(
    session: Session,
    *,
    scenario: str,
    n_events: int,
    seed: int,
    defense_config: str,
    mode: str = "batch",
    pace_per_second: float | None = None,
) -> dict:
    """Create and start a run. Returns the run detail in its current state.

    In ``batch`` mode the run has finished by the time this returns. In ``stream``
    mode a worker thread has started and the returned status is ``RUNNING``.
    """
    from app.services.detection import DEFENSE_CONFIGS

    if not catalog.is_valid_scenario(scenario):
        raise ValidationError(f"scenario must be one of: {', '.join(catalog.SCENARIO_KEYS)}.")
    if defense_config not in DEFENSE_CONFIGS:
        raise ValidationError(f"defense_config must be one of: {', '.join(DEFENSE_CONFIGS)}.")
    if mode not in MODES:
        raise ValidationError(f"mode must be one of: {', '.join(MODES)}.")
    if n_events > settings.max_simulation_events:
        raise ValidationError(
            f"n_events must be at most {settings.max_simulation_events} to keep one run bounded."
        )

    run_id = new_id("RUN")
    repo.runs.create_run(
        session,
        run_id=run_id,
        scenario=scenario,
        defense_config=defense_config,
        seed=seed,
        requested_events=n_events,
        mode=mode,
        params={
            "n_events": n_events,
            "seed": seed,
            "defense_config": defense_config,
            "pace_per_second": pace_per_second,
        },
    )
    record_audit(
        session,
        action="SIMULATION_RUN_CREATED",
        entity_type="simulation_run",
        entity_id=run_id,
        details={
            "scenario": scenario,
            "n_events": n_events,
            "seed": seed,
            "defense_config": defense_config,
            "mode": mode,
        },
    )

    if mode == "stream":
        pace = float(pace_per_second or settings.default_stream_pace_per_second)
        repo.runs.set_run_param(session, repo.runs.get_run(session, run_id), "pace_per_second", pace)
        _start_streaming_run(run_id, scenario, seed, n_events, defense_config, pace)
    else:
        _execute(run_id, scenario, seed, n_events, defense_config, stop_flag=None, pace_per_second=None)

    return run_detail(session, run_id)


def _execute(
    run_id: str,
    scenario: str,
    seed: int,
    n_events: int,
    defense_config: str,
    *,
    stop_flag: threading.Event | None,
    pace_per_second: float | None,
) -> None:
    """Replay the plan against the ledger in a dedicated session.

    A dedicated session is required for paced runs: the request-scoped session is
    closed as soon as the create/stream response is returned.
    """
    session: Session = SessionLocal()
    try:
        run = repo.runs.get_run(session, run_id)
        if run is None:  # pragma: no cover - the caller just created it
            logger.error("Run %s vanished before execution.", run_id)
            return
        repo.runs.mark_started(session, run)

        # Truncated to the second so the scripted event timestamps are reproducible
        # to the second rather than to the microsecond.
        start_time: datetime = utcnow().replace(microsecond=0)
        plan = build_plan(scenario, seed=seed, n_events=n_events)
        outcome = execute_plan(
            session,
            plan,
            run_id=run_id,
            defense_config=defense_config,
            start_time=start_time,
            stop_flag=stop_flag,
            pace_per_second=pace_per_second,
        )
        repo.runs.mark_finished(
            session,
            repo.runs.get_run(session, run_id),
            status=outcome.status,
            generated_events=outcome.generated_events,
            summary=outcome.summary,
        )
        # Give the demo customer the accounts this run attacked. The rows are not
        # copied: the customer's dashboard, transaction history and security centre
        # then read the very transactions, events, detections and incidents the
        # scenario just wrote, which is what "linked through backend records" means.
        banking.link_run_accounts(
            session,
            run_id,
            [
                account_id(run_id, account.index)
                for account in plan.accounts
                if account.role == ROLE_VICTIM
            ],
        )
        record_audit(
            session,
            action="SIMULATION_RUN_FINISHED",
            entity_type="simulation_run",
            entity_id=run_id,
            outcome=outcome.status,
            details={
                "scenario": scenario,
                "generated_events": outcome.generated_events,
                "incidents": len(outcome.incident_ids),
                "fingerprint": plan.fingerprint,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the run row must record the failure
        logger.exception("Simulation run %s failed.", run_id)
        try:
            repo.runs.mark_finished(
                session,
                repo.runs.get_run(session, run_id),
                status="FAILED",
                generated_events=0,
                summary={},
                error=str(exc)[:500],
            )
            record_audit(
                session,
                action="SIMULATION_RUN_FAILED",
                entity_type="simulation_run",
                entity_id=run_id,
                outcome="FAILED",
                details={"error": str(exc)},
            )
        except Exception:  # pragma: no cover - nothing further can be done
            logger.exception("Could not mark run %s as failed.", run_id)
    finally:
        session.close()
        with _flags_lock:
            _stop_flags.pop(run_id, None)


def _start_streaming_run(
    run_id: str,
    scenario: str,
    seed: int,
    n_events: int,
    defense_config: str,
    pace_per_second: float,
) -> None:
    stop_flag = threading.Event()
    with _flags_lock:
        _stop_flags[run_id] = stop_flag
    thread = threading.Thread(
        target=_execute,
        kwargs={
            "run_id": run_id,
            "scenario": scenario,
            "seed": seed,
            "n_events": n_events,
            "defense_config": defense_config,
            "stop_flag": stop_flag,
            "pace_per_second": pace_per_second,
        },
        name=f"rupeeshield-run-{run_id}",
        daemon=True,
    )
    thread.start()
    logger.info(
        "Streaming run %s started (%s, %s events at %.1f/s).",
        run_id,
        scenario,
        n_events,
        pace_per_second,
    )


def stop_run(session: Session, run_id: str) -> dict:
    """Ask a running simulation to stop at its next event boundary.

    Idempotent: stopping a run that has already finished returns its current state
    without changing anything.
    """
    run = repo.runs.get_run(session, run_id)
    if run is None:
        raise RunNotFound(f"No simulation run with id {run_id}.")

    if run.status not in ACTIVE_STATUSES:
        return run_detail(session, run_id)

    with _flags_lock:
        flag = _stop_flags.get(run_id)

    if flag is None:
        # No worker thread exists in this process, so the run was orphaned by a
        # restart. Saying so is more useful than leaving it "RUNNING" forever.
        repo.runs.mark_finished(
            session,
            run,
            status="STOPPED",
            generated_events=run.generated_events or 0,
            summary=dict(run.summary or {}),
            error="The process restarted while this run was active, so no worker remained to stop.",
        )
        record_audit(
            session,
            action="SIMULATION_RUN_STOPPED",
            entity_type="simulation_run",
            entity_id=run_id,
            outcome="STOPPED",
            details={"reason": "orphaned run"},
        )
        return run_detail(session, run_id)

    flag.set()
    record_audit(
        session,
        action="SIMULATION_RUN_STOP_REQUESTED",
        entity_type="simulation_run",
        entity_id=run_id,
        details={"status": run.status},
    )
    return run_detail(session, run_id)


def reset_run(session: Session, run_id: str) -> dict:
    """Delete a run and everything derived from it.

    Irreversible: the ledger rows, detections, security events, incidents, financial
    impact and replay records all disappear with the run. The audit trail keeps a
    record that it happened.
    """
    run = repo.runs.get_run(session, run_id)
    if run is None:
        raise RunNotFound(f"No simulation run with id {run_id}.")

    summary = {
        "scenario": run.scenario,
        "defense_config": run.defense_config,
        "generated_events": run.generated_events,
    }
    repo.runs.delete_run(session, run)
    record_audit(
        session,
        action="SIMULATION_RUN_RESET",
        entity_type="simulation_run",
        entity_id=run_id,
        details=summary,
    )
    return {"message": f"Simulation run {run_id} and all rows derived from it were deleted."}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def run_detail(session: Session, run_id: str) -> dict:
    run = repo.runs.get_run(session, run_id)
    if run is None:
        raise RunNotFound(f"No simulation run with id {run_id}.")

    summary = dict(run.summary or {})
    incidents = []
    rows = repo.incidents.incidents_for_run(session, run_id)
    if rows:
        metrics = repo.incidents.incident_metrics_bulk(session, [row.id for row in rows])
        labels = {
            account.id: account.label
            for account in repo.accounts.get_accounts(
                session, [row.primary_account_id for row in rows]
            ).values()
        }
        incidents = [
            shape_incident(row, labels.get(row.primary_account_id), metrics.get(row.id))
            for row in rows
        ]

    impact = repo.governance.financial_impact_for_run(session, run_id)

    return {
        "id": run.id,
        "scenario": run.scenario,
        "scenario_name": catalog.scenario_name(run.scenario),
        "defense_config": run.defense_config,
        "seed": run.seed,
        "requested_events": run.requested_events,
        "generated_events": run.generated_events or 0,
        "status": run.status,
        "mode": run.mode,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "error": run.error,
        "summary": summary,
        "incidents": incidents,
        "detection_summary": summary.get("detections", {}),
        "financial_impact": impact_row_to_dict(impact) if impact else None,
        "assumptions": list(impact.assumptions) if impact else list(ASSUMPTIONS),
    }


def list_run_page(
    session: Session, *, limit: int, offset: int, scenario: str | None = None
) -> dict:
    rows, total = repo.runs.list_runs(session, limit=limit, offset=offset, scenario=scenario)
    items = [
        {
            "id": run.id,
            "scenario": run.scenario,
            "scenario_name": catalog.scenario_name(run.scenario),
            "defense_config": run.defense_config,
            "seed": run.seed,
            "requested_events": run.requested_events,
            "generated_events": run.generated_events or 0,
            "status": run.status,
            "mode": run.mode,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "created_at": run.created_at,
            "error": run.error,
            "summary": dict(run.summary or {}),
        }
        for run in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "returned": len(items)}


__all__ = [
    "MODES",
    "RunNotFound",
    "create_run",
    "list_defense_configs",
    "list_run_page",
    "list_scenarios",
    "reset_run",
    "run_detail",
    "scenario_info",
    "stop_run",
]
