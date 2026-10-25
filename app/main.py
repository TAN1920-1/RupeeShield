"""FastAPI application entry point for RupeeShield.

Startup does four things and nothing else: configure logging, create any missing
SQLite tables, make sure the two published demo logins exist, and register the
routers. Model artefacts are loaded lazily by the detection service on first use,
so the app boots (and ``/api/health`` answers) even before ``python -m app.ml.train``
has been run.

Run:
    uvicorn app.main:app --reload --port 8000
    open http://127.0.0.1:8000/docs          (interactive API)
    open http://127.0.0.1:8000/api/docs      (in-app documentation payload)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from app.api.errors import register_exception_handlers
from app.api.routes.score import router as score_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal, dispose_engine, init_db
from app.services import auth

logger = get_logger(__name__)


def _prepare_demo_logins() -> None:
    """Create the documented demo users when they are missing. Idempotent."""
    if not settings.demo_mode_enabled:
        return
    session = SessionLocal()
    try:
        created = auth.ensure_demo_users(session)
        if created:
            logger.info("Seeded %d demo user(s) for local sign-in.", len(created))
    except Exception:  # pragma: no cover - startup must not be blocked by demo data
        session.rollback()
        logger.warning("Could not prepare demo logins; sign-in will need the seeder.", exc_info=True)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepare shared resources once per process and release them on shutdown."""
    configure_logging()
    init_db()
    _prepare_demo_logins()
    logger.info(
        "%s %s starting (env=%s, database=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
        "sqlite" if settings.is_sqlite else "external",
    )
    yield
    dispose_engine()
    logger.info("%s shut down cleanly", settings.app_name)


app = FastAPI(
    title="RupeeShield API",
    description=(
        "Educational financial-cybersecurity cyber range. Runs controlled attack "
        "scenarios against a synthetic bank, scores the resulting traffic with a hybrid "
        "rules + supervised + anomaly-detection engine, correlates the security events, "
        "and compares the simulated financial impact of different defense strategies. "
        "Operates entirely on synthetic data; never touches real accounts or payments."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Service exceptions become meaningful status codes (422 / 404 / 409) instead of 500s.
register_exception_handlers(app)

# Everything the dashboard consumes lives under /api ...
app.include_router(api_router)
# ... except the original direct-scoring contract, which stays at the root.
app.include_router(score_router)


@app.get("/", include_in_schema=False)
def root() -> dict:
    """Human-friendly index so hitting the bare host is not a 404."""
    return {
        "service": "RupeeShield",
        "version": settings.app_version,
        "interactive_docs": "/docs",
        "api_root": "/api",
        "health": "/api/health",
        "score": "POST /score",
    }


__all__ = ["app"]
