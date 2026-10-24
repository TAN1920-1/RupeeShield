"""ML detection engine: model information, evaluation and optional retraining
(Modules D and J).

Every evaluation block states which data produced it, and a missing artefact is
reported as a missing artefact with setup instructions rather than as a fabricated
metric.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionDep
from app.core.config import settings
from app.ml.predict import reset_predictor
from app.schemas.ml import (
    FeatureImportanceOut,
    ModelEvaluationOut,
    ModelInfoOut,
    TrainRequest,
    TrainResultOut,
)
from app.services import ml_service

router = APIRouter(prefix="/ml", tags=["ml"])


@router.get("/model-info", response_model=ModelInfoOut, summary="What models are on disk and loaded")
def model_info(session: SessionDep) -> dict:
    return ml_service.model_info(session)


@router.get(
    "/evaluation",
    response_model=ModelEvaluationOut,
    summary="Measured model metrics",
    description="Read from the metrics recorded when the artefacts were fitted, not recomputed: the "
                "models were fitted on that exact data snapshot. Each block names its split.",
)
def evaluation(session: SessionDep) -> dict:
    return ml_service.model_evaluation(session)


@router.get(
    "/feature-importance",
    response_model=FeatureImportanceOut,
    summary="Gain-based feature importance",
)
def feature_importance(session: SessionDep) -> dict:
    return ml_service.feature_importance_payload(session)


@router.post(
    "/train",
    response_model=TrainResultOut,
    summary="Retrain the models in-process",
    description="Disabled by default and gated behind `RUPESHIELD_ENABLE_API_TRAINING`; the "
                "documented workflow is the command line. The dataset is regenerated only when it "
                "is missing, so a dataset placed by hand is never overwritten.",
)
def train(payload: TrainRequest, session: SessionDep) -> dict:
    if not settings.enable_api_training:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "In-process training is disabled. Train from the command line instead:\n"
                "    python scripts/generate_dataset.py\n"
                "    python -m app.ml.train\n"
                "or set RUPESHIELD_ENABLE_API_TRAINING=true to allow it through the API."
            ),
        )
    result = ml_service.retrain(payload)
    ml_service.record_training_audit(session, result)
    # The trained artefacts changed on disk; drop the cached predictor so the next
    # score loads the new models instead of the previous ones.
    reset_predictor()
    return result


__all__ = ["router"]
