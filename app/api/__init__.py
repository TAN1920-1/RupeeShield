"""HTTP layer.

Routes are thin on purpose: they validate the request boundary, delegate to a
service and return a schema. Queries live in ``app.db.repositories``, decision logic
lives in ``app.services``. Nothing here computes a risk score or a financial figure.

The aggregate ``router`` below is mounted at ``/api`` by :mod:`app.main`, so the
frontend only ever prefixes ``/api`` and the sliding-window rate limit is applied
once for every route instead of being repeated in each module. The direct scoring
route (``POST /score``) is deliberately excluded and mounted at the root by
``app.main`` because it is the pre-existing public contract.

Authorization is applied **here**, once, by grouping the routers:

``PUBLIC_MODULES``
    Health and sign-in. A health probe that needs a session is not a health probe,
    and the login endpoint is the one that hands the session out.
``SECURITY_MODULES``
    Everything that describes the whole bank. These are the security-operations
    pages: scenarios, monitoring, the full ledger, the network graph, incidents,
    defense comparison, model evaluation, platform settings and the audit log.
    They require an ANALYST session, checked on the server on every request.
``banking``
    The customer's own accounts, transfers and security centre. Scoped to the
    signed-in owner inside the service, so one customer can never read another's rows.
``notifications``
    Available to any signed-in role; the audience filter decides what each of them
    actually sees.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import enforce_rate_limit, require_roles
from app.api.routes import (
    accounts,
    audit,
    banking,
    defense,
    docs,
    health,
    incidents,
    ml,
    monitoring,
    network,
    notifications,
    overview,
    scenarios,
    simulation,
    transactions,
)
from app.api.routes import auth as auth_routes
from app.api.routes import settings as settings_routes
from app.db.models import ROLE_ANALYST

router = APIRouter(prefix="/api", dependencies=[Depends(enforce_rate_limit)])

#: Reachable without a session: liveness and the sign-in handshake.
PUBLIC_MODULES = (health, auth_routes)

#: Requires an ANALYST session (401 without one, 403 with the wrong role).
SECURITY_MODULES = (
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
)

for _module in PUBLIC_MODULES:
    router.include_router(_module.router)

for _module in SECURITY_MODULES:
    router.include_router(_module.router, dependencies=[Depends(require_roles(ROLE_ANALYST))])

router.include_router(banking.router)
router.include_router(notifications.router)

__all__ = ["router"]
