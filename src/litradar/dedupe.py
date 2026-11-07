"""Deduplication: within a run, then against everything ever reported."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Article


@dataclass
class DedupeResult:
    new: list[Article] = field(default_factory=list)
    already_seen: list[Article] = field(default_factory=list)
    duplicates: int = 0


def dedupe(items: list[Article], seen: dict[str, dict]) -> DedupeResult:
    """Split candidates into new / already-reported.

    Identity is DOI-first (see ``model.fingerprint_key``).  A second occurrence of
    the same id inside one run (e.g. an article listed under two sections) is
    counted as a duplicate and dropped — the report must not show it twice.
    """
    result = DedupeResult()
    emitted: set[str] = set()
    for article in items:
        article_id = article.id
        if article_id in emitted:
            result.duplicates += 1
            continue
        emitted.add(article_id)
        if article_id in seen:
            result.already_seen.append(article)
        else:
            result.new.append(article)
    return result


GROUP_LABELS = (
    ("out_of_window", "越窗"),
    ("date_unverified", "待核验"),
    ("excluded_type", "非研究类型"),
)


def split_by_window(
    items: list[Article], window, include_types=("research-article", "review", "perspective", "letter")
) -> dict[str, list[Article]]:
    """Bucket adapter output the way the report's audit sections need it."""
    allowed = tuple(include_types) if include_types else ("research-article", "review", "perspective", "letter")
    buckets: dict[str, list[Article]] = {
        "in_window": [],
        "out_of_window": [],
        "date_unverified": [],
        "excluded_type": [],
    }
    for article in items:
        if article.content_type not in allowed:
            buckets["excluded_type"].append(article)
            continue
        if article.online_date is None:
            buckets["date_unverified"].append(article)
            continue
        if not window.contains(article.online_date):
            buckets["out_of_window"].append(article)
            continue
        buckets["in_window"].append(article)
    return buckets
