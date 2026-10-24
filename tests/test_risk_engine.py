"""Hybrid risk engine: rules, blending, calibration and the decision bands.

Every assertion that involves a threshold reads it from ``settings`` rather than
hard-coding a number, because the whole point of the engine is that the bands are
configuration, not constants. The blended-score tests use tiny stub models so the
arithmetic is pinned exactly instead of depending on whatever the current joblib
artefacts happen to output.
"""
from __future__ import annotations

import numpy as np

from app.core.config import settings
from app.ml.features import FEATURE_ORDER
from app.ml.risk_engine import (
    WEIGHTS,
    combine_scores,
    decide,
    normalize_anomaly_score,
    rule_heuristic_score,
    score_transaction,
)


def _features(**overrides) -> dict:
    """A feature vector that triggers no rule, so each test can enable exactly one."""
    base = {
        "txns_last_10min": 0,
        "txns_last_1h": 0,
        "txns_last_24h": 0,
        "time_since_prev_txn_min": 100.0,
        "amount_zscore": 0.0,
        "amount_over_user_avg": 1.0,
        "is_new_device": 0,
        "is_new_country": 0,
        "is_late_night": 0,
    }
    base.update(overrides)
    return base


def _full_features(**overrides) -> dict:
    """A complete ``FEATURE_ORDER`` vector, for the ML path of ``score_transaction``."""
    base = {
        "amount": 500.0,
        "hour": 14,
        "day_of_week": 2,
        "is_weekend": 0,
        "is_late_night": 0,
        "time_since_prev_txn_min": 100.0,
        "txns_last_10min": 0,
        "txns_last_1h": 0,
        "txns_last_24h": 3,
        "user_avg_amount": 450.0,
        "amount_over_user_avg": 1.0,
        "amount_zscore": 0.0,
        "is_new_device": 0,
        "is_new_country": 0,
        "is_channel_switch": 0,
    }
    base.update(overrides)
    assert list(base) == FEATURE_ORDER
    return base


# ---------------------------------------------------------------------------
# Rule-based heuristic
# ---------------------------------------------------------------------------
def test_rule_engine_triggers_each_documented_rule():
    velocity_10 = settings.thresholds.rule_velocity_10min
    velocity_1h = settings.thresholds.rule_velocity_1h
    zscore_cut = settings.thresholds.rule_amount_zscore
    over_avg_cut = settings.thresholds.rule_amount_over_avg

    velocity_10min = rule_heuristic_score(_features(txns_last_10min=velocity_10))
    assert velocity_10min[2] == ["R-HIGH-VELOCITY-10MIN"]
    assert velocity_10min[0] == 0.35

    velocity_1h = rule_heuristic_score(_features(txns_last_1h=velocity_1h))
    assert velocity_1h[2] == ["R-HIGH-VELOCITY-1H"]
    assert velocity_1h[0] == 0.20

    rapid_repeat = rule_heuristic_score(
        _features(time_since_prev_txn_min=0.0, txns_last_10min=2)
    )
    assert rapid_repeat[2] == ["R-RAPID-REPEAT"]
    assert rapid_repeat[0] == 0.15

    outlier = rule_heuristic_score(_features(amount_zscore=zscore_cut + 1.0))
    assert outlier[2] == ["R-AMOUNT-OUTLIER"]
    assert outlier[0] == 0.30

    multiple = rule_heuristic_score(_features(amount_over_user_avg=over_avg_cut + 1.0))
    assert multiple[2] == ["R-AMOUNT-MULTIPLE-OF-AVG"]
    assert multiple[0] == 0.15

    new_device_country = rule_heuristic_score(_features(is_new_device=1, is_new_country=1))
    assert new_device_country[2] == ["R-NEW-DEVICE-AND-COUNTRY"]
    assert new_device_country[0] == 0.30

    new_country = rule_heuristic_score(_features(is_new_country=1))
    assert new_country[2] == ["R-NEW-COUNTRY"]
    assert new_country[0] == 0.15

    new_device = rule_heuristic_score(_features(is_new_device=1))
    assert new_device[2] == ["R-NEW-DEVICE"]
    assert new_device[0] == 0.10

    late_night = rule_heuristic_score(_features(is_late_night=1, amount_over_user_avg=3.0))
    assert late_night[2] == ["R-LATE-NIGHT-LARGE"]
    assert late_night[0] == 0.10


def test_a_clean_feature_vector_triggers_no_rule():
    score, reasons, rules = rule_heuristic_score(_features())
    assert score == 0.0
    assert rules == []
    assert reasons == []


def test_rule_score_is_capped_at_one():
    everything = _features(
        txns_last_10min=settings.thresholds.rule_velocity_10min,
        time_since_prev_txn_min=0.0,
        amount_zscore=settings.thresholds.rule_amount_zscore + 1.0,
        is_new_device=1,
        is_new_country=1,
        is_late_night=1,
        amount_over_user_avg=10.0,
    )
    score, _reasons, rules = rule_heuristic_score(everything)
    assert score == 1.0
    assert len(rules) >= 4


