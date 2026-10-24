"""Liveness and readiness."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.db import repositories as repo
from app.ml.model_store import models_available
from app.schemas.ml import HealthOut

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Liveness probe",
    description="Reports whether the process is up, how many synthetic accounts are stored, and "
                "whether trained model artefacts were loaded. Never returns an error while the "
                "process is alive.",
)
def health(session: SessionDep) -> dict:
    return {
        "status": "ok",
        "tracked_accounts": repo.accounts.count_accounts(session),
        "models_loaded": models_available(),
    }


__all__ = ["router"]
