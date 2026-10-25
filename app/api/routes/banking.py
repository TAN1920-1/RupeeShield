"""Customer banking endpoints.

Everything here is the signed-in customer's *own* money: the accounts they hold, the
ledger those accounts produced, the transfers they make and the security telemetry
recorded against them. The authorization scope is applied inside
:mod:`app.services.banking` (it is the first thing every query does), so these routes
never take an account id on trust — an id that is not theirs is a 404, not an empty
page.

Sign-in itself lives in :mod:`app.api.routes.auth`; the security-operations views of
the same underlying rows live under the ANALYST-only modules. This module is the
customer's side of one shared database.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query

from app.api.deps import CurrentUser, CustomerUser, SessionDep
from app.schemas.banking import (
    AccountCardOut,
    AccountListOut,
    CustomerTransactionDetailOut,
    CustomerTransactionOut,
    PayeeOut,
    SecurityCentreOut,
    SimulatedLoginOut,
    TransferRequest,
    TransferResultOut,
)
from app.schemas.common import Page
from app.security.validation import optional_choice, page_params, validate_identifier
from app.services import banking
from app.services.banking import WEB_DEVICE
from app.services.ledger import TRANSACTION_STATUSES

router = APIRouter(prefix="/banking", tags=["banking"])

#: The customer-facing transaction type filter. It is the ledger direction, named the
#: way the UI talks about it.
DIRECTIONS = ("DEBIT", "CREDIT")


@router.get(
    "/accounts",
    response_model=AccountListOut,
    summary="Your accounts and balance summary",
    description=(
        "Balances are read from the accounts table, never computed in the browser, so a "
        "completed transfer changes this number and the ledger in the same commit. "
        "`held` is outgoing value awaiting a decision: reported separately because it "
        "has not left the account yet."
    ),
)
def list_accounts(session: SessionDep, user: CurrentUser) -> dict:
    return banking.accounts_overview(session, user)


@router.get(
    "/accounts/{account_id}",
    response_model=AccountCardOut,
    summary="One of your accounts",
    description="404 when the account exists but belongs to somebody else: the scope is part of the query, not the UI.",
)
def read_account(account_id: str, session: SessionDep, user: CurrentUser) -> dict:
    resolved = validate_identifier(account_id, "account_id")
    return banking.account_card(session, user, resolved)


@router.get(
    "/payees",
    response_model=list[PayeeOut],
    summary="Accounts you can send money to",
    description=(
        "Your own other accounts plus the bank's payee accounts. Account numbers are "
        "masked and derived from the account id: no real-looking number is ever generated."
    ),
)
def list_payees(session: SessionDep, user: CurrentUser) -> list[dict]:
    return banking.list_payees(session, user)


@router.get(
    "/transactions",
    response_model=Page[CustomerTransactionOut],
    summary="Your transaction history",
    description=(
        "Filter by free-text search, account, type (debit/credit), settlement status, "
        "date range and whether the detector queried the row. `status` is the stored "
        "settlement state; the `status_label` on each row is what the customer sees "
        "(Completed / Pending / Blocked / Under Review / Flagged / Reversed)."
    ),
)
def list_transactions(
    session: SessionDep,
    user: CurrentUser,
    account_id: str | None = Query(default=None, description="Restrict to one of your accounts."),
    search: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, description="SETTLED | BLOCKED | PENDING_REVIEW | REVERSED"),
    direction: str | None = Query(default=None, description="Transaction type: DEBIT (outgoing) or CREDIT (incoming)."),
    suspicious: bool | None = Query(default=None, description="Only rows the detector did not allow outright."),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD or an ISO timestamp."),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD (inclusive to end of day) or an ISO timestamp."),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    return banking.transaction_page(
        session,
        user,
        limit=resolved_limit,
        offset=resolved_offset,
        account_id=validate_identifier(account_id, "account_id") if account_id else None,
        search=(search or "").strip() or None,
        status=optional_choice(status, TRANSACTION_STATUSES, "status"),
        direction=optional_choice(direction, DIRECTIONS, "direction"),
        suspicious=suspicious,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=CustomerTransactionDetailOut,
    summary="One transaction, with the evidence behind it",
    description=(
        "Includes the detection that scored the row, the feature contributions that "
        "drove the score, the correlated security events, and the incident it belongs to. "
        "These are the same rows the incident timeline and the financial-impact engine read."
    ),
)
def read_transaction(transaction_id: str, session: SessionDep, user: CurrentUser) -> dict:
    resolved = validate_identifier(transaction_id, "transaction_id")
    return banking.transaction_detail(session, user, resolved)


@router.post(
    "/transfers",
    response_model=TransferResultOut,
    summary="Send money",
    description=(
        "Validated on the server: the source must be yours, the amount must be positive "
        "and within the transfer bounds, the balance must cover it, and the 24-hour limit "
        "must not be exceeded. The debit is scored by the same detection engine the attack "
        "simulator uses, then both legs and both balance changes are written in one commit — "
        "the pair settles together or not at all. Send the same `idempotency_key` twice and "
        "the original result is returned instead of moving money again."
    ),
)
def create_transfer(
    payload: TransferRequest,
    session: SessionDep,
    user: CustomerUser,
    x_device_id: Annotated[str | None, Header()] = None,
) -> dict:
    return banking.create_transfer(
        session,
        user,
        source_account_id=validate_identifier(payload.source_account_id, "source_account_id"),
        recipient_account_id=validate_identifier(
            payload.recipient_account_id, "recipient_account_id"
        ),
        amount=payload.amount,
        description=payload.description,
        idempotency_key=payload.idempotency_key,
        device_id=(x_device_id or WEB_DEVICE),
    )


@router.get(
    "/security-centre",
    response_model=SecurityCentreOut,
    summary="Account Security Center",
    description=(
        "Your security posture, computed from the security events, detections and incidents "
        "recorded against your own accounts. Every figure has a stated meaning; the posture "
        "label is never colour alone."
    ),
)
def security_centre(session: SessionDep, user: CurrentUser) -> dict:
    return banking.security_centre(session, user)


@router.post(
    "/security-centre/simulate-login",
    response_model=SimulatedLoginOut,
    summary="Record a simulated sign-in from a new device",
    description=(
        "An explicit demonstration action: it writes one security event, raises one "
        "notification, and returns both. Nothing on any page invents an alert on a timer."
    ),
)
def simulate_login(
    session: SessionDep,
    user: CustomerUser,
    device_id: str | None = Query(default=None, max_length=40),
) -> dict:
    return banking.simulate_login(session, user, device_id=device_id)


__all__ = ["router"]
