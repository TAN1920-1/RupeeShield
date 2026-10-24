"""Detection-result, security-event and monitoring schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, UtcDateTime


class DetectionOut(ApiModel):
    id: str
    run_id: str | None = None
    incident_id: str | None = None
    account_id: str
    transaction_id: str | None = None
    timestamp: UtcDateTime
    detection_source: str
    fraud_risk_score: float
    decision: str
    ml_supervised_score: float | None = None
    ml_anomaly_score: float | None = None
    rule_heuristic_score: float | None = None
    correlation_score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    correlated_event_ids: list[str] = Field(default_factory=list)
    top_features: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    ground_truth_fraud: int | None = None


class SecurityEventOut(ApiModel):
    id: str
    run_id: str | None = None
    incident_id: str | None = None
    account_id: str
    account_label: str | None = None
    timestamp: UtcDateTime
    event_type: str
    severity: str
    country: str | None = None
    device_id: str | None = None
    source_ip_hash: str | None = None
    related_transaction_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MonitoringEventOut(ApiModel):
    """One row of the live feed.

    ``response`` is the defensive status of the transaction (for example
    ``BLOCKED`` or ``SETTLED``); ``decision`` is what the detector concluded.
    """

    sequence: int
    timestamp: UtcDateTime
    run_id: str | None = None
    event_type: str
    account_id: str
    account_label: str | None = None
    amount: float | None = None
    amount_minor: int | None = None
    decision: str | None = None
    risk_score: float | None = None
    response: str
    detail: str = ""
    source: str = Field(default="simulation", description="Always the simulation engine or backend data.")


class MonitoringPage(ApiModel):
    events: list[MonitoringEventOut]
    total: int
    run_id: str | None = None
    generated_at: UtcDateTime
