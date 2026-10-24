"""Account roster and per-account evidence views.

An account is never described as criminal. The strongest label used is
``FLAGGED_FOR_INVESTIGATION``, and the per-account payload phrases what it finds as
leads to investigate rather than conclusions.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.common import Page
from app.schemas.network import AccountEventsOut
from app.schemas.transaction import AccountOut
from app.security.validation import optional_choice, page_params, validate_identifier
from app.services.ledger import RISK_STATUSES, account_detail, list_account_page
from app.services.network import account_events

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=Page[AccountOut], summary="Simulated account roster")
def list_accounts(
    session: SessionDep,
    risk_status: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    return list_account_page(
        session,
        limit=resolved_limit,
        offset=resolved_offset,
        risk_status=optional_choice(risk_status, RISK_STATUSES, "risk_status"),
    )


@router.get("/{account_id}", response_model=AccountOut, summary="One account")
def get_account(account_id: str, session: SessionDep) -> dict:
    validate_identifier(account_id, "account_id")
    return account_detail(session, account_id)


@router.get(
    "/{account_id}/events",
    response_model=AccountEventsOut,
    summary="Everything recorded about one account",
    description="Security telemetry, transactions, incidents and plain-language observations. "
                "Observations are phrased as leads, not findings.",
)
def get_account_events(account_id: str, session: SessionDep) -> dict:
    validate_identifier(account_id, "account_id")
    return account_events(session, account_id)


__all__ = ["router"]
