"""Attack simulation run lifecycle (Module B).

Thin by design: every rule about how a run may be created, stopped or reset lives
in :mod:`app.services.simulation_runner`. The routes only validate the boundary and
map service exceptions onto status codes.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.common import MessageResponse, Page
from app.schemas.simulation import SimulationRunDetail, SimulationRunOut, SimulationRunRequest
from app.security.validation import optional_choice, page_params, validate_identifier
from app.services.simulation import catalog
from app.services.simulation_runner import (
    create_run,
    list_run_page,
    reset_run,
    run_detail,
    stop_run,
)

router = APIRouter(prefix="/simulation", tags=["simulation"])


@router.get("/runs", response_model=Page[SimulationRunOut], summary="List simulation runs")
def runs(
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
    scenario: str | None = Query(default=None),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    normalised = optional_choice(scenario, catalog.SCENARIO_KEYS, "scenario")
    return list_run_page(session, limit=resolved_limit, offset=resolved_offset, scenario=normalised)


@router.post(
    "/runs",
    response_model=SimulationRunDetail,
    status_code=201,
    summary="Start a simulation run",
    description=(
        "`mode=batch` replays the whole script and answers once it has finished. `mode=stream` "
        "starts a paced worker and returns immediately with status RUNNING, which is what makes "
        "Live Monitoring a real feed: the browser watches persisted rows arrive."
    ),
)
def start_run(payload: SimulationRunRequest, session: SessionDep) -> dict:
    return create_run(
        session,
        scenario=payload.scenario,
        n_events=payload.n_events,
        seed=payload.seed,
        defense_config=payload.defense_config,
        mode=payload.mode,
        pace_per_second=payload.pace_per_second,
    )


@router.get("/runs/{run_id}", response_model=SimulationRunDetail, summary="Run detail")
def get_run(run_id: str, session: SessionDep) -> dict:
    validate_identifier(run_id, "run_id")
    return run_detail(session, run_id)


@router.post(
    "/runs/{run_id}/stop",
    response_model=SimulationRunDetail,
    summary="Stop a running simulation",
    description="Idempotent: stopping a run that has already finished returns its current state. "
                "A run orphaned by a process restart is marked STOPPED explicitly rather than left "
                "looking like it is still working.",
)
def stop(run_id: str, session: SessionDep) -> dict:
    validate_identifier(run_id, "run_id")
    return stop_run(session, run_id)


@router.delete(
    "/runs/{run_id}",
    response_model=MessageResponse,
    summary="Delete a run and everything derived from it",
    description="Irreversible: the ledger rows, detections, security events, incidents and impact "
                "rows all disappear with the run. The audit trail keeps a record that it happened.",
)
def reset(run_id: str, session: SessionDep) -> dict:
    validate_identifier(run_id, "run_id")
    return reset_run(session, run_id)


__all__ = ["router"]
