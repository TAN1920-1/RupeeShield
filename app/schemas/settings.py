"""Settings, documentation and audit-trail schemas."""
from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, UtcDateTime


class ThresholdOut(ApiModel):
    block_threshold: float
    flag_threshold: float
    rule_velocity_10min: int
    rule_velocity_1h: int
    rule_amount_zscore: float
    rule_amount_over_avg: float
    failed_login_burst: int
    failed_login_window_minutes: int
    correlation_lookback_minutes: int
    new_device_high_value_minor: int
    coordinated_fanout_beneficiaries: int


class SettingsOut(ApiModel):
    app_name: str
    app_version: str
    environment: str
    database_backend: str
    model_dir: str
    data_dir: str
    default_seed: int
    max_simulation_events: int
    rate_limit_requests_per_minute: int
    enable_api_training: bool
    cors_origins: list[str]
    thresholds: ThresholdOut
    secret_notes: list[str] = Field(
        default_factory=list,
        description="Explicit statement of which secrets/config the app does and does not need.",
    )


class ThresholdPatch(ApiModel):
    block_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    flag_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    rule_velocity_10min: int | None = Field(default=None, ge=1, le=100)
    rule_velocity_1h: int | None = Field(default=None, ge=1, le=500)
    rule_amount_zscore: float | None = Field(default=None, ge=0.0, le=50.0)
    rule_amount_over_avg: float | None = Field(default=None, ge=0.0, le=100.0)
    failed_login_burst: int | None = Field(default=None, ge=1, le=100)
    failed_login_window_minutes: int | None = Field(default=None, ge=1, le=1440)
    correlation_lookback_minutes: int | None = Field(default=None, ge=1, le=1440)
    new_device_high_value_minor: int | None = Field(default=None, ge=0)
    coordinated_fanout_beneficiaries: int | None = Field(default=None, ge=1, le=50)

    @field_validator("flag_threshold")
    @classmethod
    def _placeholder(cls, value: float | None) -> float | None:
        return value


class SettingsPatch(ApiModel):
    thresholds: ThresholdPatch


class AuditLogOut(ApiModel):
    id: str
    timestamp: UtcDateTime
    actor: str
    action: str
    entity_type: str
    entity_id: str | None = None
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)


class DocumentationSection(ApiModel):
    key: str
    title: str
    body: str
    bullets: list[str] = Field(default_factory=list)


class DocumentationOut(ApiModel):
    title: str
    version: str
    sections: list[DocumentationSection]
    formulas: list[dict[str, Any]] = Field(
        default_factory=list, description="Financial-impact formulas and their assumptions."
    )
    dataset_provenance: dict[str, Any] = Field(default_factory=dict)
    ethics: list[str] = Field(default_factory=list)
    troubleshooting: list[dict[str, str]] = Field(default_factory=list)
    commands: list[dict[str, str]] = Field(default_factory=list)
