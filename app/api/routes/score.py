"""Direct transaction scoring — the original real-time endpoint.

Mounted at the API **root** (``POST /score``), not under ``/api``, because it is the
pre-existing contract the README and the original tests describe.

The hot path is deliberately thin: compute causal features for the account from the
process-wide state store, ask the detection service to score them, and return the
decision with the top SHAP contributions. State is per-process and in-memory —
documented as such, because that is the honest description of a local teaching app.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter

from app.ml.features import UserStateStore
from app.schemas.transaction import ScoreRequest, ScoreResponse
from app.services.detection import evaluate_transaction
from app.utils.ids import new_id
from app.utils.timeutils import utcnow

router = APIRouter(tags=["scoring"])

_store: UserStateStore | None = None
_store_lock = threading.Lock()


def state_store() -> UserStateStore:
    """Process-wide causal feature store, created on first use."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = UserStateStore()
    return _store


def tracked_accounts() -> int:
    """How many accounts the in-memory feature store is currently tracking."""
    return _store.snapshot_size() if _store is not None else 0


@router.post(
    "/score",
    response_model=ScoreResponse,
    summary="Score a single transaction",
    description=(
        "Scores one transaction with the COMBINED configuration (rules + supervised ML + anomaly "
        "detection) and returns the blended risk score, the decision, the reasons and the top SHAP "
        "contributions. Features are computed causally from the account's own prior activity, so "
        "repeated calls for the same account build up velocity and amount history. Requires the "
        "trained artefacts to be present; returns 409 with setup instructions if they are not."
    ),
)
def score(payload: ScoreRequest) -> dict:
    features = state_store().compute_features(
        user_id=payload.user_id,
        amount=payload.amount,
        ts=payload.timestamp or utcnow(),
        channel=payload.channel,
        country=payload.country,
        device_id=payload.device_id,
    )
    record = evaluate_transaction(features, defense_config="COMBINED", explain=True)
    return {
        "transaction_id": new_id("REQ"),
        "fraud_risk_score": record.fraud_risk_score,
        "decision": record.decision,
        "ml_supervised_score": record.ml_supervised_score,
        "ml_anomaly_score": record.ml_anomaly_score,
        "rule_heuristic_score": record.rule_heuristic_score or 0.0,
        "reasons": record.reasons,
        "rules_triggered": record.rules_triggered,
        "top_shap_features": record.top_features,
        "latency_ms": record.latency_ms,
    }


__all__ = ["router", "state_store", "tracked_accounts"]
