"""A dependency-free word cloud (SVG) for one day's titles and tags.

Mermaid cannot draw a word cloud and adding matplotlib/wordcloud would drag a font
and rendering stack into a pipx-installed CLI.  This builds a small SVG with a
shelf layout: deterministic, text-only, and Obsidian renders it offline.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from pathlib import Path

from .model import Article

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")
_STOPWORDS = {
    "about", "after", "against", "also", "among", "analysis", "approach", "based",
    "between", "both", "cells", "could", "data", "during", "each", "effect", "effects",
    "from", "have", "human", "into", "more", "most", "novel", "other", "over", "paper",
    "results", "reveals", "role", "show", "shows", "study", "such", "than", "that",
    "method", "methods", "number", "their", "these", "this", "through", "toward",
    "towards", "under", "using", "via", "was", "were", "which", "with", "within",
    "without", "would", "your",
}
_PALETTE = ("#1f6feb", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0369a1", "#4d7c0f")


def title_terms(items: Iterable[Article], limit: int = 45) -> dict[str, int]:
    counts: dict[str, int] = {}
    for article in items:
        for token in _TOKEN_RE.findall(article.title or ""):
            word = token.lower().strip("-")
            if len(word) < 4 or word in _STOPWORDS:
                continue
            counts[word] = counts.get(word, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit])


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _term_weight(term: str) -> float:
    return 1.0 if term.isascii() else 2.2  # Chinese characters are much wider


def build_svg(
    terms: dict[str, int],
    *,
    width: int = 900,
    height: int = 380,
    caption: str = "",
) -> str:
    if not terms:
        return ""
    ordered = sorted(terms.items(), key=lambda pair: (-pair[1], pair[0]))
    maximum = max(count for _, count in ordered) or 1
    padding = 18
    x, y = padding, padding + 10
    row_height = 0.0
    pieces: list[str] = []
    for index, (term, count) in enumerate(ordered):
        size = 13 + 30 * math.pow(count / maximum, 0.65)
        unit = size * (1.0 if term.isascii() else 1.0)
        text_width = len(term) * unit * (0.62 if term.isascii() else 1.0)
        if x + text_width > width - padding and x > padding:
            x = padding
            y += row_height + 6
            row_height = 0.0
        if y + size > height - padding:
            break
        colour = _PALETTE[index % len(_PALETTE)]
        weight = "600" if size > 24 else "400"
        pieces.append(
            f'<text x="{x:.0f}" y="{y + size:.0f}" font-size="{size:.0f}" '
            f'font-weight="{weight}" fill="{colour}">{_escape(term)}</text>'
        )
        x += text_width + 12
        row_height = max(row_height, size)
    header = f'<text x="{padding}" y="{padding}" font-size="13" fill="#6b7280">{_escape(caption)}</text>' if caption else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        '<g font-family="PingFang SC, Hiragino Sans GB, Microsoft YaHei, Noto Sans CJK SC, sans-serif">'
        f"{header}{''.join(pieces)}</g></svg>"
    )


def write_wordcloud(
    report_dir: Path,
    run_id: str,
    kind: str,
    items: list[Article],
    *,
    limit: int = 45,
) -> str | None:
    """Write ``<report_dir>/assets/wordcloud-<run>-<kind>.svg``, return its relative path."""
    terms = title_terms(items, limit=limit)
    if len(terms) < 5:
        return None
    svg = build_svg(terms, caption=f"{run_id} · {kind} · {len(items)} articles")
    if not svg:
        return None
    directory = Path(report_dir) / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"wordcloud-{run_id}-{kind}.svg".replace("/", "-")
    (directory / name).write_text(svg, encoding="utf-8")
    return f"assets/{name}"
