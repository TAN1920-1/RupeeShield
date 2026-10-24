"""ML model evaluation schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, UtcDateTime


class ModelArtifactOut(ApiModel):
    name: str
    path: str
    exists: bool
    size_bytes: int | None = None


class ModelInfoOut(ApiModel):
    models_available: bool
    version: str | None = None
    trained_at: str | None = None
    feature_order: list[str] = Field(default_factory=list)
    artifacts: list[ModelArtifactOut] = Field(default_factory=list)
    calibration: dict[str, Any] = Field(default_factory=dict)
    registered_versions: list[dict[str, Any]] = Field(default_factory=list)
    setup_instructions: list[str] = Field(default_factory=list)
    shap_available: bool = False
    notes: list[str] = Field(default_factory=list)


class MetricBlock(ApiModel):
    """One evaluation slice.

    ``split`` always states which data produced the numbers: train, validation,
    test or simulation. A reader must never have to guess.
    """

    key: str
    label: str
    split: str
    description: str
    samples: int | None = None
    threshold: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    pr_auc: float | None = None
    roc_auc: float | None = None
    false_positive_rate: float | None = None
    accuracy: float | None = None
    confusion: dict[str, Any] | None = None
    class_balance: dict[str, Any] | None = None
    note: str | None = None


class ModelEvaluationOut(ApiModel):
    available: bool
    model_info: ModelInfoOut
    metrics: list[MetricBlock] = Field(default_factory=list)
    pr_curves: dict[str, Any] = Field(default_factory=dict)
    feature_importance: list[dict[str, Any]] = Field(default_factory=list)
    class_balance: dict[str, Any] = Field(default_factory=dict)
    dataset: dict[str, Any] = Field(default_factory=dict)
    split_strategy: str = ""
    latency: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    generated_at: UtcDateTime


class TrainRequest(ApiModel):
    n_users: int = Field(default=400, ge=50, le=4000)
    n_transactions: int = Field(default=30_000, ge=2000, le=300_000)
    fraud_rate: float = Field(default=0.02, gt=0.0, lt=0.5)
    seed: int = Field(default=42, ge=0)


class TrainResultOut(ApiModel):
    status: str
    message: str
    regenerate_dataset: bool
    metrics: dict[str, Any] = Field(default_factory=dict)


class FeatureImportanceItem(ApiModel):
    feature: str
    importance: float


class FeatureImportanceOut(ApiModel):
    items: list[FeatureImportanceItem] = Field(default_factory=list)
    total: int = 0


class HealthOut(ApiModel):
    """Liveness probe. Reports what the process actually has loaded."""

    status: str = "ok"
    tracked_accounts: int = 0
    models_loaded: bool = False
