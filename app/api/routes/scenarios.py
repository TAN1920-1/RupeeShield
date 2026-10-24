"""Attack-scenario catalogue (Module B).

Read-only: the catalogue is data (``app/services/simulation/catalog.py``), so the
UI, the API and the tests all describe the four scenarios identically.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import Page
from app.schemas.simulation import ScenarioInfo
from app.services.simulation_runner import list_scenarios

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get(
    "",
    response_model=Page[ScenarioInfo],
    summary="Available attack scenarios",
    description="Each scenario states its objective, designed risk level, default event count and "
                "the behavioural signature it is built to produce. Nothing here is executable "
                "against a real system.",
)
def scenarios() -> dict:
    items = list_scenarios()
    return {
        "items": items,
        "total": len(items),
        "limit": len(items),
        "offset": 0,
        "returned": len(items),
    }


__all__ = ["router"]
