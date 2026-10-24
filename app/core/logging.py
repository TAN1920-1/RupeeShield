"""Logging setup.

One place that configures the root logger so services can just call
``logging.getLogger(__name__)`` instead of configuring handlers themselves.
"""
from __future__ import annotations

import logging
import sys

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Idempotently configure the root logger (safe to call from app startup and CLI)."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(settings.log_level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # These libraries are noisy at INFO for our purposes.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
