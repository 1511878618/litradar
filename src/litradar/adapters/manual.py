"""Manual adapter — the honest fallback when a surface cannot be fetched.

Items come either inline in the journal YAML or from ``.litradar/manual/<slug>.json``
so a human can paste a listing without editing Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..model import Article, JournalScan
from ..util import ConfigError, clean_abstract, parse_date
from .base import Adapter, failed, ok


class ManualAdapter(Adapter):
    key = "manual"

    def __init__(self, vault: Path | None = None) -> None:
        self.vault = vault

    def _entries(self, journal: dict, vault: Path | None) -> list[Any]:
        inline = journal.get("items")
        if isinstance(inline, list) and inline:
            return inline
        file_ref = journal.get("file")
        if file_ref:
            path = Path(str(file_ref))
            if not path.is_absolute() and vault is not None:
                path = Path(vault) / ".litradar" / "manual" / path
            if not path.exists():
                raise ConfigError(f"manual 数据文件不存在：{path}")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
            if isinstance(payload, dict):
                payload = payload.get("items") or []
            if not isinstance(payload, list):
                raise ConfigError(f"{path} 顶层应为列表或 {{'items': [...]}}")
            return payload
        raise ConfigError(f"manual 期刊 {journal['name']!r} 需要 items 或 file")

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        entries = self._entries(journal, self.vault)
        items: list[Article] = []
        for entry in entries:
            if isinstance(entry, str):
                entry = {"title": entry}
            if not isinstance(entry, dict) or not entry.get("title"):
                continue
            date_value = parse_date(entry.get("online_date") or entry.get("date"))
            items.append(
                Article(
                    title=str(entry["title"]),
                    journal=journal["name"],
                    url=str(entry.get("url") or ""),
                    surface=journal.get("surface"),
                    doi=entry.get("doi"),
                    online_date=date_value,
                    date_source="manual" if date_value else "missing",
                    abstract_raw=clean_abstract(entry.get("abstract"), max_chars=2000),
                    authors=list(entry.get("authors") or []),
                    content_type=str(entry.get("content_type") or "research-article"),
                )
            )
        if not items:
            return [], failed(journal, "error", "manual 条目为空或缺少 title", url=journal.get("url"))
        return items, ok(journal, items, url=journal.get("url"), surface=journal.get("surface"))
