"""bioRxiv / medRxiv adapter.

Both servers expose a date-ranged JSON API, which fits this engine better than their
rolling RSS feeds:

    GET https://api.biorxiv.org/details/{server}/{from}/{to}/{cursor}

It returns 30 records per page plus a total, with the abstract and category already
present.  Preprints are not peer reviewed, so every article carries
``extra.preprint`` and the report labels it — a reader must never mistake a preprint
for a published paper.
"""

from __future__ import annotations

import re
from typing import Any

from ..model import Article, JournalScan
from ..util import clean_abstract, clean_title, parse_date
from .base import Adapter, failed, ok

API_BASE = "https://api.biorxiv.org"
PAGE_SIZE = 30
MAX_PAGES = 60  # hard stop: 1800 records, well above a normal day
SERVERS = {
    "biorxiv": {"label": "bioRxiv", "site": "www.biorxiv.org"},
    "medrxiv": {"label": "medRxiv", "site": "www.medrxiv.org"},
}


def _norm_category(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _categories(journal: dict) -> set[str]:
    raw = journal.get("categories") or []
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,;]", raw)]
    return {_norm_category(entry) for entry in raw if _norm_category(entry)}


class BiorxivAdapter(Adapter):
    key = "biorxiv"

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        server = str(journal.get("server") or "biorxiv").strip().lower()
        if server not in SERVERS:
            return [], failed(
                journal, "error", f"未知预印本服务器 {server!r}；只支持 biorxiv / medrxiv"
            )
        label = SERVERS[server]["label"]
        site = SERVERS[server]["site"]
        base = str(journal.get("api") or API_BASE).rstrip("/")
        wanted = _categories(journal)
        max_items = max(1, int(journal.get("max_items") or 500))

        items: list[Article] = []
        cursor = 0
        total = 0
        pages = 0
        while True:
            url = (
                f"{base}/details/{server}/{window.start.isoformat()}"
                f"/{window.end.isoformat()}/{cursor}"
            )
            payload, response = fetcher.get_json(url, journal.get("headers"))
            if payload is None:
                status = "blocked" if response.status in (401, 403, 429) else "error"
                return [], failed(journal, status, response.reason, http=response.status, url=url)

            messages = payload.get("messages") or []
            message = messages[0] if messages and isinstance(messages[0], dict) else {}
            state = str(message.get("status") or "").lower()
            if state == "no posts found":
                break
            if state != "ok":
                return [], failed(
                    journal,
                    "error",
                    f"{label} API：{message.get('status') or '返回结构异常'}",
                    http=response.status,
                    url=url,
                )

            page = [entry for entry in payload.get("collection") or [] if isinstance(entry, dict)]
            try:
                total = int(message.get("total") or 0)
            except (TypeError, ValueError):
                total = cursor + len(page)

            for entry in page:
                article = self._article(entry, journal, site, label, wanted)
                if article:
                    items.append(article)

            cursor += len(page)
            pages += 1
            if not page or cursor >= total or len(items) >= max_items or pages >= MAX_PAGES:
                break

        if len(items) > max_items:
            items = items[:max_items]

        reasons: list[str] = []
        if wanted:
            reasons.append("仅纳入分类：" + "、".join(sorted(wanted)))
        if len(items) >= max_items and total > max_items:
            reasons.append(f"达到单刊上限 {max_items} 条（区间内共 {total} 条）")
        if pages >= MAX_PAGES and cursor < total:
            reasons.append(f"翻页达到上限 {MAX_PAGES} 页")

        return items, ok(
            journal,
            items,
            http=200,
            url=f"{base}/details/{server}/{window.start}/{window.end}",
            reason="；".join(reasons) or None,
        )

    def _article(
        self, entry: dict, journal: dict, site: str, label: str, wanted: set[str]
    ) -> Article | None:
        category = _norm_category(entry.get("category"))
        if wanted and category not in wanted:
            return None
        doi = entry.get("doi")
        title = clean_title(entry.get("title"), max_chars=500)
        online_date = parse_date(entry.get("date"))
        if not title or not doi:
            return None
        version = str(entry.get("version") or "1").strip() or "1"
        authors = [
            part.strip()
            for part in str(entry.get("authors") or "").split(";")
            if part.strip()
        ]
        return Article(
            title=title,
            journal=journal["name"],
            url=f"https://{site}/content/{doi}v{version}",
            surface=journal.get("surface") or f"{label} Preprints",
            doi=doi,
            online_date=online_date,
            date_source="publisher" if online_date else "missing",
            abstract_raw=clean_abstract(entry.get("abstract"), max_chars=2000),
            authors=authors,
            content_type="research-article",
            article_type="Preprint",
            extra={
                "preprint": label,
                "version": version,
                "category": entry.get("category"),
                "posted": entry.get("date"),
                "published_doi": entry.get("published"),
            },
        )
