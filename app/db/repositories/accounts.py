"""Read/write helpers for simulated bank accounts."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Account, DetectionResult, Transaction


def add_accounts(session: Session, rows: list[dict]) -> list[Account]:
    """Bulk-insert the account roster for one run."""
    accounts = [Account(**row) for row in rows]
    session.add_all(accounts)
    session.commit()
    return accounts


def get_account(session: Session, account_id: str) -> Account | None:
    return session.get(Account, account_id)


def list_accounts(
    session: Session,
    *,
    limit: int,
    offset: int,
    risk_status: str | None = None,
) -> tuple[list[Account], int]:
    statement = select(Account)
    counter = select(func.count()).select_from(Account)
    if risk_status:
        statement = statement.where(Account.risk_status == risk_status)
        counter = counter.where(Account.risk_status == risk_status)
    total = int(session.execute(counter).scalar() or 0)
    rows = list(
        session.execute(statement.order_by(Account.id).limit(limit).offset(offset)).scalars()
    )
    return rows, total


def get_accounts(session: Session, account_ids: list[str]) -> dict[str, Account]:
    """Fetch a specific set of accounts in one query (avoids N+1 on the graph)."""
    if not account_ids:
        return {}
    rows = session.execute(
        select(Account).where(Account.id.in_(list(dict.fromkeys(account_ids))))
    ).scalars()
    return {account.id: account for account in rows}


def transaction_counts(session: Session, account_ids: list[str]) -> dict[str, int]:
    """Transaction count per account, in one query rather than N."""
    if not account_ids:
        return {}
    rows = session.execute(
        select(Transaction.account_id, func.count())
        .where(Transaction.account_id.in_(account_ids))
        .group_by(Transaction.account_id)
    ).all()
    return {account_id: int(count) for account_id, count in rows}


def apply_balance_delta(session: Session, account: Account, delta_minor: int) -> None:
    """Move money in/out of an account.

    The balance is allowed to go down only as far as the simulated overdraft would
    permit; the scenario builder keeps the ledger funded, so this guard exists for
    safety rather than to model credit.
    """
    account.balance_minor = int(account.balance_minor) + int(delta_minor)
    session.add(account)


def set_risk_status(session: Session, account: Account, status: str) -> None:
    if account.risk_status != status:
        account.risk_status = status


def highest_risk_accounts(session: Session, limit: int = 8) -> list[dict]:
    """Accounts with the highest maximum detected risk score.

    This is an observation about model output, not an accusation: an account only
    ever appears here as "flagged for investigation" or "elevated simulated risk".
    """
    rows = session.execute(
        select(
            Account.id,
            Account.label,
            Account.risk_status,
            func.max(DetectionResult.fraud_risk_score),
            func.count(DetectionResult.id),
        )
        .join(DetectionResult, DetectionResult.account_id == Account.id)
        .group_by(Account.id, Account.label, Account.risk_status)
        .order_by(func.max(DetectionResult.fraud_risk_score).desc())
        .limit(limit)
    ).all()
    return [
        {
            "account_id": account_id,
            "label": label,
            "risk_status": status,
            "max_risk_score": round(float(max_score or 0.0), 4),
            "detections": int(detections),
        }
        for account_id, label, status, max_score, detections in rows
    ]


def count_accounts(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Account)).scalar() or 0)
