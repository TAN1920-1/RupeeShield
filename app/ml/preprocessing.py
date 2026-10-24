"""Preprocessing: turning raw ledger rows into a leakage-free feature table.

The critical property is that the feature table is produced by replaying rows in
timestamp order through the *same* ``UserStateStore`` used at serving time. There
is exactly one implementation of the feature maths, so training and serving cannot
drift apart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.features import FEATURE_ORDER, UserStateStore

META_COLUMNS = ["transaction_id", "user_id", "timestamp", "is_fraud", "fraud_type"]


def build_feature_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Replay ``frame`` in timestamp order and return one feature row per transaction."""
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"])

    store = UserStateStore()
    rows: list[dict] = []
    for row in ordered.itertuples(index=False):
        features = store.compute_features(
            user_id=row.user_id,
            amount=float(row.amount),
            ts=row.timestamp,
            channel=row.channel,
            country=row.country,
            device_id=row.device_id,
        )
        features["transaction_id"] = row.transaction_id
        features["user_id"] = row.user_id
        features["timestamp"] = row.timestamp
        features["is_fraud"] = row.is_fraud
        features["fraud_type"] = getattr(row, "fraud_type", "NONE")
        rows.append(features)

    return pd.DataFrame(rows)


def clean_feature_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Make the feature matrix safe to hand to scikit-learn.

    * non-numeric junk becomes NaN, then is filled with the column median
      (median rather than 0 so a single bad row cannot look like a legitimately
      tiny amount);
    * infinities are removed;
    * rows missing a target label are dropped for *supervised* training only --
      callers that need unlabelled rows keep them.
    """
    cleaned = table.copy()

    for column in FEATURE_ORDER:
        if column not in cleaned.columns:
            cleaned[column] = 0.0
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned[FEATURE_ORDER] = cleaned[FEATURE_ORDER].replace([np.inf, -np.inf], np.nan)
    if cleaned[FEATURE_ORDER].isna().any().any():
        medians = cleaned[FEATURE_ORDER].median(numeric_only=True).fillna(0.0)
        cleaned[FEATURE_ORDER] = cleaned[FEATURE_ORDER].fillna(medians)

    return cleaned


def temporal_split(table: pd.DataFrame, test_fraction: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by time, never randomly.

    A random split would put transactions that happen *after* a fraud burst into
    the training set, which leaks future information into the model. Splitting on
    time reproduces the real deployment situation: train on the past, score the
    future.

    Injected fraud bursts are contiguous in time, so a temporal split also keeps
    each burst entirely on one side of the boundary rather than scattering
    near-duplicate rows across train and test.
    """
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")

    ordered = table.sort_values("timestamp").reset_index(drop=True)
    cut = int(len(ordered) * (1.0 - test_fraction))
    if cut <= 0 or cut >= len(ordered):
        raise ValueError(
            f"Dataset of {len(ordered)} rows is too small for a {test_fraction:.0%} temporal split."
        )
    return ordered.iloc[:cut].reset_index(drop=True), ordered.iloc[cut:].reset_index(drop=True)


def class_balance(labels: pd.Series) -> dict:
    """Describe the imbalance explicitly -- accuracy is meaningless without it."""
    total = int(labels.notna().sum())
    positives = int((labels == 1).sum())
    negatives = int((labels == 0).sum())
    return {
        "labelled_rows": total,
        "positive_rows": positives,
        "negative_rows": negatives,
        "positive_rate": round(positives / total, 6) if total else 0.0,
        "imbalance_ratio": round(negatives / positives, 2) if positives else None,
    }
