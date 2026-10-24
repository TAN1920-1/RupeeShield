"""In-application documentation (Module K).

Generated from the same constants the running system uses, so the formulas and
thresholds a reader sees here are the ones actually in force rather than a copy that
can drift out of date.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.settings import DocumentationOut
from app.services.documentation import build_documentation

router = APIRouter(tags=["documentation"])


@router.get(
    "/docs",
    response_model=DocumentationOut,
    include_in_schema=False,
    summary="Platform documentation, formulas and provenance",
)
def documentation() -> dict:
    return build_documentation()


__all__ = ["router"]
