"""Adapter contract: ``fetch(journal, fetcher, window) -> (items, scan)``.

Adapters parse a publisher surface.  They never filter by date-window edge cases,
never deduplicate, and never decide whether a failure is fatal — the engine does
that so every journal behaves identically.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Protocol

from ..model import Article, JournalScan
from ..util import ConfigError, iso


def ok(
    journal: dict,
    items: list[Article],
    *,
    http: int | None = None,
    url: str | None = None,
    surface: str | None = None,
    status: str = "ok",
    visibility: str = "publisher",
    reason: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
    issue_identifier: str | None = None,
    cover_date: str | None = None,
) -> JournalScan:
    return JournalScan(
        journal=journal["name"],
        status=status,
        surface=surface if surface is not None else journal.get("surface"),
        url=url or journal.get("url") or journal.get("feed"),
        http=http,
        items_seen=len(items),
        reason=reason,
        scanned_at=iso(),
        visibility=visibility,
        cadence=journal.get("cadence") or "daily",
        weekday=journal.get("weekday"),
        volume=volume,
        issue=issue,
        issue_identifier=issue_identifier,
        cover_date=cover_date,
    )


def failed(
    journal: dict,
    status: str,
    reason: str,
    *,
    http: int | None = None,
    url: str | None = None,
) -> JournalScan:
    if status not in ("blocked", "timeout", "error", "metadata-only"):
        status = "error"
    return JournalScan(
        journal=journal["name"],
        status=status,
        surface=journal.get("surface"),
        url=url or journal.get("url") or journal.get("feed"),
        http=http,
        items_seen=0,
        reason=reason,
        scanned_at=iso(),
        visibility="metadata-only" if reason and "metadata" in reason else "publisher",
        cadence=journal.get("cadence") or "daily",
        weekday=journal.get("weekday"),
    )


class Adapter(Protocol):
    key: str

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        ...


class CustomAdapter:
    key = "custom"

    def __init__(self, journal: dict, vault: Path) -> None:
        module_ref = str(journal.get("module") or "")
        path = Path(module_ref)
        if not path.is_absolute():
            path = Path(vault) / ".litradar" / path
        if not path.exists():
            raise ConfigError(f"custom 适配器文件不存在：{path}")
        name = f"litradar_custom_{journal.get('slug') or 'journal'}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ConfigError(f"无法加载 custom 适配器：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        fetch = getattr(module, "fetch", None)
        if not callable(fetch):
            raise ConfigError(f"{path} 必须定义 fetch(journal, fetcher, window)")
        self._fetch = fetch
        self._vault = vault

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        result = self._fetch(journal, fetcher, window)
        if not (isinstance(result, tuple) and len(result) == 2):
            raise ConfigError("custom fetch() 必须返回 (list[Article], JournalScan)")
        items, scan = result
        if not isinstance(scan, JournalScan):
            raise ConfigError("custom fetch() 的第二个返回值必须是 JournalScan")
        return list(items), scan


def get_adapter(journal: dict, vault: Path) -> Adapter:
    """Instantiate the configured adapter for one journal."""
    from .biorxiv import BiorxivAdapter
    from .crossref import CrossrefAdapter
    from .html_surface import HtmlAdapter
    from .manual import ManualAdapter
    from .rss import RssAdapter

    key = str(journal.get("adapter") or "").lower()
    if key == "rss":
        return RssAdapter()
    if key == "crossref":
        return CrossrefAdapter()
    if key == "biorxiv":
        return BiorxivAdapter()
    if key == "html":
        return HtmlAdapter()
    if key == "manual":
        return ManualAdapter(vault)
    if key == "custom":
        return CustomAdapter(journal, vault)
    if key == "browser":
        raise ConfigError(
            "browser 适配器需要 `pip install \"litradar[browser]\"` 并装 Playwright；"
            "当前版本先请用 crossref 兜底"
        )
    raise ConfigError(f"未知 adapter {key!r}")
