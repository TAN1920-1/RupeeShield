"""Read/write helpers for ledger transactions."""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Account, DetectionResult, Transaction


def add_transaction(session: Session, row: dict) -> Transaction:
    transaction = Transaction(**row)
    session.add(transaction)
    session.flush()
    return transaction


def get_transaction(session: Session, transaction_id: str) -> Transaction | None:
    return session.get(Transaction, transaction_id)


def list_transactions(
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
) -> tuple[list[tuple[Transaction, str | None, DetectionResult | None]], int]:
    """Return ``(transaction, account_label, detection)`` triples plus the total.

    The detection is joined rather than fetched afterwards so the ``decision``
    filter and the pagination stay consistent with each other. Each simulated
    transaction is scored exactly once, so the join cannot duplicate a row; the
    count is nevertheless taken over ``DISTINCT`` transaction ids as a guard.
    """
    statement = (
        select(Transaction, Account.label, DetectionResult)
        .join(Account, Account.id == Transaction.account_id, isouter=True)
        .join(DetectionResult, DetectionResult.transaction_id == Transaction.id, isouter=True)
    )
    counter = select(func.count(func.distinct(Transaction.id))).select_from(Transaction).join(
        DetectionResult, DetectionResult.transaction_id == Transaction.id, isouter=True
    )

    filters = []
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if run_id:
        filters.append(Transaction.run_id == run_id)
    if status:
        filters.append(Transaction.status == status)
    if scenario:
        filters.append(Transaction.scenario == scenario)
    if decision:
        filters.append(DetectionResult.decision == decision)
    if min_amount_minor is not None:
        filters.append(Transaction.amount_minor >= min_amount_minor)
    for condition in filters:
        statement = statement.where(condition)
        counter = counter.where(condition)

    total = int(session.execute(counter).scalar() or 0)
    rows = session.execute(
        statement.order_by(Transaction.timestamp.desc(), Transaction.id).limit(limit).offset(offset)
    ).all()
    return [(row[0], row[1], row[2]) for row in rows], total


def overview_totals(session: Session) -> dict:
    """Ledger totals for the Overview dashboard, aggregated in SQL rather than in Python."""
    total_count, total_value = session.execute(
        select(func.count(), func.coalesce(func.sum(Transaction.amount_minor), 0)).select_from(Transaction)
    ).one()

    # "Value at risk" is the value of everything the scenario put in play: the
    # transactions that were labelled fraudulent OR that the detector did not
    # allow. One predicate over one row set, so nothing is counted twice.
    at_risk_count, at_risk_value = session.execute(
        select(func.count(func.distinct(Transaction.id)), func.coalesce(func.sum(Transaction.amount_minor), 0))
        .join(DetectionResult, DetectionResult.transaction_id == Transaction.id, isouter=True)
        .where(or_(Transaction.is_fraud == 1, DetectionResult.decision != "ALLOW"))
    ).one()

    decisions = {
        str(decision): int(count)
        for decision, count in session.execute(
            select(DetectionResult.decision, func.count()).group_by(DetectionResult.decision)
        ).all()
    }
    settled_fraud = int(
        session.execute(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.status == "SETTLED", Transaction.is_fraud == 1
            )
        ).scalar()
        or 0
    )
    return {
        "transaction_count": int(total_count or 0),
        "total_value_minor": int(total_value or 0),
        "value_at_risk_count": int(at_risk_count or 0),
        "value_at_risk_minor": int(at_risk_value or 0),
        "settled_fraud_minor": settled_fraud,
        "decisions": decisions,
    }


def transactions_for_run(session: Session, run_id: str) -> list[Transaction]:
    return list(
        session.execute(
            select(Transaction)
            .where(Transaction.run_id == run_id)
            .order_by(Transaction.timestamp, Transaction.id)
        ).scalars()
    )


def account_history(session: Session, account_id: str, limit: int = 50) -> list[Transaction]:
    """Most recent transactions for an account, oldest first (used to warm state)."""
    rows = list(
        session.execute(
            select(Transaction)
            .where(Transaction.account_id == account_id)
            .order_by(Transaction.timestamp.desc())
            .limit(limit)
        ).scalars()
    )
    return list(reversed(rows))


def amount_stats(session: Session, account_id: str) -> dict:
    row = session.execute(
        select(func.count(), func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.account_id == account_id
        )
    ).one()
    return {"count": int(row[0] or 0), "total_minor": int(row[1] or 0)}


def totals_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Transaction.status, func.count(), func.coalesce(func.sum(Transaction.amount_minor), 0))
        .group_by(Transaction.status)
    ).all()
    return {status: {"count": int(count), "amount_minor": int(amount)} for status, count, amount in rows}


