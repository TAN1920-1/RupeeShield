"""Live monitoring feed (Module C).

Two access patterns over the same persisted rows:

``GET /monitoring/events``
    one page, with a ``since_sequence`` cursor so a client can poll without
    re-reading history;

``GET /monitoring/stream``
    Server-Sent Events. The generator polls for rows that appeared after the last
    cursor and closes when the run reaches a terminal status.

Nothing here invents an event: every row was written by the simulation engine. The
event vocabulary is ``message`` (one event), ``heartbeat`` (keep-alive),
``complete`` (the run finished) and ``timeout`` (the stream closed to free the
connection).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import SessionDep
from app.db.session import SessionLocal
from app.schemas.detection import MonitoringPage
from app.services.monitoring import DEFAULT_PAGE_SIZE, build_feed, event_stream, resolve_run_id

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get(
    "/events",
    response_model=MonitoringPage,
    summary="One page of the merged monitoring feed",
    description="Security telemetry and scored transactions from one run, interleaved by "
                "timestamp and sequence-numbered. Defaults to the newest run when no run_id is "
                "given, so the page shows 'what is running now'.",
)
def events(
    session: SessionDep,
    run_id: str | None = Query(default=None),
    since_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=500),
) -> dict:
    return build_feed(session, run_id=run_id, since_sequence=since_sequence, limit=limit)


@router.get(
    "/stream",
    summary="Server-Sent Events stream for one run",
    description="Yields newly recorded events as SSE `message` events, plus periodic heartbeats. "
                "Emits `complete` when the run finishes and `timeout` if the stream is closed to "
                "free the connection.",
)
async def stream(run_id: str | None = Query(default=None)):
    # Resolve the run in a short-lived session: the request-scoped dependency would
    # otherwise stay open for the whole lifetime of the stream.
    session = SessionLocal()
    try:
        resolved = resolve_run_id(session, run_id)
    finally:
        session.close()

    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No simulation run exists yet, so there is nothing to stream. "
                "Start one from the Attack Simulator first."
            ),
        )

    async def generator():
        yield ": rupeeshield stream open\n\n"
        async for payload in event_stream(resolved):
            data = json.dumps(payload["data"], default=str)
            yield f"event: {payload['event']}\ndata: {data}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


__all__ = ["router"]
