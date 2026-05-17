"""Compatibility module exposing ``create_app`` and an ``app`` instance.

The substantive implementation lives in :mod:`whispercatch_sentinel.api`.
This module is preserved as the historical entry point so ``uvicorn
whispercatch_sentinel.app:app`` keeps working.
"""
from __future__ import annotations

from .api import AppDependencies, create_app

__all__ = ["AppDependencies", "create_app", "app"]

app = create_app()
