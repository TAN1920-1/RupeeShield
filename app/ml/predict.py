"""Inference wrapper.

Loads the model bundle **once** per process and exposes scoring + SHAP
explanations. Keeping this separate from training means the serving path never
imports scikit-learn training utilities, and a missing artefact produces one
clear error at startup rather than a failure mid-request.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from app.core.logging import get_logger
from app.ml.features import FEATURE_ORDER
from app.ml.model_store import ModelArtifactError, ModelBundle, load_model_bundle
from app.ml.risk_engine import RiskResult, score_transaction

logger = get_logger(__name__)


@dataclass
class ScoredTransaction:
    result: RiskResult
    top_features: list[dict]
    latency_ms: float


class Predictor:
    """Stateful, thread-safe holder of the loaded models and SHAP explainer."""

    def __init__(self, bundle: ModelBundle) -> None:
        self.bundle = bundle
        self._explainer = None
        self._lock = threading.Lock()

    @property
    def feature_order(self) -> list[str]:
        return self.bundle.feature_order

    @property
    def calibration(self) -> dict:
        return self.bundle.calibration

    def _get_explainer(self):
        """Build the SHAP explainer lazily.

        ``shap`` is an optional dependency (it is comparatively heavy). When it is
        not installed we degrade gracefully: scoring still works and the API
        returns an explicit note instead of pretending explanations exist.
        """
        if self._explainer is not None:
            return self._explainer
        with self._lock:
            if self._explainer is None:
                try:
                    import shap

                    self._explainer = shap.TreeExplainer(self.bundle.xgb_model)
                except Exception as exc:  # pragma: no cover - optional dependency
                    logger.warning("SHAP explanations unavailable (%s)", exc)
                    self._explainer = False
        return self._explainer

    def explain(self, features: dict, top_k: int = 4) -> list[dict]:
        explainer = self._get_explainer()
        if not explainer:
            return []
        matrix = np.array([[features[name] for name in self.feature_order]], dtype=float)
        values = explainer.shap_values(matrix)[0]
        indices = np.argsort(-np.abs(values))[:top_k]
        return [
            {
                "feature": self.feature_order[i],
                "value": round(float(features[self.feature_order[i]]), 3),
                "shap_contribution": round(float(values[i]), 4),
            }
            for i in indices
        ]

    def score(self, features: dict, *, use_ml: bool = True, explain: bool = False) -> ScoredTransaction:
        started = time.perf_counter()
        result = score_transaction(
            features,
            xgb_model=self.bundle.xgb_model,
            iso_model=self.bundle.iso_model,
            feature_order=self.feature_order,
            use_ml=use_ml,
        )
        top_features = self.explain(features) if explain else []
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return ScoredTransaction(result=result, top_features=top_features, latency_ms=latency_ms)


_predictor: Predictor | None = None
_predictor_lock = threading.Lock()


def get_predictor() -> Predictor:
    """Process-wide singleton predictor."""
    global _predictor
    if _predictor is None:
        with _predictor_lock:
            if _predictor is None:
                _predictor = Predictor(load_model_bundle())
    return _predictor


def reset_predictor() -> None:
    """Drop the cached predictor (used by tests and after retraining)."""
    global _predictor
    with _predictor_lock:
        _predictor = None


__all__ = [
    "FEATURE_ORDER",
    "ModelArtifactError",
    "Predictor",
    "ScoredTransaction",
    "get_predictor",
    "reset_predictor",
]
