"""HTML surface adapter — a listing page plus CSS selectors.

Selector specs accept a tiny suffix language so one YAML block can address text and
attributes without a second schema:

* ``h4 a``             -> text of the first match
* ``h4 a::attr(href)`` -> that attribute
* ``h4 a::text``       -> text
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..model import Article, JournalScan, looks_non_research
from ..util import clean_abstract, parse_date, strip_html
from .base import Adapter, failed, ok


def _soup(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise RuntimeError("html 适配器需要 beautifulsoup4：pip install beautifulsoup4") from exc
    return BeautifulSoup(html, "html.parser")


def _pick(node, spec: str | None) -> str:
    if not spec:
        return ""
    css, _, modifier = spec.partition("::")
    css = css.strip()
    modifier = modifier.strip()
    try:
        element = node.select_one(css) if css else node
    except Exception:  # invalid CSS selector — surface it as a parse error upstream
        raise ValueError(f"非法 CSS 选择器：{spec!r}") from None
    if element is None:
        return ""
    if modifier.startswith("attr(") and modifier.endswith(")"):
        return (element.get(modifier[5:-1]) or "").strip()
    if modifier in ("", "text"):
        return strip_html(element.get_text(" ", strip=True))
    raise ValueError(f"不支持的选择器修饰符：{spec!r}")


class HtmlAdapter(Adapter):
    key = "html"

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        url = journal.get("url")
        response = fetcher.get(url, journal.get("headers"))
        if not response.ok:
            status = "blocked" if response.status in (401, 403, 429) else "error"
            return [], failed(journal, status, response.reason, http=response.status, url=url)

        soup = _soup(response.text)
        selectors = journal.get("selectors") or {}
        try:
            nodes = soup.select(selectors["item"])
        except Exception as exc:
            return [], failed(
                journal, "error", f"selectors.item 非法：{exc}", http=response.status, url=url
            )
        if not nodes:
            return [], failed(
                journal,
                "error",
                "页面可读但 selectors.item 未匹配（站点可能改版）",
                http=response.status,
                url=url,
            )

        base = journal.get("base_url") or url
        items: list[Article] = []
        for node in nodes:
            try:
                title = _pick(node, selectors.get("title"))
                link = _pick(node, selectors.get("link")) or _pick(node, "a::attr(href)")
                doi = _pick(node, selectors.get("doi")) or None
                date_text = _pick(node, selectors.get("date"))
                abstract = _pick(node, selectors.get("abstract"))
            except ValueError as exc:
                return [], failed(
                    journal, "error", str(exc), http=response.status, url=url
                )
            if not title or not link:
                continue
            online_date = parse_date(date_text)
            content_type = journal.get("content_type")
            if not content_type:
                content_type = "other" if looks_non_research(title) else "research-article"
            items.append(
                Article(
                    title=title,
                    journal=journal["name"],
                    url=urllib.parse.urljoin(base, link),
                    surface=journal.get("surface"),
                    doi=doi,
                    online_date=online_date,
                    date_source="publisher" if online_date else "missing",
                    abstract_raw=clean_abstract(abstract, max_chars=2000),
                    content_type=content_type,
                )
            )

        if not items:
            return [], failed(
                journal,
                "error",
                f"匹配到 {len(nodes)} 个条目容器，但没有一个解析出标题+链接",
                http=response.status,
                url=url,
            )
        return items, ok(journal, items, http=response.status, url=url)
