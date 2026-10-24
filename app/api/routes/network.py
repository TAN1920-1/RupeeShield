"""Financial network graph (Module F).

Graph *construction* lives in :mod:`app.services.network`; this route only exposes
it. Building the graph server-side means it can be asserted in a test without a
browser, and a different renderer can be swapped in without touching the queries.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.network import NetworkGraphOut
from app.security.validation import validate_identifier
from app.services.network import DEFAULT_NODE_LIMIT, MAX_NODE_LIMIT, build_graph

router = APIRouter(prefix="/network", tags=["network"])


@router.get(
    "/graph",
    response_model=NetworkGraphOut,
    summary="Directed transfer graph",
    description="Nodes are simulated accounts, edges are transfers with direction and amount. An "
                "edge is marked `suspicious` when a detector did not allow at least one transaction "
                "on it — an observation about model output, not evidence of wrongdoing. Supply "
                "`incident_id` for an incident-scoped view or `run_id` for a whole run.",
)
def graph(
    session: SessionDep,
    run_id: str | None = Query(default=None),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_NODE_LIMIT, ge=1, le=MAX_NODE_LIMIT),
) -> dict:
    if incident_id:
        validate_identifier(incident_id, "incident_id")
    if run_id:
        validate_identifier(run_id, "run_id")
    return build_graph(session, run_id=run_id, incident_id=incident_id, limit=limit)


__all__ = ["router"]