def sum_amount(session: Session, statuses: list[str] | None = None, run_id: str | None = None) -> int:
    statement = select(func.coalesce(func.sum(Transaction.amount_minor), 0))
    if statuses:
        statement = statement.where(Transaction.status.in_(statuses))
    if run_id:
        statement = statement.where(Transaction.run_id == run_id)
    return int(session.execute(statement).scalar() or 0)


def count_transactions(session: Session, statuses: list[str] | None = None, run_id: str | None = None) -> int:
    statement = select(func.count()).select_from(Transaction)
    if statuses:
        statement = statement.where(Transaction.status.in_(statuses))
    if run_id:
        statement = statement.where(Transaction.run_id == run_id)
    return int(session.execute(statement).scalar() or 0)


def settled_fraud_total(session: Session, run_id: str | None = None) -> int:
    """Realized unauthorized value: settled transactions carrying a fraud label."""
    statement = select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
        Transaction.is_fraud == 1
    )
    if run_id:
        statement = statement.where(Transaction.run_id == run_id)
    return int(session.execute(statement).scalar() or 0)


def volume_series(session: Session, buckets: int = 24) -> list[dict]:
    """Transaction count and value per hour, for the Overview volume chart.

    Buckets on ``strftime`` so the aggregation happens in SQLite rather than
    pulling every row into Python.
    """
    rows = session.execute(
        select(
            func.strftime("%Y-%m-%dT%H:00", Transaction.timestamp).label("bucket"),
            func.count(),
            func.coalesce(func.sum(Transaction.amount_minor), 0),
        )
        .group_by("bucket")
        .order_by("bucket")
    ).all()
    series = [
        {"timestamp": bucket, "value": int(count), "extra": {"amount_minor": int(amount)}}
        for bucket, count, amount in rows
        if bucket
    ]
    return series[-buckets:]


def scenario_breakdown(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(func.coalesce(Transaction.scenario, "BASELINE"), func.count())
        .group_by(Transaction.scenario)
        .order_by(func.count().desc())
    ).all()
    return [(str(name), int(count)) for name, count in rows]


def network_edges(session: Session, run_id: str | None = None, limit: int = 400) -> list[tuple]:
    """Aggregate transfers into directed edges (source -> counterparty)."""
    statement = (
        select(
            Transaction.account_id,
            Transaction.counterparty_id,
            func.count(),
            func.coalesce(func.sum(Transaction.amount_minor), 0),
        )
        .where(Transaction.counterparty_id.is_not(None))
        .group_by(Transaction.account_id, Transaction.counterparty_id)
        .order_by(func.coalesce(func.sum(Transaction.amount_minor), 0).desc())
        .limit(limit)
    )
    if run_id:
        statement = statement.where(Transaction.run_id == run_id)
    return session.execute(statement).all()


