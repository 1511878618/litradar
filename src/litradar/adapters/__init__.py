"""Adapter registry."""

from __future__ import annotations

from .base import Adapter, CustomAdapter, failed, get_adapter, ok
from .biorxiv import BiorxivAdapter
from .crossref import CrossrefAdapter
from .html_surface import HtmlAdapter
from .manual import ManualAdapter
from .rss import RssAdapter

__all__ = [
    "Adapter",
    "BiorxivAdapter",
    "CrossrefAdapter",
    "CustomAdapter",
    "HtmlAdapter",
    "ManualAdapter",
    "RssAdapter",
    "failed",
    "get_adapter",
    "ok",
]
