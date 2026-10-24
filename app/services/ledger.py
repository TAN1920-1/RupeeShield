"""Transaction and account views.

Two read-only responsibilities:

**Listing.** ``list_transaction_page`` and ``list_account_page`` return the standard
paginated envelope. The transaction query joins the account label and the detection
result in one statement, so a page of rows costs one query rather than one query per
row.

**Detail.** ``transaction_detail`` assembles everything recorded about one ledger
entry: the decision that was taken, the SHAP features behind it, the security
telemetry the scenario explicitly attached to that transaction, and a small
neighbourhood of related rows for context.

Money is converted through :mod:`app.utils.money` at the moment of serialisation, so
no float arithmetic ever touches a stored amount.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Account, Transaction
from app.services.incidents import shape_detection
from app.utils.money import minor_units_to_float

logger = get_logger(__name__)

#: How many related rows the neighbourhood panel shows. Enough to give context
#: without turning the detail view into a second ledger page.
NEIGHBOURHOOD_LIMIT = 12

#: Ledger settlement states. Only ``SETTLED`` represents value that actually moved.
TRANSACTION_STATUSES = ("SETTLED", "BLOCKED", "PENDING_REVIEW", "REVERSED")

#: Account review labels. An account is never labelled as criminal.
RISK_STATUSES = ("NORMAL", "ELEVATED_SIMULATED_RISK", "FLAGGED_FOR_INVESTIGATION")


def shape_transaction(row: Transaction, label: str | None = None, detection=None) -> dict:
    """Shape a ledger row into the ``TransactionOut`` payload."""
    return {
        "id": row.id,
        "run_id": row.run_id,
        "account_id": row.account_id,
        "account_label": label,
        "counterparty_id": row.counterparty_id,
        "counterparty_label": row.counterparty_label,
        "timestamp": row.timestamp,
        "amount_minor": int(row.amount_minor),
        "amount": minor_units_to_float(row.amount_minor),
        "currency": row.currency,
        "channel": row.channel,
        "country": row.country,
        "merchant_id": row.merchant_id,
        "device_id": row.device_id,
        "direction": row.direction,
        "status": row.status,
        "scenario": row.scenario,
        "risk_score": None if detection is None else round(float(detection.fraud_risk_score), 4),
        "decision": None if detection is None else detection.decision,
        "is_fraud": row.is_fraud,
    }


def shape_account(account: Account, transaction_count: int = 0) -> dict:
    return {
        "id": account.id,
        "label": account.label,
        "home_country": account.home_country,
        "preferred_channel": account.preferred_channel,
        "account_age_days": int(account.account_age_days),
        "balance_minor": int(account.balance_minor),
        "balance": minor_units_to_float(account.balance_minor),
        "opening_balance_minor": int(account.opening_balance_minor),
        "risk_status": account.risk_status,
        "is_synthetic": bool(account.is_synthetic),
        "transaction_count": int(transaction_count),
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_transaction_page(
    session: Session,
    *,
    limit: int,
    offset: int,
    account_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    scenario: str | None = None,
    decision: str | None = None,
    min_amount_minor: int | None = None,
) -> dict:
    rows, total = repo.transactions.list_transactions(
        session,
        limit=limit,
        offset=offset,
        account_id=account_id,
        run_id=run_id,
        status=status,
        scenario=scenario,
        decision=decision,
        min_amount_minor=min_amount_minor,
    )
    items = [shape_transaction(row, label, detection) for row, label, detection in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "returned": len(items)}


def _security_event_detail(row) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp,
        "event_type": row.event_type,
        "severity": row.severity,
        "country": row.country,
        "device_id": row.device_id,
        "source_ip_hash": row.source_ip_hash,
        "details": dict(row.details or {}),
    }


def _neighbourhood(
    session: Session, row: Transaction, limit: int = NEIGHBOURHOOD_LIMIT
) -> list[dict]:
    """Recent transactions of the same account and of the counterparty.

    Returned as context for a human, with the relation stated explicitly so a
    related row is never mistaken for evidence of wrongdoing.
    """
    related: list[dict] = []
    seen = {row.id}

    for other in repo.transactions.account_history(session, row.account_id, limit=limit + 1):
        if other.id in seen:
            continue
        seen.add(other.id)
        related.append(
            {
                "id": other.id,
                "relation": "SAME_ACCOUNT",
                "timestamp": other.timestamp,
                "amount_minor": int(other.amount_minor),
                "amount": minor_units_to_float(other.amount_minor),
                "channel": other.channel,
                "counterparty_id": other.counterparty_id,
                "counterparty_label": other.counterparty_label,
                "status": other.status,
                "direction": other.direction,
            }
        )

    if row.counterparty_id:
        for other in repo.transactions.account_history(
            session, row.counterparty_id, limit=limit // 2 + 1
        ):
            if other.id in seen:
                continue
            seen.add(other.id)
            related.append(
                {
                    "id": other.id,
                    "relation": "COUNTERPARTY_HISTORY",
                    "timestamp": other.timestamp,
                    "amount_minor": int(other.amount_minor),
                    "amount": minor_units_to_float(other.amount_minor),
                    "channel": other.channel,
                    "counterparty_id": other.counterparty_id,
                    "counterparty_label": other.counterparty_label,
                    "status": other.status,
                    "direction": other.direction,
                }
            )

    related.sort(key=lambda item: item["timestamp"], reverse=True)
    return related[:limit]


def transaction_detail(session: Session, transaction_id: str) -> dict:
    """Full detail for one ledger entry.

    Raises :class:`LookupError` when the id does not exist so the API can answer
    404 instead of returning an empty object that looks like a real transaction.
    """
    row = repo.transactions.get_transaction(session, transaction_id)
    if row is None:
        raise LookupError(f"No transaction with id {transaction_id}.")

    label = repo.accounts.get_accounts(session, [row.account_id]).get(row.account_id)
    detection = repo.detections.detection_for_transaction(session, transaction_id)

    payload = shape_transaction(row, label.label if label else None, detection)
    payload["detection"] = shape_detection(detection) if detection is not None else None
    payload["explanation"] = list(detection.top_features or []) if detection is not None else []
    payload["security_events"] = [
        _security_event_detail(event)
        for event in repo.detections.security_events_for_transaction(session, transaction_id)
    ]
    payload["neighborhood"] = _neighbourhood(session, row)
    return payload


def list_account_page(
    session: Session,
    *,
    limit: int,
    offset: int,
    risk_status: str | None = None,
) -> dict:
    rows, total = repo.accounts.list_accounts(
        session, limit=limit, offset=offset, risk_status=risk_status
    )
    counts = repo.accounts.transaction_counts(session, [row.id for row in rows])
    items = [shape_account(row, counts.get(row.id, 0)) for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "returned": len(items)}


def account_detail(session: Session, account_id: str) -> dict:
    account = repo.accounts.get_account(session, account_id)
    if account is None:
        raise LookupError(f"No account with id {account_id}.")
    counts = repo.accounts.transaction_counts(session, [account_id])
    return shape_account(account, counts.get(account_id, 0))


__all__ = [
    "NEIGHBOURHOOD_LIMIT",
    "RISK_STATUSES",
    "TRANSACTION_STATUSES",
    "account_detail",
    "list_account_page",
    "list_transaction_page",
    "shape_account",
    "shape_transaction",
    "transaction_detail",
]
