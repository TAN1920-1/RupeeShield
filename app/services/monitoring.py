"""Live monitoring feed.

The feed is a **merged, ordered view of persisted rows**: security telemetry and
scored transactions from one simulation run, interleaved by timestamp. Nothing here
invents an event — every row the browser sees was written by
:mod:`app.services.simulation.engine` and can be read back from the database.

Two access patterns are supported:

``build_feed``
    One page of the feed, with a monotonically increasing ``sequence`` and a
    ``since_sequence`` cursor so a client can poll without re-reading history. The
    sequence is derived from the row ordering, so the same database always produces
    the same sequence numbers.

``event_stream``
    An async generator used by the Server-Sent Events endpoint. It polls for rows
    that appeared after the last cursor and stops once the run reaches a terminal
    status and no further rows arrive.

Ordering rule: at an identical timestamp a security event sorts before a
transaction, because the correlation rules look *backwards* in time from the
transaction — the same convention the engine's work queue uses.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.session import SessionLocal
from app.utils.money import minor_units_to_float
from app.utils.timeutils import iso_utc, utcnow

logger = get_logger(__name__)

SECURITY_KIND = "SECURITY_EVENT"
TRANSACTION_KIND = "TRANSACTION"

_KIND_RANK = {SECURITY_KIND: 0, TRANSACTION_KIND: 1}

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500

#: Response shown for telemetry that is logged but has no transaction to act on.
LOGGED_RESPONSE = "LOGGED"

TERMINAL_RUN_STATUSES = {"COMPLETED", "STOPPED", "FAILED"}


def resolve_run_id(session: Session, run_id: str | None) -> str | None:
    """Default the feed to the most recent run when no run is named.

    Live Monitoring is meant to watch "the simulation that is running now", so an
    unqualified request attaches itself to the newest run rather than showing
    nothing. Returns ``None`` only when no run has ever been created.
    """
    if run_id:
        return run_id
    rows, _total = repo.runs.list_runs(session, limit=1, offset=0)
    return rows[0].id if rows else None


def shape_security_event(row, label: str | None = None, sequence: int = 0) -> dict:
    """Shape one ``SecurityEvent`` row into a monitoring event.

    Also used by the Overview's "recent security events" panel so both surfaces
    describe an event identically.
    """
    country = row.country or "unknown country"
    device = row.device_id or "unrecorded device"
    return {
        "sequence": int(sequence),
        "timestamp": row.timestamp,
        "run_id": row.run_id,
        "event_type": row.event_type,
        "account_id": row.account_id,
        "account_label": label,
        "amount": None,
        "amount_minor": None,
        "decision": None,
        "risk_score": None,
        "response": LOGGED_RESPONSE,
        "detail": f"{row.severity} severity · {country} · {device}",
        "source": "simulation",
    }


def shape_transaction_event(row, detection=None, label: str | None = None, sequence: int = 0) -> dict:
    """Shape one ``Transaction`` (+ its detection) into a monitoring event.

    ``decision`` is what the detector concluded; ``response`` is what the bank did
    about it (``SETTLED`` | ``BLOCKED`` | ``PENDING_REVIEW``). Keeping the two
    separate matters: a transaction can be flagged and still settle.
    """
    decision = detection.decision if detection is not None else None
    counterparty = row.counterparty_label or "an external party"
    detail = (
        f"{row.direction.title()} of {minor_units_to_float(row.amount_minor):,.2f} INR "
        f"via {row.channel} to {counterparty}"
    )
    if decision:
        detail += f" · detector concluded {decision}"
    else:
        detail += " · not scored"

    return {
        "sequence": int(sequence),
        "timestamp": row.timestamp,
        "run_id": row.run_id,
        "event_type": TRANSACTION_KIND,
        "account_id": row.account_id,
        "account_label": label,
        "amount": minor_units_to_float(row.amount_minor),
        "amount_minor": int(row.amount_minor),
        "decision": decision,
        "risk_score": None if detection is None else round(float(detection.fraud_risk_score), 4),
        "response": row.status,
        "detail": detail,
        "source": "simulation",
    }


def _merged(session: Session, run_id: str) -> list[dict]:
    """Every monitoring event for a run, ordered and sequence-numbered."""
    security_rows = repo.detections.security_events_for_run(session, run_id)
    transaction_rows = repo.transactions.transactions_for_run(session, run_id)
    detections = repo.detections.detections_for_run(session, run_id)
    detection_by_transaction = {
        row.transaction_id: row for row in detections if row.transaction_id
    }

    account_ids = {row.account_id for row in security_rows} | {
        row.account_id for row in transaction_rows
    }
    labels = {account.id: account.label for account in repo.accounts.get_accounts(session, list(account_ids)).values()}

    entries: list[tuple] = []
    for row in security_rows:
        entries.append((row.timestamp, _KIND_RANK[SECURITY_KIND], row.id, SECURITY_KIND, row))
    for row in transaction_rows:
        entries.append((row.timestamp, _KIND_RANK[TRANSACTION_KIND], row.id, TRANSACTION_KIND, row))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))

    feed: list[dict] = []
    for sequence, (timestamp, _rank, _row_id, kind, row) in enumerate(entries, start=1):
        label = labels.get(row.account_id)
        if kind == SECURITY_KIND:
            feed.append(shape_security_event(row, label, sequence))
        else:
            feed.append(
                shape_transaction_event(row, detection_by_transaction.get(row.id), label, sequence)
            )
    return feed


def build_feed(
    session: Session,
    *,
    run_id: str | None = None,
    since_sequence: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
    """One page of the feed: events with ``sequence > since_sequence``."""
    resolved = resolve_run_id(session, run_id)
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
    if resolved is None:
        return {"events": [], "total": 0, "run_id": None, "generated_at": utcnow()}

    feed = _merged(session, resolved)
    cursor = max(int(since_sequence or 0), 0)
    pending = [event for event in feed if event["sequence"] > cursor]
    return {
        "events": pending[:page_size],
        "total": len(feed),
        "run_id": resolved,
        "generated_at": utcnow(),
    }


def _poll(session_factory, run_id: str, cursor: int, page_size: int) -> tuple[list[dict], int, str | None]:
    """Read new rows and the run's current status in a short-lived session."""
    session: Session = session_factory()
    try:
        feed = _merged(session, run_id)
        new_events = [event for event in feed if event["sequence"] > cursor]
        run = repo.runs.get_run(session, run_id)
        status = run.status if run is not None else None
        return new_events[:page_size], len(feed), status
    finally:
        session.close()


