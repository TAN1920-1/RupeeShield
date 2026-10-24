"""Incident investigation and replay (Module H).

Reading an incident, moving it through its lifecycle, appending an investigator note
and replaying it read-only. Notes are appended and never rewritten; replay never
edits the original incident history.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.common import Page
from app.schemas.incident import (
    IncidentDetailOut,
    IncidentNoteRequest,
    IncidentOut,
    IncidentStatusRequest,
    ReplayOut,
)
from app.security.validation import (
    optional_choice,
    page_params,
    sanitise_text,
    validate_choice,
    validate_identifier,
)
from app.services.incidents import (
    INCIDENT_STATUSES,
    add_note,
    change_status,
    incident_detail,
    list_incident_page,
    replay_incident,
)
from app.services.simulation import catalog

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentOut], summary="Incident queue")
def incidents(
    session: SessionDep,
    status: str | None = Query(default=None),
    scenario: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    return list_incident_page(
        session,
        limit=resolved_limit,
        offset=resolved_offset,
        status=optional_choice(status, INCIDENT_STATUSES, "status"),
        scenario=optional_choice(scenario, catalog.SCENARIO_KEYS, "scenario"),
    )


@router.get("/{incident_id}", response_model=IncidentDetailOut, summary="Full incident detail")
def detail(incident_id: str, session: SessionDep) -> dict:
    validate_identifier(incident_id, "incident_id")
    return incident_detail(session, incident_id)


@router.patch(
    "/{incident_id}/status",
    response_model=IncidentDetailOut,
    summary="Move an incident through its lifecycle",
)
def update_status(incident_id: str, payload: IncidentStatusRequest, session: SessionDep) -> dict:
    validate_identifier(incident_id, "incident_id")
    status = validate_choice(payload.status, INCIDENT_STATUSES, "status")
    return change_status(session, incident_id, status)


@router.post(
    "/{incident_id}/notes",
    response_model=IncidentDetailOut,
    summary="Append an investigator note",
    description="Notes are timestamped and appended; existing notes are never rewritten.",
)
def add_investigation_note(
    incident_id: str, payload: IncidentNoteRequest, session: SessionDep
) -> dict:
    validate_identifier(incident_id, "incident_id")
    note = sanitise_text(payload.note, max_length=2000, field="note")
    return add_note(session, incident_id, note)


@router.post(
    "/{incident_id}/replay",
    response_model=ReplayOut,
    summary="Replay an incident read-only",
    description="Recomputes the incident's decisions from the scenario script and reports where the "
                "recomputed decision differs from the recorded one. The only row written is a record "
                "of the replay itself: `mutates_history` is always false.",
)
def replay(incident_id: str, session: SessionDep) -> dict:
    validate_identifier(incident_id, "incident_id")
    return replay_incident(session, incident_id)


__all__ = ["router"]
