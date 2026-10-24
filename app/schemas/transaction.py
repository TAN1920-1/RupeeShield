"""Transaction, scoring and account schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime

CHANNELS = ["CARD_PRESENT", "CARD_NOT_PRESENT", "UPI_WALLET", "WIRE_TRANSFER", "CASH", "TRANSFER"]


class ScoreRequest(ApiModel):
    """A single transaction to score (the original real-time endpoint contract)."""

    user_id: str = Field(..., min_length=1, max_length=40, examples=["A000042"])
    amount: float = Field(..., gt=0, le=1_000_000_000, examples=[1500.0],
                          description="Major units (INR). Must be positive.")
    timestamp: OptionalUtcDateTime = Field(default=None, description="Defaults to now (UTC).")
    channel: str = Field(..., min_length=1, max_length=24, examples=["UPI_WALLET"])
    country: str = Field(..., min_length=2, max_length=4, examples=["IN"])
    merchant_id: str = Field(..., min_length=1, max_length=40, examples=["M4821"])
    device_id: str = Field(..., min_length=1, max_length=40, examples=["D1234"])

    @field_validator("country")
    @classmethod
    def normalise_country(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("channel")
    @classmethod
    def normalise_channel(cls, value: str) -> str:
        return value.strip().upper()


class ShapFeature(ApiModel):
    feature: str
    value: float
    shap_contribution: float


class ScoreResponse(ApiModel):
    transaction_id: str
    fraud_risk_score: float
    decision: str
    ml_supervised_score: float | None = None
    ml_anomaly_score: float | None = None
    rule_heuristic_score: float
    reasons: list[str]
    rules_triggered: list[str] = Field(default_factory=list)
    top_shap_features: list[ShapFeature] = Field(default_factory=list)
    latency_ms: float


class TransactionOut(ApiModel):
    id: str
    run_id: str | None = None
    account_id: str
    account_label: str | None = None
    counterparty_id: str | None = None
    counterparty_label: str | None = None
    timestamp: UtcDateTime
    amount_minor: int
    amount: float = Field(description="Major units (INR), display only.")
    currency: str
    channel: str
    country: str
    merchant_id: str
    device_id: str
    direction: str
    status: str
    scenario: str | None = None
    risk_score: float | None = None
    decision: str | None = None
    is_fraud: int | None = Field(default=None, description="Synthetic ground truth label.")


class TransactionDetailOut(TransactionOut):
    detection: dict[str, Any] | None = None
    explanation: list[ShapFeature] = Field(default_factory=list)
    security_events: list[dict[str, Any]] = Field(default_factory=list)
    neighborhood: list[dict[str, Any]] = Field(default_factory=list)


class AccountOut(ApiModel):
    id: str
    label: str
    home_country: str
    preferred_channel: str
    account_age_days: int
    balance_minor: int
    balance: float
    opening_balance_minor: int
    risk_status: str
    is_synthetic: bool = True
    transaction_count: int = 0
