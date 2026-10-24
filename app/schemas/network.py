"""Financial-network graph schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, UtcDateTime


class GraphNode(ApiModel):
    id: str
    label: str
    risk_status: str = Field(
        description="NORMAL | ELEVATED_SIMULATED_RISK | FLAGGED_FOR_INVESTIGATION. "
                    "Never a criminal accusation — an account is only ever flagged for review."
    )
    is_primary: bool = False
    balance_minor: int = 0
    balance: float = 0.0
    transaction_count: int = 0
    total_out_minor: int = 0
    total_in_minor: int = 0
    suspicious_connection_count: int = 0


class GraphEdge(ApiModel):
    id: str
    source: str
    target: str
    transaction_count: int
    amount_minor: int
    amount: float
    suspicious: bool = Field(
        default=False,
        description="True when at least one transaction on this edge was flagged or blocked.",
    )
    max_risk_score: float | None = None


class NetworkGraphOut(ApiModel):
    run_id: str | None = None
    incident_id: str | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
    node_limit: int
    generated_at: UtcDateTime
    legend: list[str] = Field(default_factory=list)


class AccountEventsOut(ApiModel):
    account: GraphNode
    security_events: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[str] = Field(
        default_factory=list,
        description="Plain-language observations, phrased as leads to investigate rather than conclusions.",
    )