async def event_stream(
    run_id: str,
    *,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 15.0,
    max_seconds: float = 900.0,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> AsyncIterator[dict]:
    """Yield newly recorded events until the run finishes or ``max_seconds`` elapse.

    The blocking SQLite reads run in a worker thread so the API event loop stays
    responsive while several browser tabs are watching the same run.
    """
    cursor = 0
    started = time.monotonic()
    last_beat = started

    while True:
        events, _total, status = await asyncio.to_thread(
            _poll, SessionLocal, run_id, cursor, page_size
        )
        for event in events:
            cursor = max(cursor, int(event["sequence"]))
            yield {"event": "message", "data": _jsonable(event)}

        now = time.monotonic()
        if status in TERMINAL_RUN_STATUSES and not events:
            yield {
                "event": "complete",
                "data": {"run_id": run_id, "status": status, "generated_at": iso_utc(utcnow())},
            }
            return

        if now - started > max_seconds:
            yield {
                "event": "timeout",
                "data": {
                    "run_id": run_id,
                    "detail": f"Stream closed after {max_seconds:.0f}s to free the connection.",
                },
            }
            return

        if now - last_beat >= heartbeat_seconds:
            last_beat = now
            yield {"event": "heartbeat", "data": {"sequence": cursor}}

        await asyncio.sleep(poll_seconds)


def _jsonable(event: dict) -> dict:
    """Convert a shaped event into JSON-safe primitives for SSE."""
    payload = dict(event)
    timestamp = payload.get("timestamp")
    if hasattr(timestamp, "isoformat"):
        payload["timestamp"] = iso_utc(timestamp)
    return payload


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "SECURITY_KIND",
    "TERMINAL_RUN_STATUSES",
    "TRANSACTION_KIND",
    "build_feed",
    "event_stream",
    "resolve_run_id",
    "shape_security_event",
    "shape_transaction_event",
]
