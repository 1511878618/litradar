"""Crossref adapter — discovery only, always marked ``metadata-only``.

Every journal with an ISSN is reachable this way, but Crossref rarely carries the
abstract and the date is a metadata deposit rather than what the article page says.
The report therefore labels these dates as unverified instead of pretending they
were read off the publisher's surface.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from ..model import Article, JournalScan, looks_non_research
from ..util import clean_abstract, clean_title, parse_date
from .base import Adapter, failed, ok

API = "https://api.crossref.org/works"
SELECT = (
    "DOI,title,URL,type,published-online,published-print,issued,created,"
    "abstract,author,container-title,page,volume,issue,relation"
)
TYPE_MAP = {
    "journal-article": "research-article",
    "posted-content": "other",
    "proceedings-article": "research-article",
    "book-chapter": "other",
    "dataset": "other",
    "component": "other",
}
TYPE_LABELS = {
    "journal-article": "Journal Article",
    "posted-content": "Preprint",
    "proceedings-article": "Proceedings Article",
    "book-chapter": "Book Chapter",
    "dataset": "Dataset",
    "component": "Component",
}
_JATS_RE = re.compile(r"<jats:[^>]+>")


def _date_parts(item: dict, key: str):
    node = item.get(key)
    if not isinstance(node, dict):
        return None
    parts = node.get("date-parts")
    if not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    values = list(parts[0]) + [1, 1]
    try:
        return parse_date(f"{int(values[0]):04d}-{int(values[1]):02d}-{int(values[2]):02d}")
    except (TypeError, ValueError):
        return None


def _authors(item: dict) -> list[str]:
    names: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        name = " ".join(part for part in (given, family) if part)
        if not name:
            continue
        names.append(name)
    return names


class CrossrefAdapter(Adapter):
    key = "crossref"

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        # Elsevier journals (Lancet Digital Health, JBI) deposit only a month in
        # ``published``/``issued``; the precise day lives in ``created``.  Such
        # journals opt into a created-date query instead of an online-date one.
        date_filter = str(journal.get("date_filter") or "online").strip().lower()
        if date_filter == "created":
            window_filter = (
                f"from-created-date:{window.start.isoformat()},"
                f"until-created-date:{window.end.isoformat()}"
            )
        elif date_filter == "published":
            window_filter = (
                f"from-pub-date:{window.start.isoformat()},"
                f"until-pub-date:{window.end.isoformat()}"
            )
        else:
            window_filter = (
                f"from-online-pub-date:{window.start.isoformat()},"
                f"until-online-pub-date:{window.end.isoformat()}"
            )
        params = {
            "filter": f"issn:{journal['issn']},{window_filter}",
            "rows": str(max(1, min(1000, int(journal.get("rows") or 200)))),
            "select": SELECT,
        }
        if journal.get("mailto"):
            params["mailto"] = str(journal["mailto"])
        url = f"{API}?{urllib.parse.urlencode(params)}"

        payload, response = fetcher.get_json(url, journal.get("headers"))
        if payload is None:
            status = "blocked" if response.status in (401, 403, 429) else "error"
            return [], failed(journal, status, response.reason, http=response.status, url=url)

        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            return [], failed(
                journal, "error", "Crossref 返回结构异常（缺少 message.items）", url=url
            )

        entries = list(message["items"])
        total = int(message.get("total-results") or len(entries))
        offset = len(entries)
        page_limit = max(1, min(50, int(journal.get("max_pages", 20))))
        page_note = ""
        for _page in range(1, page_limit):
            if offset >= total or offset > 10000:
                break
            params["offset"] = str(offset)
            page_url = f"{API}?{urllib.parse.urlencode(params)}"
            more, page_response = fetcher.get_json(page_url, journal.get("headers"))
            batch = (more or {}).get("message", {}).get("items") if isinstance(more, dict) else None
            if not isinstance(batch, list) or not batch:
                page_note = f"；分页未完成（已读取 {offset}/{total} 条），请重试"
                break
            entries.extend(batch)
            offset += len(batch)
        if offset < total and not page_note:
            page_note = f"；达到分页上限（{offset}/{total} 条），请缩短窗口或提高 max_pages"
        items: list[Article] = []
        emitted = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            doi = entry.get("DOI")
            if doi and str(doi).lower() in emitted:
                continue
            if doi:
                emitted.add(str(doi).lower())
            titles = entry.get("title") or []
            title = clean_title(titles[0], max_chars=400) if titles else ""
            if not title:
                continue
            if date_filter == "created":
                online_date = (
                    _date_parts(entry, "created")
                    or _date_parts(entry, "published-online")
                    or _date_parts(entry, "published-print")
                    or _date_parts(entry, "issued")
                )
            elif date_filter == "published":
                # Crossref's issued is the earliest online/print publication date,
                # matching the broad publication filter. Never use deposit time.
                candidates = [d for d in (
                    _date_parts(entry, "published-online"),
                    _date_parts(entry, "published-print"),
                    _date_parts(entry, "issued"),
                ) if d is not None]
                online_date = min(candidates) if candidates else None
            else:
                online_date = (
                    _date_parts(entry, "published-online")
                    or _date_parts(entry, "published-print")
                    or _date_parts(entry, "issued")
                    or _date_parts(entry, "created")
                )
            content_type = TYPE_MAP.get(str(entry.get("type") or ""), "other")
            if content_type == "research-article" and looks_non_research(title):
                content_type = "other"
            items.append(
                Article(
                    title=title,
                    journal=journal["name"],
                    url=entry.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                    surface=journal.get("surface"),
                    doi=doi,
                    online_date=online_date,
                    date_source="crossref" if online_date else "missing",
                    abstract_raw=clean_abstract(_JATS_RE.sub(" ", entry.get("abstract") or ""), max_chars=2000),
                    authors=_authors(entry),
                    content_type=content_type,
                    article_type=TYPE_LABELS.get(
                        str(entry.get("type") or ""),
                        str(entry.get("type") or "Journal Article").replace("-", " ").title(),
                    ),
                    extra={
                        "crossref_type": entry.get("type"),
                        "relations": entry.get("relation") or {},
                        "date_basis": date_filter if date_filter in ("created", "published") else "online",
                        "volume": entry.get("volume"),
                        "issue": entry.get("issue"),
                        "page": entry.get("page"),
                    },
                )
            )

        items.sort(key=lambda article: (article.online_date is not None, article.online_date), reverse=True)
        return items, ok(
            journal,
            items,
            http=response.status,
            url=url,
            status="metadata-only",
            visibility="metadata-only",
            reason="Crossref 仅提供元数据，摘要多为空" + page_note,
        )