# ---------------------------------------------------------------------------
# Customer-scoped reads (the banking experience)
# ---------------------------------------------------------------------------
def owned_transactions(
    session: Session,
    *,
    account_ids: list[str],
    limit: int,
    offset: int,
    account_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    direction: str | None = None,
    suspicious: bool | None = None,
    date_from=None,
    date_to=None,
) -> tuple[list[tuple[Transaction, str | None, DetectionResult | None]], int]:
    """The same triple shape as :func:`list_transactions`, restricted to a set of accounts.

    ``account_ids`` is the authorization scope: it is applied first and cannot be
    widened by any other argument, so a customer can never see another customer's
    rows even by guessing a query parameter.
    """
    statement = (
        select(Transaction, Account.label, DetectionResult)
        .join(Account, Account.id == Transaction.account_id, isouter=True)
        .join(DetectionResult, DetectionResult.transaction_id == Transaction.id, isouter=True)
    )
    counter = select(func.count(func.distinct(Transaction.id))).select_from(Transaction).join(
        DetectionResult, DetectionResult.transaction_id == Transaction.id, isouter=True
    )

    filters = [Transaction.account_id.in_(list(account_ids) or [""])]
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if status:
        filters.append(Transaction.status == status)
    if direction:
        filters.append(Transaction.direction == direction)
    if suspicious is True:
        filters.append(DetectionResult.decision.is_not(None))
        filters.append(DetectionResult.decision != "ALLOW")
    elif suspicious is False:
        filters.append(or_(DetectionResult.decision.is_(None), DetectionResult.decision == "ALLOW"))
    if date_from is not None:
        filters.append(Transaction.timestamp >= date_from)
    if date_to is not None:
        filters.append(Transaction.timestamp <= date_to)
    if search:
        # One LIKE over the fields a customer can actually see on the row.
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(Transaction.id).like(pattern),
                func.lower(func.coalesce(Transaction.description, "")).like(pattern),
                func.lower(func.coalesce(Transaction.counterparty_label, "")).like(pattern),
                func.lower(func.coalesce(Transaction.counterparty_id, "")).like(pattern),
                func.lower(Transaction.channel).like(pattern),
                func.lower(Transaction.status).like(pattern),
            )
        )

    for condition in filters:
        statement = statement.where(condition)
        counter = counter.where(condition)

    total = int(session.execute(counter).scalar() or 0)
    rows = session.execute(
        statement.order_by(Transaction.timestamp.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [(row[0], row[1], row[2]) for row in rows], total


def debits_since(session: Session, account_id: str, since) -> int:
    """Sum of outgoing settled/hold value from one account since ``since``.

    Used for the rolling daily transfer limit. Held and blocked transfers are
    counted too, because the limit exists to cap what a customer *asks* to move.
    """
    return int(
        session.execute(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.account_id == account_id,
                Transaction.direction == "DEBIT",
                Transaction.timestamp >= since,
            )
        ).scalar()
        or 0
    )


def by_idempotency_key(session: Session, key: str) -> Transaction | None:
    """The original leg of an already-accepted submission (see ``transfer_id``)."""
    if not key:
        return None
    return session.execute(
        select(Transaction)
        .where(Transaction.idempotency_key == key)
        .order_by(Transaction.timestamp)
    ).scalars().first()


def transfer_legs(session: Session, transfer_id: str) -> list[Transaction]:
    return list(
        session.execute(
            select(Transaction)
            .where(Transaction.transfer_id == transfer_id)
            .order_by(Transaction.direction.desc(), Transaction.id)
        ).scalars()
    )


def last_transaction_at(session: Session, account_ids: list[str]):
    if not account_ids:
        return None
    return session.execute(
        select(func.max(Transaction.timestamp)).where(Transaction.account_id.in_(account_ids))
    ).scalar()


def decision_counts_for_accounts(session: Session, account_ids: list[str]) -> dict[str, int]:
    if not account_ids:
        return {}
    rows = session.execute(
        select(DetectionResult.decision, func.count())
        .where(DetectionResult.account_id.in_(account_ids))
        .group_by(DetectionResult.decision)
    ).all()
    return {str(decision): int(count) for decision, count in rows}


def status_counts_by_account(
    session: Session, account_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Settlement-state counts per account, in one grouped query.

    The account cards show these individually, so a bank-wide total would be wrong
    on every card but one.
    """
    if not account_ids:
        return {}
    rows = session.execute(
        select(Transaction.account_id, Transaction.status, func.count())
        .where(Transaction.account_id.in_(list(account_ids)))
        .group_by(Transaction.account_id, Transaction.status)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for account_id, status, count in rows:
        counts.setdefault(account_id, {})[str(status)] = int(count)
    return counts


def decision_counts_by_account(
    session: Session, account_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Detection decision counts per account (the ``suspicious`` figure on a card)."""
    if not account_ids:
        return {}
    rows = session.execute(
        select(DetectionResult.account_id, DetectionResult.decision, func.count())
        .where(DetectionResult.account_id.in_(list(account_ids)))
        .group_by(DetectionResult.account_id, DetectionResult.decision)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for account_id, decision, count in rows:
        counts.setdefault(account_id, {})[str(decision)] = int(count)
    return counts


def pending_outgoing_total(session: Session, account_ids: list[str]) -> int:
    """Outgoing value awaiting a decision.

    Money in this state has been *asked for* but not deducted, so the balance
    summary reports it separately rather than blending it into one figure.
    """
    if not account_ids:
        return 0
    return int(
        session.execute(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.account_id.in_(list(account_ids)),
                Transaction.direction != "CREDIT",
                Transaction.status == "PENDING_REVIEW",
            )
        ).scalar()
        or 0
    )


def suspicious_for_accounts(session: Session, account_ids: list[str], limit: int = 10):
    """Most recent non-ALLOW detections on the customer's own accounts."""
    if not account_ids:
        return []
    return session.execute(
        select(Transaction, DetectionResult, Account.label)
        .join(DetectionResult, DetectionResult.transaction_id == Transaction.id)
        .join(Account, Account.id == Transaction.account_id, isouter=True)
        .where(
            Transaction.account_id.in_(account_ids),
            DetectionResult.decision != "ALLOW",
        )
        .order_by(Transaction.timestamp.desc())
        .limit(limit)
    ).all()
