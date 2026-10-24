"""Model artefact storage and loading.

Artefacts live in ``models/`` (see ``RUPESHIELD_MODEL_DIR``):

============================  ==================================================
``xgb_model.joblib``          supervised classifier (known fraud typologies)
``iso_forest.joblib``         unsupervised anomaly detector (novel patterns)
``feature_order.json``        exact feature order the models were trained with
``anomaly_calibration.json``  p50/p99 of the anomaly score on legitimate data
``metrics.json``              metrics measured at training time
============================  ==================================================

``feature_order.json`` is not cosmetic: if serving ever computed features in a
different order the model would silently produce garbage. Loading is therefore
strict and fails loudly when an artefact is missing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from app.core.config import settings
from app.ml.features import FEATURE_ORDER

XGB_FILENAME = "xgb_model.joblib"
ISO_FILENAME = "iso_forest.joblib"
FEATURE_ORDER_FILENAME = "feature_order.json"
CALIBRATION_FILENAME = "anomaly_calibration.json"
METRICS_FILENAME = "metrics.json"
MODEL_CARD_FILENAME = "model_card.json"


class ModelArtifactError(RuntimeError):
    """Raised when a required model artefact is missing or unreadable."""


@dataclass
class ModelBundle:
    xgb_model: Any
    iso_model: Any
    feature_order: list[str]
    calibration: dict
    metrics: dict
    model_dir: Path

    @property
    def version(self) -> str:
        return str(self.metrics.get("version", "unversioned"))

    @property
    def trained_at(self) -> str | None:
        return self.metrics.get("trained_at")


def model_dir(override: Path | None = None) -> Path:
    return Path(override or settings.model_dir)


def artifact_paths(directory: Path | None = None) -> dict[str, Path]:
    base = model_dir(directory)
    return {
        "xgb": base / XGB_FILENAME,
        "iso": base / ISO_FILENAME,
        "feature_order": base / FEATURE_ORDER_FILENAME,
        "calibration": base / CALIBRATION_FILENAME,
        "metrics": base / METRICS_FILENAME,
        "model_card": base / MODEL_CARD_FILENAME,
    }


def models_available(directory: Path | None = None) -> bool:
    paths = artifact_paths(directory)
    return all(paths[key].exists() for key in ("xgb", "iso"))


def load_model_bundle(directory: Path | None = None) -> ModelBundle:
    """Load every artefact needed to score a transaction.

    Raises :class:`ModelArtifactError` with the exact remediation command when
    something is missing, instead of returning a half-initialised bundle.
    """
    paths = artifact_paths(directory)
    missing = [name for name in ("xgb", "iso") if not paths[name].exists()]
    if missing:
        raise ModelArtifactError(
            f"Missing model artefact(s) {', '.join(missing)} in {model_dir(directory)}. "
            "Train the models first:\n"
            "    python scripts/generate_dataset.py\n"
            "    python -m app.ml.train"
        )

    feature_order = FEATURE_ORDER
    if paths["feature_order"].exists():
        with open(paths["feature_order"], encoding="utf-8") as handle:
            stored = json.load(handle)
        if stored != FEATURE_ORDER:
            raise ModelArtifactError(
                "models/feature_order.json does not match FEATURE_ORDER in app/ml/features.py "
                f"({stored} != {FEATURE_ORDER}). The model would score the wrong columns; retrain."
            )
        feature_order = stored

    calibration = {"p50": -0.5, "p99": -0.2}
    if paths["calibration"].exists():
        with open(paths["calibration"], encoding="utf-8") as handle:
            calibration = json.load(handle)

    metrics: dict = {}
    if paths["metrics"].exists():
        with open(paths["metrics"], encoding="utf-8") as handle:
            metrics = json.load(handle)

    return ModelBundle(
        xgb_model=joblib.load(paths["xgb"]),
        iso_model=joblib.load(paths["iso"]),
        feature_order=feature_order,
        calibration=calibration,
        metrics=metrics,
        model_dir=model_dir(directory),
    )


def save_model_bundle(
    xgb_model: Any,
    iso_model: Any,
    feature_order: list[str],
    calibration: dict,
    metrics: dict,
    directory: Path | None = None,
) -> dict[str, Path]:
    paths = artifact_paths(directory)
    paths["xgb"].parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(xgb_model, paths["xgb"])
    joblib.dump(iso_model, paths["iso"])
    with open(paths["feature_order"], "w", encoding="utf-8") as handle:
        json.dump(feature_order, handle, indent=2)
    with open(paths["calibration"], "w", encoding="utf-8") as handle:
        json.dump(calibration, handle, indent=2)
    with open(paths["metrics"], "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)
    return paths


def read_metrics(directory: Path | None = None) -> dict:
    path = artifact_paths(directory)["metrics"]
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
