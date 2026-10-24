"""Transaction Explorer (ledger reads).

The list query joins the account label and the detection result in one statement, so
a page of rows costs one query rather than one per row.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Query

from app.api.deps import SessionDep
from app.schemas.common import Page
from app.schemas.transaction import TransactionDetailOut, TransactionOut
from app.security.validation import (
    non_negative_amount,
    optional_choice,
    page_params,
    validate_identifier,
)
from app.services.ledger import TRANSACTION_STATUSES, list_transaction_page, transaction_detail
from app.services.simulation import catalog
from app.utils.money import to_minor_units

router = APIRouter(prefix="/transactions", tags=["ledger"])

DECISIONS = ("ALLOW", "FLAG", "BLOCK")


@router.get(
    "",
    response_model=Page[TransactionOut],
    summary="Search the simulated ledger",
    description="Filters compose: account, run, decision, settlement status, scenario and a minimum "
                "amount in major units. Rows carry the synthetic ground-truth label as `is_fraud`, "
                "which is a generator label and never a model output.",
)
def list_transactions(
    session: SessionDep,
    account_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    status: str | None = Query(default=None),
    scenario: str | None = Query(default=None),
    min_amount: float | None = Query(default=None, ge=0),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
    x_request_id: str | None = Header(default=None, include_in_schema=False),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    amount = non_negative_amount(min_amount, "min_amount")
    return list_transaction_page(
        session,
        limit=resolved_limit,
        offset=resolved_offset,
        account_id=(account_id or None),
        run_id=(run_id or None),
        status=optional_choice(status, TRANSACTION_STATUSES, "status"),
        scenario=optional_choice(scenario, catalog.SCENARIO_KEYS, "scenario"),
        decision=optional_choice(decision, DECISIONS, "decision"),
        min_amount_minor=None if amount is None else to_minor_units(amount),
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionDetailOut,
    summary="Everything recorded about one ledger entry",
)
def get_transaction(transaction_id: str, session: SessionDep) -> dict:
    validate_identifier(transaction_id, "transaction_id")
    return transaction_detail(session, transaction_id)


__all__ = ["router"]