# ---------------------------------------------------------------------------
# Decision bands
# ---------------------------------------------------------------------------
def test_decision_honours_the_configured_bands_at_and_around_the_boundaries():
    block = settings.thresholds.block_threshold
    flag = settings.thresholds.flag_threshold
    step = 1e-4
    assert flag < block

    assert decide(block) == "BLOCK"
    assert decide(block + step) == "BLOCK"
    assert decide(block - step) == "FLAG"

    assert decide(flag) == "FLAG"
    assert decide(flag + step) == "FLAG"
    assert decide(flag - step) == "ALLOW"

    assert decide(0.0) == "ALLOW"


# ---------------------------------------------------------------------------
# Anomaly normalisation
# ---------------------------------------------------------------------------
def test_anomaly_normalisation_maps_calibration_percentiles_to_zero_and_one():
    # The calibration stores anomaly-scale percentiles (higher == more anomalous).
    # score_samples is negated first, so a raw score of -p50 is typical (0.0).
    calibration = {"p50": 0.5, "p99": 0.8}
    assert normalize_anomaly_score(-0.5, calibration) == 0.0
    assert normalize_anomaly_score(-0.8, calibration) == 1.0
    assert normalize_anomaly_score(-0.65, calibration) == 0.5


def test_anomaly_normalisation_clamps_outside_the_calibrated_range():
    calibration = {"p50": 0.5, "p99": 0.8}
    # Less anomalous than p50 saturates at 0.0; far more anomalous than p99 at 1.0.
    assert normalize_anomaly_score(-0.1, calibration) == 0.0
    assert normalize_anomaly_score(-2.0, calibration) == 1.0
    for raw in (-0.1, -2.0, -0.65):
        assert 0.0 <= normalize_anomaly_score(raw, calibration) <= 1.0


def test_anomaly_normalisation_handles_a_degenerate_span():
    degenerate = {"p50": 0.5, "p99": 0.5}
    assert normalize_anomaly_score(-0.5, degenerate) == 0.0
    # Any deviation from the single-point calibration saturates rather than dividing by zero.
    assert normalize_anomaly_score(-0.6, degenerate) == 1.0


# ---------------------------------------------------------------------------
# Blending
# ---------------------------------------------------------------------------
def test_combine_scores_respects_the_documented_weights():
    rule, supervised, anomaly = 0.5, 0.8, 0.4
    expected = round(
        WEIGHTS["ml_supervised"] * supervised
        + WEIGHTS["ml_anomaly"] * anomaly
        + WEIGHTS["rule_heuristic"] * rule,
        4,
    )
    assert combine_scores(rule, supervised, anomaly) == expected


def test_combine_scores_tolerates_a_missing_ml_score():
    rule, supervised, anomaly = 0.7, 0.9, 0.5
    # Either ML signal missing falls back to the rule score alone, clipped to [0, 1].
    assert combine_scores(rule, None, anomaly) == rule
    assert combine_scores(rule, supervised, None) == rule
    assert combine_scores(1.5, None, None) == 1.0
    assert combine_scores(-0.5, None, None) == 0.0


# ---------------------------------------------------------------------------
# Full entry point with stub models
# ---------------------------------------------------------------------------
class _StubXgb:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, matrix):
        return np.array([[1.0 - self.probability, self.probability]])


class _StubIso:
    def __init__(self, raw_score: float) -> None:
        self.raw_score = raw_score

    def score_samples(self, matrix):
        return np.array([self.raw_score])


def test_score_transaction_blends_models_rules_and_the_decision_consistently():
    features = _full_features(is_new_device=1)
    stub_xgb = _StubXgb(0.9)
    stub_iso = _StubIso(-0.55)

    result = score_transaction(features, xgb_model=stub_xgb, iso_model=stub_iso, feature_order=FEATURE_ORDER)

    expected_anomaly = normalize_anomaly_score(-0.55)
    rule_score, _reasons, rules = rule_heuristic_score(features)
    expected_final = combine_scores(rule_score, 0.9, expected_anomaly)

    assert result.ml_supervised_score == round(0.9, 4)
    assert result.ml_anomaly_score == round(expected_anomaly, 4)
    assert result.rule_heuristic_score == round(rule_score, 4)
    assert result.rules_triggered == rules == ["R-NEW-DEVICE"]
    assert result.fraud_risk_score == expected_final
    # The decision is exactly the decision function applied to the returned score.
    assert result.decision == decide(result.fraud_risk_score)


def test_score_transaction_rule_only_configuration_reports_no_ml_scores():
    features = _full_features(is_new_device=1)
    result = score_transaction(
        features, xgb_model=_StubXgb(0.9), iso_model=_StubIso(-0.55), use_ml=False
    )
    assert result.ml_supervised_score is None
    assert result.ml_anomaly_score is None
    assert result.fraud_risk_score == round(rule_heuristic_score(features)[0], 4)
    assert result.decision == decide(result.fraud_risk_score)


def test_score_transaction_without_models_falls_back_to_rules_only():
    features = _full_features(is_new_device=1, is_new_country=1)
    result = score_transaction(features)
    assert result.ml_supervised_score is None
    assert result.ml_anomaly_score is None
    assert result.fraud_risk_score == round(rule_heuristic_score(features)[0], 4)
    assert result.decision == decide(result.fraud_risk_score)
