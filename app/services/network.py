"""Financial network construction.

The graph is built **here**, from query results, and is rendered by the browser.
Keeping construction and rendering apart means the graph can be asserted in a unit
test (nodes, edges, direction, amounts) without a browser, and a different renderer
could be swapped in later without touching the queries.

Wording rule that this module enforces: an edge is marked ``suspicious`` when a
detector did not allow at least one transaction on it. That is an *observation
about model output*, not evidence of wrongdoing, and an account is only ever given
one of three labels — ``NORMAL``, ``ELEVATED_SIMULATED_RISK`` or
``FLAGGED_FOR_INVESTIGATION``. The platform never asserts that an account belongs
to a criminal.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import repositories as repo
from app.db.models import Account
from app.utils.money import minor_units_to_float
from app.utils.timeutils import utcnow

DEFAULT_NODE_LIMIT = 150
MAX_NODE_LIMIT = 400

#: Risk labels, worst last. Used to pick the strongest label a node has been given.
_RISK_RANK = {"NORMAL": 0, "ELEVATED_SIMULATED_RISK": 1, "FLAGGED_FOR_INVESTIGATION": 2}

LEGEND = [
    "Node size and label come from the simulated account roster; labels are neutral.",
    "Arrows point from the paying account to the beneficiary, so direction is the flow of funds.",
    "A red edge means at least one transaction on it was flagged or blocked by a detector.",
    "FLAGGED_FOR_INVESTIGATION and ELEVATED_SIMULATED_RISK are review states produced by the "
    "simulation, not assertions of wrongdoing.",
    "Only transactions between the simulated accounts of a run are drawn; external counterparties "
    "appear as a single EXTERNAL node.",
]


def _risk_label(status: str | None) -> str:
    return status if status in _RISK_RANK else "NORMAL"


def _aggregate_edges(rows) -> dict[tuple[str, str], dict]:
    """Collapse ``(source, target, count, amount)`` rows into one entry per edge."""
    edges: dict[tuple[str, str], dict] = {}
    for source, target, count, amount in rows:
        if not source or not target or source == target:
            continue
        key = (str(source), str(target))
        existing = edges.get(key)
        if existing is None:
            edges[key] = {"count": int(count), "amount_minor": int(amount or 0)}
        else:
            existing["count"] += int(count)
            existing["amount_minor"] += int(amount or 0)
    return edges


def _edges_for_incident(session: Session, incident_id: str) -> dict[tuple[str, str], dict]:
    """Aggregate the incident's own transactions in Python.

    An incident is small (a handful of transactions), so a Python aggregation is
    clearer than a second SQL variant of the same grouping.
    """
    transactions = repo.incidents.transactions_for_incident(session, incident_id)
    return _aggregate_edges(
        [
            (txn.account_id, txn.counterparty_id, 1, txn.amount_minor)
            for txn in transactions
            if txn.counterparty_id
        ]
    )


def build_graph(
    session: Session,
    *,
    run_id: str | None = None,
    incident_id: str | None = None,
    limit: int = DEFAULT_NODE_LIMIT,
) -> dict:
    """Build the node/edge graph for a whole run, or for one incident."""
    node_limit = max(1, min(int(limit), MAX_NODE_LIMIT))

    primary_account_id: str | None = None
    if incident_id:
        incident = repo.incidents.get_incident(session, incident_id)
        if incident is None:
            raise LookupError(f"No incident with id {incident_id}.")
        primary_account_id = incident.primary_account_id
        run_id = run_id or incident.run_id
        edges = _edges_for_incident(session, incident_id)
        truncated = False
    else:
        raw = repo.transactions.network_edges(session, run_id=run_id, limit=node_limit)
        edges = _aggregate_edges(raw)
        truncated = len(raw) >= node_limit

    flagged = repo.detections.flagged_edges(session, run_id)

    node_ids = sorted({node for pair in edges for node in pair})
    truncated = truncated or len(node_ids) > node_limit
    node_ids = node_ids[:node_limit]

    # Drop edges that reference a node beyond the cap so the graph stays consistent.
    allowed = set(node_ids)
    edges = {pair: data for pair, data in edges.items() if pair[0] in allowed and pair[1] in allowed}

    accounts = repo.accounts.get_accounts(session, node_ids)
    counts = repo.accounts.transaction_counts(session, node_ids)

    outgoing: dict[str, int] = {}
    incoming: dict[str, int] = {}
    suspicious_connections: dict[str, int] = {}
    edge_payload: list[dict] = []

    for (source, target), data in sorted(edges.items(), key=lambda item: item[1]["amount_minor"], reverse=True):
        risk = flagged.get((source, target))
        suspicious = risk is not None
        outgoing[source] = outgoing.get(source, 0) + data["amount_minor"]
        incoming[target] = incoming.get(target, 0) + data["amount_minor"]
        if suspicious:
            suspicious_connections[source] = suspicious_connections.get(source, 0) + 1
            suspicious_connections[target] = suspicious_connections.get(target, 0) + 1
        edge_payload.append(
            {
                "id": f"E-{source}->{target}",
                "source": source,
                "target": target,
                "transaction_count": data["count"],
                "amount_minor": data["amount_minor"],
                "amount": minor_units_to_float(data["amount_minor"]),
                "suspicious": suspicious,
                "max_risk_score": None if risk is None else round(float(risk), 4),
            }
        )

    nodes = [
        _node_payload(
            account=accounts.get(node_id),
            node_id=node_id,
            transaction_count=counts.get(node_id, 0),
            total_out=outgoing.get(node_id, 0),
            total_in=incoming.get(node_id, 0),
            suspicious_connections=suspicious_connections.get(node_id, 0),
            is_primary=node_id == primary_account_id,
        )
        for node_id in node_ids
    ]

    return {
        "run_id": run_id,
        "incident_id": incident_id,
        "nodes": nodes,
        "edges": edge_payload,
        "truncated": truncated,
        "node_limit": node_limit,
        "generated_at": utcnow(),
        "legend": list(LEGEND),
    }


def _node_payload(
    *,
    account: Account | None,
    node_id: str,
    transaction_count: int,
    total_out: int,
    total_in: int,
    suspicious_connections: int,
    is_primary: bool,
) -> dict:
    balance_minor = int(account.balance_minor) if account is not None else 0
    return {
        "id": node_id,
        "label": account.label if account is not None else node_id,
        "risk_status": _risk_label(account.risk_status if account is not None else None),
        "is_primary": is_primary,
        "balance_minor": balance_minor,
        "balance": minor_units_to_float(balance_minor),
        "transaction_count": int(transaction_count),
        "total_out_minor": int(total_out),
        "total_in_minor": int(total_in),
        "suspicious_connection_count": int(suspicious_connections),
    }


def account_events(session: Session, account_id: str) -> dict:
    """Everything recorded about one account, phrased as leads rather than verdicts."""
    account = repo.accounts.get_account(session, account_id)
    if account is None:
        raise LookupError(f"No account with id {account_id}.")

    transactions = repo.transactions.account_history(session, account_id, limit=100)
    security_events = repo.detections.security_events_for_account(session, account_id, limit=60)
    incidents = repo.incidents.incidents_for_account(session, account_id, limit=20)
    detections = repo.detections.detections_for_account(session, account_id, limit=200)

    out_total = sum(txn.amount_minor for txn in transactions if txn.direction == "DEBIT")
    in_total = sum(txn.amount_minor for txn in transactions if txn.direction == "CREDIT")
    devices = {txn.device_id for txn in transactions if txn.device_id}
    blocked = sum(1 for txn in transactions if txn.status == "BLOCKED")
    held = sum(1 for txn in transactions if txn.status == "PENDING_REVIEW")
    highest = max((detection.fraud_risk_score for detection in detections), default=0.0)

    node = _node_payload(
        account=account,
        node_id=account_id,
        transaction_count=len(transactions),
        total_out=out_total,
        total_in=in_total,
        suspicious_connections=sum(
            1 for detection in detections if detection.decision != "ALLOW"
        ),
        is_primary=False,
    )

    observations: list[str] = []
    if security_events:
        worst = max(
            (event.severity for event in security_events),
            key=lambda severity: ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"].index(severity)
            if severity in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
            else 0,
        )
        observations.append(
            f"{len(security_events)} security event(s) recorded for this account; "
            f"the highest severity seen was {worst}."
        )
    else:
        observations.append("No security telemetry has been recorded for this account.")
    if blocked or held:
        observations.append(
            f"{blocked} transaction(s) were blocked and {held} held for review, so "
            f"the value involved never settled while the defense was active."
        )
    else:
        observations.append("No transaction on this account was blocked or held by a defense.")
    if len(devices) > 1:
        observations.append(
            f"{len(devices)} distinct devices appear in this account's recorded activity; "
            "worth checking whether the customer recognises all of them."
        )
    observations.append(
        f"The highest model risk score seen on this account is {round(float(highest), 2)} "
        "(a model observation, not a finding)."
    )
    observations.append(
        "This is a synthetic account inside the simulation; no real customer data is involved."
    )

    return {
        "account": node,
        "security_events": [
            {
                "id": event.id,
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "severity": event.severity,
                "country": event.country,
                "device_id": event.device_id,
                "details": dict(event.details or {}),
            }
            for event in security_events
        ],
        "transactions": [
            {
                "id": txn.id,
                "timestamp": txn.timestamp,
                "amount_minor": int(txn.amount_minor),
                "amount": minor_units_to_float(txn.amount_minor),
                "direction": txn.direction,
                "channel": txn.channel,
                "status": txn.status,
                "device_id": txn.device_id,
                "counterparty_label": txn.counterparty_label,
                "is_fraud": txn.is_fraud,
            }
            for txn in transactions
        ],
        "incidents": [
            {
                "id": incident.id,
                "title": incident.title,
                "severity": incident.severity,
                "status": incident.status,
                "opened_at": incident.opened_at,
            }
            for incident in incidents
        ],
        "observations": observations,
    }


__all__ = ["DEFAULT_NODE_LIMIT", "MAX_NODE_LIMIT", "account_events", "build_graph"]
