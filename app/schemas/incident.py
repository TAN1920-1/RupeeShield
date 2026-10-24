"""Incident, replay and financial-impact schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime


class IncidentEventOut(ApiModel):
    id: str
    seq: int
    timestamp: UtcDateTime
    kind: str = Field(description="SECURITY_EVENT | TRANSACTION | DETECTION | DEFENSE")
    ref_id: str | None = None
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FinancialImpactOut(ApiModel):
    id: str
    run_id: str | None = None
    incident_id: str | None = None
    exposure_minor: int
    exposure: float
    unauthorized_minor: int
    unauthorized: float
    prevented_minor: int
    prevented: float
    interrupted_minor: int = Field(
        default=0,
        description="Value of legitimate transactions the defense blocked or held: the false-positive "
                    "cost in rupees. Never a realized loss and never counted in prevented value.",
    )
    interrupted: float = 0.0
    disruption_cost_minor: int
    disruption_cost: float
    recovery_cost_minor: int
    recovery_cost: float
    net_impact_minor: int
    net_impact: float
    assumptions: list[str] = Field(default_factory=list)


class IncidentOut(ApiModel):
    id: str
    run_id: str | None = None
    scenario: str
    scenario_name: str | None = None
    primary_account_id: str
    primary_account_label: str | None = None
    title: str
    summary: str = ""
    severity: str
    risk_level: str
    status: str
    opened_at: UtcDateTime
    closed_at: OptionalUtcDateTime = None
    event_count: int = 0
    transaction_count: int = 0
    suspected_loss_minor: int = 0
    suspected_loss: float = 0.0


class IncidentDetailOut(IncidentOut):
    notes: str = ""
    timeline: list[IncidentEventOut] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)
    detections: list[dict[str, Any]] = Field(default_factory=list)
    defense_actions: list[dict[str, Any]] = Field(default_factory=list)
    financial_impact: FinancialImpactOut | None = None
    triggered_rules: list[str] = Field(default_factory=list)


class IncidentStatusRequest(ApiModel):
    status: str = Field(..., description="OPEN | INVESTIGATING | CONTAINED | CLOSED")


class IncidentNoteRequest(ApiModel):
    note: str = Field(..., min_length=1, max_length=2000)


class ReplayStep(ApiModel):
    seq: int
    timestamp: UtcDateTime
    kind: str
    description: str
    decision: str | None = None
    risk_score: float | None = None
    matches_original: bool = True


class ReplayOut(ApiModel):
    replay_id: str
    incident_id: str
    started_at: UtcDateTime
    finished_at: UtcDateTime
    steps: list[ReplayStep]
    steps_replayed: int
    divergence_count: int = Field(
        description="Steps whose recomputed decision differs from the recorded one. "
                    "Non-zero means the thresholds or models changed since the incident was recorded."
    )
    mutates_history: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)


class DefenseActionOut(ApiModel):
    id: str
    run_id: str | None = None
    incident_id: str | None = None
    account_id: str
    transaction_id: str | None = None
    action_type: str
    timestamp: UtcDateTime
    reason: str = ""
    amount_minor: int = 0
    amount: float = 0.0
