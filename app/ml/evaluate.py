"""Model evaluation metrics.

Accuracy is deliberately **not** the headline number: the datasets used here have
a fraud rate well under 2%, so a model that predicts "not fraud" for everything
scores above 98% accuracy while catching nothing. Precision, recall, F1, PR-AUC
and the false-positive rate are reported instead.

Edge cases are handled explicitly because they show up constantly in practice:

* a slice containing only one class (all-positive or all-negative) -> metrics are
  ``None`` with an explanatory ``note`` instead of a crash or a fake 0.0;
* missing model artefacts -> the caller raises before reaching this module.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from sklearn.metrics import (
    auc,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


def _clean(labels: Sequence[Any], scores: Sequence[Any]) -> tuple[np.ndarray, np.ndarray]:
    y_true = np.asarray(labels, dtype=float)
    y_score = np.asarray(scores, dtype=float)
    mask = ~(np.isnan(y_true) | np.isnan(y_score))
    return y_true[mask].astype(int), y_score[mask]


def binary_metrics(
    labels: Sequence[Any],
    scores: Sequence[Any],
    threshold: float = 0.5,
) -> dict:
    """Full classification report for one decision threshold."""
    y_true, y_score = _clean(labels, scores)
    if y_true.size == 0:
        return {
            "samples": 0, "note": "No labelled samples available for evaluation.",
            "threshold": threshold, "precision": None, "recall": None, "f1": None,
            "pr_auc": None, "roc_auc": None, "false_positive_rate": None,
            "accuracy": None, "confusion": None, "class_balance": None,
        }

    positives = int((y_true == 1).sum())
    negatives = int((y_true == 0).sum())
    single_class_note = None
    if positives == 0 or negatives == 0:
        single_class_note = (
            f"Test slice contains only one class ({'positive' if positives else 'negative'}); "
            "precision/recall/PR-AUC/ROC-AUC are undefined and reported as null."
        )

    predictions = (y_score >= threshold).astype(int)

    result: dict[str, Any] = {
        "samples": int(y_true.size),
        "threshold": threshold,
        "note": single_class_note,
        "class_balance": {
            "positives": positives,
            "negatives": negatives,
            "positive_rate": round(positives / y_true.size, 6),
        },
    }

    if single_class_note:
        result.update({
            "precision": None, "recall": None, "f1": None, "pr_auc": None,
            "roc_auc": None, "false_positive_rate": None, "accuracy": None,
            "confusion": _confusion(y_true, predictions),
        })
        return result

    tn, fp, fn, tp = confusion_matrix(y_true, predictions, labels=[0, 1]).ravel()
    result.update({
        "precision": round(float(precision_score(y_true, predictions, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, predictions, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, predictions, zero_division=0)), 4),
        "pr_auc": round(float(average_precision_score(y_true, y_score)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_score)), 4),
        "false_positive_rate": round(float(fp / (fp + tn)), 6) if (fp + tn) else None,
        "accuracy": round(float((tp + tn) / y_true.size), 4),
        "confusion": _confusion(y_true, predictions),
    })
    return result


def _confusion(y_true: np.ndarray, predictions: np.ndarray) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, predictions, labels=[0, 1]).ravel()
    return {
        "true_negative": int(tn),
        "false_positive": int(fp),
        "false_negative": int(fn),
        "true_positive": int(tp),
        "labels": [["TN", "FP"], ["FN", "TP"]],
    }


def precision_recall_curve_points(
    labels: Sequence[Any],
    scores: Sequence[Any],
    max_points: int = 60,
) -> dict:
    """Down-sampled precision-recall curve for plotting."""
    y_true, y_score = _clean(labels, scores)
    if y_true.size == 0 or y_true.min() == y_true.max():
        return {"precision": [], "recall": [], "thresholds": [], "average_precision": None}

    precision, recall, thresholds = precision_recall_curve(y_true, y_score)
    step = max(len(precision) // max_points, 1)
    return {
        "precision": [round(float(v), 4) for v in precision[::step]],
        "recall": [round(float(v), 4) for v in recall[::step]],
        "thresholds": [round(float(v), 4) for v in thresholds[::step]],
        "average_precision": round(float(auc(recall, precision)), 4),
    }


def feature_importance(model, feature_order: Sequence[str]) -> list[dict]:
    """Gain-based importance from an XGBoost classifier, sorted descending.

    The classifier is fitted from a numeric matrix, so XGBoost keeps its own column
    names (``f0``, ``f1``, ...) and reports gains under those keys. ``feature_order``
    is the exact column order used at fit time — ``models/feature_order.json`` fails
    loudly if it ever drifts from ``app/ml/features.py`` — so unnamed columns are
    mapped back to their real name by position. Resolving them is what stops this
    function from returning a list of zeroes for every feature.
    """
    booster = model.get_booster()
    gains = booster.get_score(importance_type="gain")

    by_name: dict[str, float] = {}
    for key, value in gains.items():
        if key in feature_order:
            by_name[key] = float(value)
        elif key.startswith("f") and key[1:].isdigit():
            position = int(key[1:])
            if position < len(feature_order):
                by_name[feature_order[position]] = float(value)

    rows = [{"feature": name, "importance": round(by_name.get(name, 0.0), 4)} for name in feature_order]
    return sorted(rows, key=lambda item: item["importance"], reverse=True)


def latency_stats(latencies_ms: Sequence[float]) -> dict:
    """Percentile summary of per-transaction inference latency."""
    values = np.asarray([v for v in latencies_ms if v is not None], dtype=float)
    if values.size == 0:
        return {"samples": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {
        "samples": int(values.size),
        "mean_ms": round(float(values.mean()), 3),
        "p50_ms": round(float(np.percentile(values, 50)), 3),
        "p95_ms": round(float(np.percentile(values, 95)), 3),
        "p99_ms": round(float(np.percentile(values, 99)), 3),
    }
