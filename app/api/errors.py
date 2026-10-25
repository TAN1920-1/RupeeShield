"""Service exception -> HTTP status mapping, registered once.

Routes are deliberately thin, so they do not wrap every service call in a
``try/except``. Instead the services raise small, meaningful exception types and
this module translates them at the HTTP boundary:

============================  ==========  ==================================================
Exception                     Status      Meaning
============================  ==========  ==================================================
``ValidationError``           422         A boundary value is out of range or not an allowed choice.
``LookupError``               404         The requested run / incident / account / transfer does not exist.
``DetectionUnavailable``      409         The requested configuration needs the ML artefacts.
``AuthenticationRequired``    401         No usable session (missing, expired or revoked).
``AccessDenied``              403         Authenticated but not permitted to touch this resource.
``DuplicateSubmission``       409         One idempotency key reused for a materially different request.
============================  ==========  ==================================================

Anything else stays a 500 and is logged: swallowing an unexpected exception and
returning ``200 OK`` would hide real bugs, which is exactly what the project's code
quality rules forbid.
"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.security.access import AccessDenied, AuthenticationRequired, DuplicateSubmission
from app.security.validation import ValidationError
from app.services.detection import DetectionUnavailable

logger = get_logger(__name__)


def _payload(detail: str, code: str) -> dict:
    return {"detail": detail, "code": code}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the API's exception handlers to ``app``."""

    @app.exception_handler(ValidationError)
    async def _validation_error(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload(str(exc), "validation_error"),
        )

    @app.exception_handler(AuthenticationRequired)
    async def _unauthenticated(_request: Request, exc: AuthenticationRequired) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_payload(str(exc) or "Sign in to continue.", "unauthenticated"),
            headers={"WWW-Authenticate": "Session"},
        )

    @app.exception_handler(AccessDenied)
    async def _forbidden(_request: Request, exc: AccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_payload(str(exc) or "You do not have access to this resource.", "forbidden"),
        )

    @app.exception_handler(DuplicateSubmission)
    async def _duplicate(_request: Request, exc: DuplicateSubmission) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload(str(exc), "duplicate_submission"),
        )

    @app.exception_handler(LookupError)
    async def _not_found(_request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_payload(str(exc) or "Resource not found.", "not_found"),
        )

    @app.exception_handler(DetectionUnavailable)
    async def _detection_unavailable(_request: Request, exc: DetectionUnavailable) -> JSONResponse:
        logger.warning("Detection unavailable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload(str(exc), "detection_unavailable"),
        )


__all__ = ["register_exception_handlers"]
