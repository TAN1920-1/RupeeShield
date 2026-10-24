"""Overview dashboard schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, NamedCount, Provenance, TimeSeriesPoint, UtcDateTime


class LatencyStatsOut(ApiModel):
    samples: int = 0
    mean_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None


class OverviewStats(ApiModel):
    """Every field is computed from stored rows — none of it is hard-coded."""

    total_simulated_transactions: int
    total_value_minor: int
    total_value: float
    suspicious_transactions: int
    blocked_transactions: int
    flagged_transactions: int
    open_incidents: int
    total_incidents: int
    value_at_risk_minor: int
    value_at_risk: float
    loss_prevented_minor: int
    loss_prevented: float
    realized_loss_minor: int
    realized_loss: float
    detection_latency: LatencyStatsOut
    active_runs: int
    completed_runs: int
    observed_dataset: dict[str, Any] | None = Field(
        default=None, description="Statistics of the ML dataset, kept separate from simulation output."
    )
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    generated_at: UtcDateTime


class OverviewCharts(ApiModel):
    volume_over_time: list[TimeSeriesPoint]
    alerts_by_severity: list[NamedCount]
    decisions_breakdown: list[NamedCount]
    scenario_breakdown: list[NamedCount]
    top_risk_accounts: list[dict[str, Any]]


class OverviewPayload(ApiModel):
    stats: OverviewStats
    charts: OverviewCharts
    recent_events: list[dict[str, Any]]
    recent_incidents: list[dict[str, Any]]
    empty_states: list[str] = Field(
        default_factory=list,
        description="Human-readable notes explaining any metric that is currently zero because "
                    "no data exists yet (rather than because nothing happened).",
    )
