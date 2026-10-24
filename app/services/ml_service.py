"""Model information, evaluation and (optional) in-process retraining.

The numbers shown here are the ones measured when the artefacts were fitted —
``metrics.json`` is read rather than recomputed, because the models were fitted on
that exact data snapshot and recomputing would silently mix a later dataset with an
earlier model.

Two rules keep this honest:

* every evaluation block states which data produced it (``train``, ``validation``,
  ``test`` or ``simulation``) so a metric can never be read out of context;
* when the artefacts are absent, the payload says so and returns setup instructions
  instead of a fabricated metric.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db import repositories as repo
from app.ml.evaluate import latency_stats
from app.ml.features import FEATURE_ORDER
from app.ml.model_store import (
    FEATURE_ORDER_FILENAME,
    artifact_paths,
    models_available,
    read_metrics,
)
from app.security.audit import record_audit
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

SETUP_INSTRUCTIONS = [
    "No trained model artefacts were found, so the detection engine is running its "
    "rule-based fallback and reports the ML components as null.",
    "Generate the dataset: python scripts/generate_dataset.py --n-users 800 --n-transactions 40000",
    "Train the models: python -m app.ml.train",
    "Restart the API so the trained artefacts are loaded.",
    "Nothing is downloaded and no credentials are required.",
]

#: Label and explanation for each metrics block stored in ``metrics.json``.
#: ``split`` is not decoration: it tells a reader which data produced the number.
METRIC_BLOCKS = (
    ("xgboost", "XGBoost (supervised)", "test",
     "Supervised classifier on transaction features only, measured on the held-out test window."),
    ("isolation_forest", "Isolation Forest (unsupervised)", "test",
     "Fitted on legitimate transactions only, then scored on the held-out test window."),
    ("combined", "Combined hybrid score", "test",
     "Rules + supervised + anomaly blended at the configured FLAG threshold."),
    ("rule_based", "Rule heuristics only", "test",
     "Auditable thresholds with no ML component."),
)

_LIMITATIONS = [
    "Metrics come from the synthetic dataset, whose fraud typologies are known to the model by "
    "construction, so they are an upper bound rather than a real-world estimate.",
    "The dataset carries no authentication or device telemetry: the contribution of security-event "
    "correlation cannot be measured here and is evaluated in the Defense Comparison Lab instead.",
    "Accuracy is not reported as a headline number because the positive class is roughly 3% of rows.",
]


def _shap_available() -> bool:
    try:
        import shap  # noqa: F401

        return True
    except Exception:  # pragma: no cover - optional dependency
        return False


def _artifact_rows(directory: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    for key, path in artifact_paths(directory).items():
        exists = path.exists()
        rows.append(
            {
                "name": key,
                "path": str(path),
                "exists": exists,
                "size_bytes": path.stat().st_size if exists else None,
            }
        )
    return rows


def _registered_versions(session: Session) -> list[dict]:
    rows = repo.governance.list_model_versions(session, limit=10)
    return [
        {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "artifact_path": row.artifact_path,
            "trained_at": row.trained_at,
            "training_rows": int(row.training_rows or 0),
            "is_active": bool(row.is_active),
            "notes": row.notes or "",
            "metrics": dict(row.metrics or {}),
        }
        for row in rows
    ]


def model_info(session: Session, directory: Path | None = None) -> dict:
    """``ModelInfoOut``: what is on disk, what is loaded, and how to fix it if not."""
    available = models_available(directory)
    metrics = read_metrics(directory)
    artifacts = _artifact_rows(directory)

    calibration = metrics.get("calibration") or {}
    if not calibration:
        # Fall back to the calibration file itself when a partial metrics file exists.
        paths = artifact_paths(directory)
        if paths["calibration"].exists():
            try:
                with open(paths["calibration"], encoding="utf-8") as handle:
                    calibration = json.load(handle)
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                logger.warning("Could not read anomaly calibration: %s", exc)

    feature_order = metrics.get("feature_order") or list(FEATURE_ORDER)
    notes: list[str] = []
    if not available:
        notes.append(
            "Trained artefacts are missing. Scoring still works, but only the rule-based "
            "component contributes and the ML scores are reported as null."
        )
    if metrics.get("feature_order") and list(metrics["feature_order"]) != list(FEATURE_ORDER):
        notes.append(
            f"{FEATURE_ORDER_FILENAME} disagrees with FEATURE_ORDER in app/ml/features.py. "
            "Retrain before trusting any score."
        )
    if not _shap_available():
        notes.append(
            "The optional 'shap' package is not installed, so per-transaction explanations are "
            "unavailable. Scoring and all metrics are unaffected."
        )

    return {
        "models_available": available,
        "version": metrics.get("version"),
        "trained_at": metrics.get("trained_at"),
        "feature_order": list(feature_order),
        "artifacts": artifacts,
        "calibration": calibration,
        "registered_versions": _registered_versions(session),
        "setup_instructions": [] if available else list(SETUP_INSTRUCTIONS),
        "shap_available": _shap_available(),
        "notes": notes,
    }


def _metric_block(key: str, label: str, split: str, description: str, source: dict) -> dict:
    """One ``MetricBlock`` from a stored metrics slice.

    Absent values stay ``None`` rather than becoming ``0.0``: a precision of zero and
    an undefined precision mean very different things.
    """
    return {
        "key": key,
        "label": label,
        "split": split,
        "description": source.get("description") or description,
        "samples": source.get("samples"),
        "threshold": source.get("threshold"),
        "precision": source.get("precision"),
        "recall": source.get("recall"),
        "f1": source.get("f1"),
        "pr_auc": source.get("pr_auc"),
        "roc_auc": source.get("roc_auc"),
        "false_positive_rate": source.get("false_positive_rate"),
        "accuracy": source.get("accuracy"),
        "confusion": source.get("confusion"),
        "class_balance": source.get("class_balance"),
        "note": source.get("note"),
    }


def model_evaluation(session: Session, directory: Path | None = None) -> dict:
    """``ModelEvaluationOut``: stored training metrics plus live inference latency."""
    metrics = read_metrics(directory)
    available = bool(metrics) and models_available(directory)

    blocks = [
        _metric_block(key, label, split, description, metrics.get(key) or {})
        for key, label, split, description in METRIC_BLOCKS
        if metrics.get(key)
    ]

    pr_curves = {
        key: metrics[key]["pr_curve"]
        for key in ("xgboost", "combined")
        if isinstance(metrics.get(key), dict) and metrics[key].get("pr_curve")
    }

    importance: list[dict[str, Any]] = []
    if isinstance(metrics.get("xgboost"), dict):
        importance = list(metrics["xgboost"].get("importance") or [])

    limitations = list(metrics.get("limitations") or _LIMITATIONS)

    return {
        "available": available,
        "model_info": model_info(session, directory),
        "metrics": blocks,
        "pr_curves": pr_curves,
        "feature_importance": importance,
        "class_balance": dict(metrics.get("class_balance") or {}),
        "dataset": dict(metrics.get("dataset") or {}),
        "split_strategy": str(metrics.get("split_strategy") or ""),
        "latency": latency_stats(repo.detections.detection_latencies(session)),
        "limitations": limitations,
        "generated_at": utcnow(),
    }


def feature_importance_payload(session: Session, directory: Path | None = None) -> dict:
    """Gain-based importance, read from the training metrics when present.

    Reading rather than recomputing keeps the API path free of scikit-learn training
    imports and avoids a second, subtly different computation of the same number. If
    an older metrics file has no importance block, the model is loaded and the gain
    is computed once as a fallback rather than the endpoint returning nothing.
    """
    metrics = read_metrics(directory)
    rows: list[dict[str, Any]] = []
    if isinstance(metrics.get("xgboost"), dict):
        rows = list(metrics["xgboost"].get("importance") or [])

    if not rows:
        try:
            from app.ml.evaluate import feature_importance
            from app.ml.model_store import load_model_bundle

            bundle = load_model_bundle(directory)
            rows = feature_importance(bundle.xgb_model, bundle.feature_order)
        except Exception as exc:
            logger.info("Feature importance unavailable (%s)", exc)
            rows = []
    return {"items": rows, "total": len(rows)}


def retrain(request, directory: Path | None = None) -> dict:
    """Regenerate the dataset if needed and retrain, synchronously.

    Only reachable when ``RUPESHIELD_ENABLE_API_TRAINING`` is enabled; the documented
    workflow is the command line. The dataset is regenerated only when it is missing,
    so retraining does not silently overwrite a dataset a user placed themselves.
    """
    from app.ml import synthetic_data, train as train_module

    target = settings.data_dir / "transactions.csv"
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    regenerated = False
    if not target.exists():
        frame, users = synthetic_data.generate(
            n_users=request.n_users,
            n_transactions=request.n_transactions,
            fraud_rate=request.fraud_rate,
            seed=request.seed,
        )
        frame.to_csv(target, index=False)
        users.to_csv(settings.data_dir / "users.csv", index=False)
        regenerated = True
        logger.info("Generated dataset with %s rows at %s", len(frame), target)

    argv = ["--transactions", str(target), "--no-register"]
    if directory is not None:
        argv += ["--model-dir", str(directory)]
    exit_code = train_module.main(argv)

    metrics = read_metrics(directory)
    status = "SUCCESS" if exit_code == 0 else "FAILED"
    message = (
        f"Retrained on {target}."
        if exit_code == 0
        else "Training failed; see the server log for the reason. Existing artefacts were left "
             "untouched where possible."
    )
    return {
        "status": status,
        "message": message,
        "regenerate_dataset": regenerated,
        "metrics": {
            key: metrics.get(key)
            for key in ("version", "trained_at", "xgboost", "combined", "isolation_forest")
            if metrics.get(key) is not None
        },
    }


def record_training_audit(session: Session, result: dict) -> None:
    record_audit(
        session,
        action="MODEL_TRAINED",
        entity_type="model",
        details={
            "status": result["status"],
            "regenerate_dataset": result["regenerate_dataset"],
        },
    )


__all__ = [
    "METRIC_BLOCKS",
    "SETUP_INSTRUCTIONS",
    "feature_importance_payload",
    "model_evaluation",
    "model_info",
    "record_training_audit",
    "retrain",
]
