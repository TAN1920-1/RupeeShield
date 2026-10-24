"""Shared schema building blocks.

Money crosses the API boundary in **both** forms:

* ``*_minor``  exact integer paise — the source of truth, safe to sum client-side;
* ``*``        a float in major units (INR) — for display and charting only.

Timestamps are naive-UTC in the database and are serialised with an explicit
trailing ``Z`` so a browser in any timezone renders them correctly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.utils.timeutils import iso_utc

T = TypeVar("T")

UtcDateTime = Annotated[
    datetime,
    PlainSerializer(lambda value: iso_utc(value), return_type=str, when_used="json"),
]

OptionalUtcDateTime = Annotated[
    datetime | None,
    PlainSerializer(
        lambda value: iso_utc(value) if value is not None else None,
        return_type=str | None,
        when_used="json",
    ),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(ApiModel):
    detail: str
    code: str = "error"


class MessageResponse(ApiModel):
    message: str


class PaginationMeta(ApiModel):
    total: int = Field(description="Total rows matching the filter, before pagination.")
    limit: int
    offset: int
    returned: int


class Page(ApiModel, Generic[T]):
    """Standard list response so every endpoint paginates the same way."""

    items: list[T]
    total: int
    limit: int
    offset: int
    returned: int


class Provenance(ApiModel):
    """Where a number came from.

    Every metric surfaced in the UI carries one of these so observed dataset
    statistics, simulation output, model predictions and calculated estimates are
    never confused with each other.
    """

    source: str = Field(description="observed_dataset | simulation | model | estimate")
    detail: str
    deterministic: bool | None = None


class NamedCount(ApiModel):
    key: str
    count: int


class TimeSeriesPoint(ApiModel):
    timestamp: str
    value: float
    extra: dict[str, Any] = Field(default_factory=dict)
