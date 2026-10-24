"""Runtime-editable detection and correlation thresholds.

The thresholds are the single most interesting knob in this project: the Defense
Comparison Lab exists to show that changing them changes what an attack costs. So
they must be editable at runtime rather than requiring a source edit and a restart.

Implementation: the defaults live in :mod:`app.core.config` (and can be set through
environment variables). Overrides applied through the API are validated, written to
``data/thresholds.json`` and applied to the in-memory settings object, then
re-applied on the next startup. That file contains no secrets and is gitignored.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.config import ThresholdSettings, settings
from app.core.logging import get_logger
from app.security.validation import ValidationError

logger = get_logger(__name__)

OVERRIDE_PATH = settings.data_dir / "thresholds.json"

SECRET_NOTES = [
    "No API keys, tokens or cloud credentials are required anywhere in this project.",
    "The database is a local SQLite file; there are no database credentials to configure.",
    "Configuration is non-sensitive (paths and thresholds) and lives in .env / app/core/config.py.",
    "Never commit a real .env file: .gitignore excludes it and only .env.example is tracked.",
]


def _read_overrides() -> dict[str, Any]:
    if not OVERRIDE_PATH.exists():
        return {}
    try:
        with open(OVERRIDE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable threshold overrides at %s (%s)", OVERRIDE_PATH, exc)
        return {}


def load_overrides() -> list[str]:
    """Apply any persisted overrides at startup. Returns the keys that were applied."""
    overrides = _read_overrides()
    if not overrides:
        return []
    applied: list[str] = []
    for key, value in overrides.items():
        if hasattr(settings.thresholds, key):
            try:
                setattr(settings.thresholds, key, value)
                applied.append(key)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
                logger.warning("Could not apply threshold override %s=%s (%s)", key, value, exc)
    if applied:
        logger.info("Applied %d threshold override(s) from %s", len(applied), OVERRIDE_PATH)
    return applied


def current_thresholds() -> ThresholdSettings:
    return settings.thresholds


def thresholds_as_dict() -> dict[str, Any]:
    return settings.thresholds.model_dump()


def apply_threshold_patch(patch: dict[str, Any]) -> ThresholdSettings:
    """Validate and apply a partial threshold update, then persist the overlay.

    Raises :class:`ValidationError` (mapped to HTTP 422) when the patch would leave
    the configuration in a contradictory state, for example a flag threshold above
    the block threshold — which would make ``FLAG`` unreachable.
    """
    cleaned = {key: value for key, value in patch.items() if value is not None}
    if not cleaned:
        raise ValidationError("No threshold values supplied.")

    unknown = sorted(set(cleaned) - set(ThresholdSettings.model_fields))
    if unknown:
        raise ValidationError(f"Unknown threshold field(s): {', '.join(unknown)}.")

    candidate = settings.thresholds.model_dump()
    candidate.update(cleaned)

    try:
        validated = ThresholdSettings(**candidate)
    except ValueError as exc:
        raise ValidationError(f"Invalid threshold value: {exc}") from exc

    if validated.flag_threshold >= validated.block_threshold:
        raise ValidationError(
            "flag_threshold must be lower than block_threshold, otherwise no transaction "
            "can be flagged for review without being blocked outright."
        )

    for key, value in validated.model_dump().items():
        setattr(settings.thresholds, key, value)

    _persist(cleaned)
    return settings.thresholds


def reset_overrides() -> ThresholdSettings:
    """Drop persisted overrides and fall back to the configured defaults."""
    if OVERRIDE_PATH.exists():
        OVERRIDE_PATH.unlink()
    defaults = ThresholdSettings()
    for key, value in defaults.model_dump().items():
        setattr(settings.thresholds, key, value)
    return settings.thresholds


def _persist(patch: dict[str, Any]) -> None:
    merged = {**_read_overrides(), **patch}
    try:
        OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OVERRIDE_PATH, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, sort_keys=True)
    except OSError as exc:  # pragma: no cover - defensive
        logger.error("Could not persist threshold overrides to %s (%s)", OVERRIDE_PATH, exc)
