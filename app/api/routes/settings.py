"""Runtime settings (Module K).

Reads the live configuration the detection engine is using and lets the operator
change the decision and correlation thresholds without a restart. A patch is
validated before it is applied, so a contradictory configuration (a flag threshold
above the block threshold) is rejected instead of silently making FLAG unreachable.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.core.config import settings as app_settings
from app.schemas.settings import SettingsOut, SettingsPatch
from app.security.audit import record_audit
from app.services.settings_service import SECRET_NOTES, apply_threshold_patch, thresholds_as_dict

router = APIRouter(prefix="/settings", tags=["settings"])


def _payload() -> dict:
    return {
        "app_name": app_settings.app_name,
        "app_version": app_settings.app_version,
        "environment": app_settings.environment,
        "database_backend": app_settings.database_url.split(":", 1)[0],
        "model_dir": str(app_settings.model_dir),
        "data_dir": str(app_settings.data_dir),
        "default_seed": app_settings.default_seed,
        "max_simulation_events": app_settings.max_simulation_events,
        "rate_limit_requests_per_minute": app_settings.rate_limit_requests_per_minute,
        "enable_api_training": app_settings.enable_api_training,
        "cors_origins": list(app_settings.cors_origins),
        "thresholds": thresholds_as_dict(),
        "secret_notes": list(SECRET_NOTES),
    }


@router.get("", response_model=SettingsOut, summary="Current configuration and thresholds")
def read_settings() -> dict:
    return _payload()


@router.patch(
    "",
    response_model=SettingsOut,
    summary="Update detection / correlation thresholds",
    description="Validated, applied to the running process immediately and persisted to a "
                "gitignored overlay so the change survives a restart. An audit row records it.",
)
def update_settings(payload: SettingsPatch, session: SessionDep) -> dict:
    before = thresholds_as_dict()
    apply_threshold_patch(payload.thresholds.model_dump())
    after = thresholds_as_dict()
    changed = {key: value for key, value in after.items() if before.get(key) != value}
    record_audit(
        session,
        action="THRESHOLDS_UPDATED",
        entity_type="settings",
        entity_id="thresholds",
        details={"changed": changed},
    )
    return _payload()


__all__ = ["router"]
