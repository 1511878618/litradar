"""Core data model: one article, one journal coverage record.

Every persisted structure carries a ``schema`` version written by the caller; the
shapes below are the single source of truth for the engine, the Markdown renderer
and the Obsidian plugin.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .util import collapse, norm_title

DATE_SOURCES = ("publisher", "crossref", "manual", "missing")
CONTENT_TYPES = ("research-article", "review", "other")
SCAN_STATUSES = ("ok", "metadata-only", "blocked", "timeout", "error")
HEALTHY_STATUSES = ("ok", "metadata-only")

_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI_TRAILING = ".,;:)]}\"'"


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    doi = _DOI_PREFIX_RE.sub("", str(raw).strip())
    doi = doi.split("?")[0].strip().strip(_DOI_TRAILING)
    return doi.lower() or None


def fingerprint_key(
    *, title: str, journal: str, doi: str | None = None, authors: list[str] | None = None
) -> str:
    """Stable identity for an article.

    DOI wins.  Without one we fall back to title + journal + *full* first-author
    name: using the surname alone collides ("Ann Lee" vs "Bob Lee").
    """
    if doi:
        return f"doi:{doi}"
    first_author = collapse((authors or [""])[0]).casefold()
    return f"title:{norm_title(title)}|{collapse(journal).casefold()}|{first_author}"


@dataclass
class Article:
    title: str
    journal: str
    url: str
    surface: str | None = None
    doi: str | None = None
    online_date: date | None = None
    date_source: str = "missing"
    abstract_raw: str | None = None
    abstract_source: str | None = None
    authors: list[str] = field(default_factory=list)
    content_type: str = "research-article"
    first_seen_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = collapse(self.title)
        self.journal = collapse(self.journal)
        self.url = (self.url or "").strip()
        self.doi = normalize_doi(self.doi)
        if self.date_source not in DATE_SOURCES:
            self.date_source = "missing"
        if self.content_type not in CONTENT_TYPES:
            self.content_type = "other"
        if not self.authors:
            self.authors = []

    @property
    def id(self) -> str:
        key = fingerprint_key(
            title=self.title, journal=self.journal, doi=self.doi, authors=self.authors
        )
        return f"sha1:{sha1_hex(key)[:16]}"

    @property
    def fingerprint(self) -> str:
        return fingerprint_key(
            title=self.title, journal=self.journal, doi=self.doi, authors=self.authors
        )

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract_raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "journal": self.journal,
            "url": self.url,
            "surface": self.surface,
            "doi": self.doi,
            "online_date": self.online_date.isoformat() if self.online_date else None,
            "date_source": self.date_source,
            "abstract_raw": self.abstract_raw,
            "abstract_source": self.abstract_source,
            "authors": list(self.authors),
            "content_type": self.content_type,
            "first_seen_at": self.first_seen_at,
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Article:
        from .util import parse_date

        return cls(
            title=data.get("title") or "",
            journal=data.get("journal") or "",
            url=data.get("url") or "",
            surface=data.get("surface"),
            doi=data.get("doi"),
            online_date=parse_date(data.get("online_date")),
            date_source=data.get("date_source") or "missing",
            abstract_raw=data.get("abstract_raw"),
            abstract_source=data.get("abstract_source"),
            authors=list(data.get("authors") or []),
            content_type=data.get("content_type") or "research-article",
            first_seen_at=data.get("first_seen_at"),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class JournalScan:
    """What happened to one journal in one run — the only place coverage is decided."""

    journal: str
    status: str
    surface: str | None = None
    url: str | None = None
    http: int | None = None
    items_seen: int = 0
    reason: str | None = None
    scanned_at: str | None = None
    visibility: str = "publisher"

    def __post_init__(self) -> None:
        if self.status not in SCAN_STATUSES:
            self.status = "error"

    @property
    def healthy(self) -> bool:
        return self.status in HEALTHY_STATUSES

    @property
    def complete(self) -> bool:
        return self.status == "ok" and self.visibility == "publisher"

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal": self.journal,
            "status": self.status,
            "surface": self.surface,
            "url": self.url,
            "http": self.http,
            "items_seen": self.items_seen,
            "reason": self.reason,
            "scanned_at": self.scanned_at,
            "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalScan:
        return cls(
            journal=data.get("journal") or "",
            status=data.get("status") or "error",
            surface=data.get("surface"),
            url=data.get("url"),
            http=data.get("http"),
            items_seen=int(data.get("items_seen") or 0),
            reason=data.get("reason"),
            scanned_at=data.get("scanned_at"),
            visibility=data.get("visibility") or "publisher",
        )


NON_RESEARCH_PREFIX = re.compile(
    r"^(editorial|author correction|publisher correction|correction to|correction|"
    r"erratum|retraction note|retraction|news|obituary|book review|correspondence|"
    r"comment|in this issue|table of contents|focus|publisher'?s note|"
    r"expression of concern)\b",
    re.IGNORECASE,
)


def looks_non_research(title: str) -> bool:
    """Title-prefix heuristic for feeds that carry no item type at all.

    Heuristics miss things; that is exactly why excluded items are kept in the
    report's audit section instead of being silently dropped.
    """
    return bool(NON_RESEARCH_PREFIX.match(collapse(title)))
