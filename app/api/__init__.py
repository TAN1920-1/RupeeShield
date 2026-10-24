"""HTTP layer.

Routes are thin on purpose: they validate the request boundary, delegate to a
service and return a schema. Queries live in ``app.db.repositories``, decision logic
lives in ``app.services``. Nothing here computes a risk score or a financial figure.

The aggregate ``router`` below is mounted at ``/api`` by :mod:`app.main`, so the
frontend only ever prefixes ``/api`` and the sliding-window rate limit is applied
once for every route instead of being repeated in each module. The direct scoring
route (``POST /score``) is deliberately excluded and mounted at the root by
``app.main`` because it is the pre-existing public contract.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import enforce_rate_limit
from app.api.routes import (
    accounts,
    audit,
    defense,
    docs,
    health,
    incidents,
    ml,
    monitoring,
    network,
    overview,
    scenarios,
    simulation,
    transactions,
)
from app.api.routes import settings as settings_routes

router = APIRouter(prefix="/api", dependencies=[Depends(enforce_rate_limit)])

for _module in (
    health,
    overview,
    scenarios,
    simulation,
    monitoring,
    transactions,
    accounts,
    network,
    incidents,
    defense,
    ml,
    settings_routes,
    docs,
    audit,
):
    router.include_router(_module.router)

__all__ = ["router"]
