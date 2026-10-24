"""Attack-simulation and defense-comparison schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime

SCENARIO_KEYS = {
    "ACCOUNT_TAKEOVER",
    "CREDENTIAL_STUFFING",
    "PAYMENT_MANIPULATION",
    "SUSPICIOUS_TRANSFER",
}
DEFENSE_CONFIG_KEYS = {"NONE", "RULE_BASED", "COMBINED"}


class ScenarioInfo(ApiModel):
    key: str
    name: str
    description: str
    objective: str
    risk_level: str = Field(description="LOW | MEDIUM | HIGH — the scenario's designed severity.")
    default_events: int
    signature: list[str] = Field(description="Behavioural signals this scenario is designed to create.")


class DefenseConfigInfo(ApiModel):
    key: str
    name: str
    description: str
    uses_ml: bool
    uses_correlation: bool


class SimulationRunRequest(ApiModel):
    scenario: str = Field(..., description="One of the SCENARIO_KEYS.")
    n_events: int = Field(default=40, ge=5, le=1500)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    defense_config: str = Field(default="COMBINED")
    mode: str = Field(default="batch", description="batch (synchronous) or stream (paced, for Live Monitoring)")
    pace_per_second: float = Field(default=12.0, gt=0.0, le=200.0,
                                   description="Events per second when mode == 'stream'.")

    @property
    def is_streaming(self) -> bool:
        return self.mode == "stream"


class SimulationRunOut(ApiModel):
    id: str
    scenario: str
    scenario_name: str | None = None
    defense_config: str
    seed: int
    requested_events: int
    generated_events: int
    status: str
    mode: str
    started_at: OptionalUtcDateTime = None
    finished_at: OptionalUtcDateTime = None
    created_at: UtcDateTime
    error: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class SimulationRunDetail(SimulationRunOut):
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    detection_summary: dict[str, Any] = Field(default_factory=dict)
    financial_impact: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)


class DefenseComparisonRequest(ApiModel):
    scenario: str
    n_events: int = Field(default=40, ge=5, le=800)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class DefenseConfigResult(ApiModel):
    config: str
    name: str
    detected_events: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    false_positive_rate: float | None
    detection_latency_ms: float | None
    prevented_count: int
    prevented_value_minor: int
    prevented_value: float
    realized_loss_minor: int
    realized_loss: float
    net_impact_minor: int
    net_impact: float
    processing_ms: float
    run_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class DefenseComparisonOut(ApiModel):
    id: str
    scenario: str
    scenario_name: str | None = None
    seed: int
    n_events: int
    label_source: str = Field(description="Where ground truth came from (synthetic generator).")
    results: list[DefenseConfigResult]
    same_inputs: bool = Field(
        default=True,
        description="True when all configurations were given identical accounts, balances and event sequences.",
    )
    conclusion: list[str] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="Caveats about this specific comparison, e.g. a configuration that could not run "
                    "as requested because the trained ML artefacts are missing.",
    )
    created_at: UtcDateTime
