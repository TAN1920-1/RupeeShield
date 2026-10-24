"""Overview dashboard (Module A)."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.overview import OverviewPayload
from app.services.overview import build_overview

router = APIRouter(tags=["overview"])


@router.get(
    "/overview",
    response_model=OverviewPayload,
    summary="Dashboard statistics, charts and recent activity",
    description=(
        "Every figure is computed from stored rows in this request — nothing is hard-coded. "
        "Each statistic carries a `provenance.source` of `observed_dataset`, `simulation`, "
        "`model` or `estimate` so the four kinds of number are never confused. "
        "`empty_states` explains any metric that is zero because no data exists yet."
    ),
)
def overview(session: SessionDep) -> dict:
    return build_overview(session)


__all__ = ["router"]
