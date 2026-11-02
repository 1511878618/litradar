"""Screening: the prompt we send, and the keyword fallback we trust more than nothing.

Screening only ever *orders* articles.  The full list is rendered from the store, so
a screening failure degrades the highlight section and nothing else.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .model import Article
from .util import collapse, truncate

SYSTEM_PROMPT = """你是文献筛选助手。给你一个研究方向和一批论文（标题 + 英文摘要），逐条判断相关性。

硬性规则：
1. 输出必须是单个 JSON 对象，格式为 {"results": [...]}，不要任何解释文字或 Markdown 代码块。
2. results 的长度必须等于 papers 的长度，顺序一致，每个 paper 恰好一条，id 必须原样复制。
3. relevance 是 0 到 1 之间的小数；relevant 是布尔值。
4. why 是中文，最多 40 字，说明为什么与方向相关；不要复述标题。
4b. summary_zh 是中文一句话解读（最多 60 字），说明这篇论文做了什么（基于标题/摘要），
    不要评价重要性、不要用"突破性/重大"这类词；信息不足就留 null。
5. abstract_en：如果摘要原文够用，逐字复制 papers 里给你的摘要（abstract_mode="verbatim"），
   不允许改写、缩写、补句；如果原文过长或不通顺，可写简短忠实概述（abstract_mode="faithful-summary"）；
   没有摘要就填 null 且 abstract_mode="none"。
6. conclusion 可留空。填写时必须是论文自己给出的结论（不是你的总结），且 conclusion_source
   必须是该论文的原文页面 URL（https://...），否则留空。
7. topic_tags 最多 3 个中文短语。
8. 绝对不要输出"总体趋势""研究热点""本期亮点"之类的综述内容。
9. 拿不准时 relevant=false、relevance 给低分；不要为了凑数而抬高分数。"""

USER_HINT = "研究方向：{direction}\n期刊：{journals}\n论文数：{count}\n\n论文列表："


def build_payload(
    direction: str,
    journals: list[str],
    articles: list[Article],
    *,
    max_abstract_chars: int = 1200,
) -> dict[str, Any]:
    papers = []
    for article in articles:
        papers.append(
            {
                "id": article.id,
                "journal": article.journal,
                "title": article.title,
                "abstract": truncate(article.abstract_raw or "", max_abstract_chars),
            }
        )
    return {"direction": direction, "journals": journals, "papers": papers}


def build_messages(
    direction: str,
    journals: list[str],
    articles: list[Article],
    *,
    max_abstract_chars: int = 1200,
) -> list[dict[str, str]]:
    payload = build_payload(
        direction, journals, articles, max_abstract_chars=max_abstract_chars
    )
    user = USER_HINT.format(
        direction=direction or "（未填写，按论文质量与新颖性判断）",
        journals="、".join(journals) or "（未知）",
        count=len(articles),
    ) + json.dumps(payload["papers"], ensure_ascii=False)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extract_results(payload: Any) -> list[dict]:
    """Accept the documented shape plus the few near-misses models produce."""
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "screening", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


_WORD_RE = re.compile(r"[a-z0-9\-]+")


def keyword_hits(article: Article, keywords: list[str]) -> list[str]:
    haystack = f"{article.title} {article.abstract_raw or ''}".casefold()
    return [keyword for keyword in keywords if keyword and keyword.casefold() in haystack]


def keyword_screen(
    articles: list[Article], keywords: list[str], min_relevance: float = 0.6
) -> list[dict]:
    """Deterministic fallback used when no model is available."""
    results: list[dict] = []
    for article in articles:
        hits = keyword_hits(article, keywords)
        title_hits = [keyword for keyword in hits if keyword.casefold() in article.title.casefold()]
        score = 0.0
        if hits:
            # One distinctive phrase is already evidence; extra hits and a title hit
            # push it up.  A single abstract hit must reach the default 0.6 bar,
            # otherwise a keyword-only run produces no highlights at all.
            score = min(0.95, 0.60 + 0.08 * (len(hits) - 1) + 0.10 * len(title_hits))
        results.append(
            {
                "id": article.id,
                "relevant": score >= min_relevance,
                "relevance": round(score, 2),
                "why": ("关键词命中：" + "、".join(hits[:3])) if hits else None,
                "topic_tags": [collapse(keyword) for keyword in hits[:3]],
                "abstract_en": None,
                "abstract_mode": "none",
                "conclusion": None,
                "conclusion_source": None,
            }
        )
    return results
