"""Defense Comparison Lab (Module I).

Three endpoints: the configuration catalogue the UI explains, the experiment
itself (one scenario script replayed under all three configurations) and the
history of past comparisons.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.common import Page
from app.schemas.simulation import (
    DefenseComparisonOut,
    DefenseComparisonRequest,
    DefenseConfigInfo,
)
from app.services.defense_lab import compare, list_comparison_page
from app.services.simulation_runner import list_defense_configs

router = APIRouter(prefix="/defense", tags=["defense"])


@router.get("/configs", response_model=Page[DefenseConfigInfo], summary="Defense configurations")
def defense_configs() -> dict:
    items = list_defense_configs()
    return {
        "items": items,
        "total": len(items),
        "limit": len(items),
        "offset": 0,
        "returned": len(items),
    }


@router.post(
    "/compare",
    response_model=DefenseComparisonOut,
    status_code=201,
    summary="Compare the three defense configurations on one scenario",
    description=(
        "Builds one deterministic scenario script from (scenario, seed, n_events), then replays it "
        "three times changing nothing but the defense: NONE, RULE_BASED and COMBINED. Each run's "
        "scenario fingerprint is checked, so the response can state whether the three runs really "
        "did receive identical inputs. The conclusion reports whichever configuration actually "
        "produced the lowest net simulated impact, including the case where the extra detection "
        "cost more than it saved."
    ),
)
def compare_defenses(payload: DefenseComparisonRequest, session: SessionDep) -> dict:
    return compare(session, scenario=payload.scenario, n_events=payload.n_events, seed=payload.seed)


@router.get(
    "/comparisons",
    response_model=Page[DefenseComparisonOut],
    summary="Saved defense comparisons",
)
def comparisons(
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return list_comparison_page(session, limit=limit, offset=offset)


__all__ = ["router"]
