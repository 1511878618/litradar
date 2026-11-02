"""RSS / Atom / RDF adapter — the highest-quality source when a journal offers one.

Publishers differ wildly: RDF feeds use ``dc:*`` and ``prism:*``, Atom uses
``entry/published``, RSS 2.0 uses ``pubDate``.  We match elements by local name so
one parser covers all three, and we never invent a date when the feed omits one.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..model import Article, JournalScan, looks_non_research
from ..util import clean_abstract, parse_date, strip_html
from .base import Adapter, failed, ok

_PUBLISHED_ONLINE_RE = re.compile(
    r"(?:Published|Publication date:)\s*(?:online|Available online)?\s*:?\s*"
    r"([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE,
)
_NON_RESEARCH_TYPES = (
    "editorial", "news", "letter", "essay", "perspective", "book", "errata",
    "correction", "comment", "obituary", "highlight", "in depth", "policy",
    "working life", "expert voices", "new products", "career", "feature",
    "correspondence", "reply", "retraction",
)
_DOI_IN_TEXT_RE = re.compile(r"10\.[0-9]{4,9}/[^\s\"<>,;]+")


def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element) -> list[ET.Element]:
    return list(node)


def _first_child(node: ET.Element, names: tuple[str, ...]) -> ET.Element | None:
    for child in _children(node):
        if _local(child.tag) in names:
            return child
    return None


def _all_children(node: ET.Element, names: tuple[str, ...]) -> list[ET.Element]:
    return [child for child in _children(node) if _local(child.tag) in names]


def _text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return strip_html("".join(node.itertext()))


def _text(node: ET.Element, *names: str) -> str:
    return _text_of(_first_child(node, names))


def _link(node: ET.Element) -> str:
    for child in _children(node):
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href and (child.get("rel") or "alternate") == "alternate":
            return href.strip()
    plain = _text(node, "link", "guid")
    return plain.strip()


def _doi(node: ET.Element, link: str) -> str | None:
    candidates = [
        _text(node, "doi"),
        _text(node, "identifier"),
        _text(node, "id"),
    ]
    for candidate in candidates:
        if candidate and candidate.lower().startswith("10."):
            return candidate
    match = _DOI_IN_TEXT_RE.search(link or "")
    return match.group(0) if match else None


def _date(node: ET.Element) -> tuple[Any, str]:
    for name in ("date", "publicationDate", "published", "pubDate", "updated", "issued", "created"):
        value = _text(node, name)
        parsed = parse_date(value)
        if parsed:
            return parsed, "publisher"
    for name in ("source", "description", "summary", "encoded", "content"):
        value = _text(node, name)
        match = _PUBLISHED_ONLINE_RE.search(value)
        if match and (parsed := parse_date(match.group(1))):
            return parsed, "publisher"
    return None, "missing"


def _abstract(node: ET.Element) -> str | None:
    for name in ("encoded", "summary", "description", "content"):
        element = _first_child(node, (name,))
        if element is None:
            continue
        raw = "".join(element.itertext())
        if not raw.strip():
            continue
        cleaned = clean_abstract(raw, max_chars=2000)
        if cleaned:
            return cleaned
    return None


def _content_type(node: ET.Element, journal: dict, title: str) -> str:
    """Feed-declared item type beats title heuristics (Science ships dc:type)."""
    declared = journal.get("content_type")
    if declared:
        return str(declared)
    raw = " ".join(_text(node, name) for name in ("type", "section")).strip().lower()
    if raw:
        if "review" in raw:
            return "review"
        if "research article" in raw or raw == "report":
            return "research-article"
        if any(term in raw for term in _NON_RESEARCH_TYPES):
            return "other"
        if "research" in raw:
            return "research-article"
    return "other" if looks_non_research(title) else "research-article"


def _authors(node: ET.Element) -> list[str]:
    people: list[str] = []
    for child in _children(node):
        local = _local(child.tag)
        if local in ("creator", "author"):
            name = child.get("name") if child.get("name") else None
            text = _text_of(child) if not name else strip_html(name)
            text = text.strip()
            if text and text not in people:
                people.append(text)
    return people


class RssAdapter(Adapter):
    key = "rss"

    def fetch(self, journal: dict, fetcher: Any, window: Any) -> tuple[list[Article], JournalScan]:
        url = journal.get("feed") or journal.get("url")
        response = fetcher.get(url, journal.get("headers"))
        if not response.ok:
            status = "blocked" if response.status in (401, 403, 429) else "error"
            return [], failed(journal, status, response.reason, http=response.status, url=url)
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            return [], failed(
                journal, "error", f"Feed 解析失败：{exc}", http=response.status, url=url
            )

        entries = [element for element in root.iter() if _local(element.tag) in ("item", "entry")]
        items: list[Article] = []
        for entry in entries:
            title = _text(entry, "title")
            link = _link(entry)
            if not title or not link:
                continue
            online_date, date_source = _date(entry)
            doi = _doi(entry, link)
            prefixes = journal.get("exclude_doi_prefixes") or []
            if doi and any(doi.lower().startswith(str(prefix).lower()) for prefix in prefixes):
                content_type = "other"
            else:
                content_type = _content_type(entry, journal, title)
            items.append(
                Article(
                    title=title,
                    journal=journal["name"],
                    url=link,
                    surface=journal.get("surface"),
                    doi=doi,
                    online_date=online_date,
                    date_source=date_source,
                    abstract_raw=_abstract(entry),
                    authors=_authors(entry),
                    content_type=content_type,
                )
            )
        return items, ok(journal, items, http=response.status, url=url)
