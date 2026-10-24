"""Hybrid risk engine.

``final_risk`` is a weighted blend of three independent signals, each covering a
different failure mode:

====================  =====================================================
Signal                Catches
====================  =====================================================
ML_SUPERVISED         known fraud typologies (XGBoost on labelled history)
ML_ANOMALY            novel / never-labelled patterns (Isolation Forest)
RULE_HEURISTIC        instant, auditable guardrails independent of any model
====================  =====================================================

Why not trust a single ML score: a model is opaque in the moment, and it silently
degrades as patterns drift. Rules are cheap insurance that always fire, and they
produce reasons a human can read. This mirrors how real fraud engines are built.

This module is intentionally free of any database or framework dependency so it
can be unit-tested directly.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.features import FEATURE_ORDER

logger = get_logger(__name__)

WEIGHTS = {
    "ml_supervised": 0.55,
    "ml_anomaly": 0.25,
    "rule_heuristic": 0.20,
}

DEFAULT_BLOCK_THRESHOLD = 0.80
DEFAULT_FLAG_THRESHOLD = 0.45

_CALIBRATION_PATH = settings.model_dir / "anomaly_calibration.json"
_calibration_cache: dict | None = None


def load_anomaly_calibration(path: Path | None = None) -> dict:
    """Load the Isolation Forest score calibration computed at training time.

    Falls back to a permissive default so serving never hard-crashes, but a
    missing calibration file means anomaly scores are uncalibrated and should be
    treated as unreliable until the models are retrained.
    """
    global _calibration_cache
    target = path or _CALIBRATION_PATH
    if path is None and _calibration_cache is not None:
        return _calibration_cache

    if target.exists():
        with open(target, encoding="utf-8") as handle:
            calibration = json.load(handle)
    else:
        logger.warning(
            "anomaly calibration file %s not found; anomaly scores are uncalibrated. "
            "Run `python -m app.ml.train` to regenerate it.",
            target,
        )
        calibration = {"p50": -0.5, "p99": -0.2}

    if path is None:
        _calibration_cache = calibration
    return calibration


@dataclass
class RiskResult:
    fraud_risk_score: float
    decision: str
    ml_supervised_score: float | None
    ml_anomaly_score: float | None
    rule_heuristic_score: float
    reasons: list[str] = field(default_factory=list)
    rules_triggered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def rule_heuristic_score(
    features: dict,
    *,
    velocity_10min: int | None = None,
    velocity_1h: int | None = None,
    amount_zscore_cut: float | None = None,
    amount_over_avg_cut: float | None = None,
) -> tuple[float, list[str], list[str]]:
    """Cheap, explainable, model-independent guardrails.

    Thresholds default to the values in application settings (Settings ->
    correlation/rule thresholds) so the Defense Comparison Lab can change them
    without touching code.
    """
    thresholds = settings.thresholds
    velocity_10min = velocity_10min or thresholds.rule_velocity_10min
    velocity_1h = velocity_1h or thresholds.rule_velocity_1h
    amount_zscore_cut = amount_zscore_cut or thresholds.rule_amount_zscore
    amount_over_avg_cut = amount_over_avg_cut or thresholds.rule_amount_over_avg

    score = 0.0
    reasons: list[str] = []
    rules: list[str] = []

    if features["txns_last_10min"] >= velocity_10min:
        score += 0.35
        rules.append("R-HIGH-VELOCITY-10MIN")
        reasons.append(f"Very high transaction velocity ({velocity_10min}+ in last 10 minutes)")
    elif features["txns_last_1h"] >= velocity_1h:
        score += 0.20
        rules.append("R-HIGH-VELOCITY-1H")
        reasons.append(f"High transaction velocity ({velocity_1h}+ in last hour)")

    if features["time_since_prev_txn_min"] < 0.5 and features["txns_last_10min"] > 1:
        score += 0.15
        rules.append("R-RAPID-REPEAT")
        reasons.append("Extremely short time since previous transaction")

    if features["amount_zscore"] > amount_zscore_cut:
        score += 0.30
        rules.append("R-AMOUNT-OUTLIER")
        reasons.append("Transaction amount far above this account's historical pattern")
    elif features["amount_over_user_avg"] > amount_over_avg_cut:
        score += 0.15
        rules.append("R-AMOUNT-MULTIPLE-OF-AVG")
        reasons.append("Transaction amount several times this account's average")

    if features["is_new_device"] and features["is_new_country"]:
        score += 0.30
        rules.append("R-NEW-DEVICE-AND-COUNTRY")
        reasons.append("New device AND new country observed simultaneously")
    elif features["is_new_country"]:
        score += 0.15
        rules.append("R-NEW-COUNTRY")
        reasons.append("Transaction from a country not seen before for this account")
    elif features["is_new_device"]:
        score += 0.10
        rules.append("R-NEW-DEVICE")
        reasons.append("Transaction from a previously unseen device")

    if features["is_late_night"] and features["amount_over_user_avg"] > 2:
        score += 0.10
        rules.append("R-LATE-NIGHT-LARGE")
        reasons.append("Large late-night transaction")

    return min(score, 1.0), reasons, rules


def normalize_anomaly_score(raw_score: float, calibration: dict | None = None) -> float:
    """Map a raw ``IsolationForest.score_samples`` value to a [0, 1] anomaly score.

    The mapping is calibrated against the **actual** score distribution measured
    on legitimate training data:

    * ``p50`` -> ~0.0 (typical legitimate transaction)
    * ``p99`` -> ~1.0 (as unusual as the most unusual 1% of legitimate traffic)

    An assumed fixed centre saturates near 1.0 for almost any transaction, which
    makes the signal meaningless noise rather than evidence.
    """
    if calibration is None:
        calibration = load_anomaly_calibration()
    anomaly = -raw_score  # higher == more anomalous
    p50, p99 = calibration["p50"], calibration["p99"]
    span = max(p99 - p50, 1e-6)
    return float(np.clip((anomaly - p50) / span, 0.0, 1.0))


def combine_scores(
    rule_score: float,
    ml_supervised: float | None,
    ml_anomaly: float | None,
) -> float:
    """The single definition of the blended risk score.

    Both the online scorer and the offline evaluation use this, so the number
    reported in the model-evaluation dashboard is computed exactly the way a live
    transaction would be scored.
    """
    if ml_supervised is None or ml_anomaly is None:
        return round(min(max(rule_score, 0.0), 1.0), 4)
    blended = (
        WEIGHTS["ml_supervised"] * ml_supervised
        + WEIGHTS["ml_anomaly"] * ml_anomaly
        + WEIGHTS["rule_heuristic"] * rule_score
    )
    return round(min(max(blended, 0.0), 1.0), 4)


def decide(score: float, block_threshold: float | None = None, flag_threshold: float | None = None) -> str:
    thresholds = settings.thresholds
    block = thresholds.block_threshold if block_threshold is None else block_threshold
    flag = thresholds.flag_threshold if flag_threshold is None else flag_threshold
    if score >= block:
        return "BLOCK"
    if score >= flag:
        return "FLAG"
    return "ALLOW"


def score_transaction(
    features: dict,
    *,
    xgb_model=None,
    iso_model=None,
    feature_order: list[str] | None = None,
    use_ml: bool = True,
    block_threshold: float | None = None,
    flag_threshold: float | None = None,
) -> RiskResult:
    """Blend the available signals for one transaction's feature vector.

    ``use_ml=False`` yields the rule-based-only defense configuration: it never
    touches the models and the ML components of the result are reported as
    ``None`` so a caller can always tell how a decision was reached.
    """
    order = feature_order or FEATURE_ORDER
    rule_score, rule_reasons, rules_triggered = rule_heuristic_score(features)

    ml_supervised: float | None = None
    ml_anomaly: float | None = None

    if use_ml and xgb_model is not None and iso_model is not None:
        matrix = np.array([[features[name] for name in order]], dtype=float)
        # Models are persisted with scikit-learn/XGBoost; both accept bare ndarrays.
        ml_supervised = float(xgb_model.predict_proba(matrix)[0, 1])
        ml_anomaly = normalize_anomaly_score(float(iso_model.score_samples(matrix)[0]))

    if ml_supervised is None or ml_anomaly is None:
        # Rule-only configuration: the rule score IS the risk score.
        final = round(min(max(rule_score, 0.0), 1.0), 4)
    else:
        final = combine_scores(rule_score, ml_supervised, ml_anomaly)

    reasons = list(rule_reasons)
    if ml_supervised is not None and ml_supervised > 0.7:
        reasons.append(f"ML model strongly matches a known fraud pattern (p={ml_supervised:.2f})")
    if ml_anomaly is not None and ml_anomaly > 0.7:
        reasons.append(
            f"Transaction is statistically unusual versus population behaviour (score={ml_anomaly:.2f})"
        )
    if not reasons:
        reasons.append("No significant risk signals detected")

    return RiskResult(
        fraud_risk_score=final,
        decision=decide(final, block_threshold, flag_threshold),
        ml_supervised_score=None if ml_supervised is None else round(ml_supervised, 4),
        ml_anomaly_score=None if ml_anomaly is None else round(ml_anomaly, 4),
        rule_heuristic_score=round(rule_score, 4),
        reasons=reasons,
        rules_triggered=rules_triggered,
    )
