"""Per-article Chinese deep reading for the few highlights of one day.

Screening only *orders* articles; this stage writes what a human actually reads:
a translated title, a field label, a short digest and Chinese keywords.  It is
deliberately downstream of the full list and of ``screen.json``:

* it can only annotate articles that are already in the report (unknown ids are
  rejected, never invented);
* every item degrades back to the plain English layout on any validation failure;
* a model outage means "no digest", never "no report".

The grounding rule mirrors ``validate.py``: a digest may not introduce numbers
that do not appear in the title or the verbatim abstract.  Violations are kept as
warnings (and surface in the report audit) instead of being silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import Article
from .util import collapse, truncate

SYSTEM_PROMPT = """你是中文文献编辑。给你一个研究方向和若干篇论文（标题 + 英文摘要），
为每一篇写一段中文精读，供日报的「与你最相关」一节直接使用。

硬性规则：
1. 输出单个 JSON 对象，格式 {"papers": [...]}，不要任何解释文字或 Markdown 代码块。
2. papers 与输入 papers 顺序一致、数量相同；id 必须原样复制，不能新增论文、不能漏。
3. title_zh：中文标题，忠实翻译，不超过 60 字；基因名、软件名、专有名词保留英文。
4. field_zh：研究领域，写成「大领域/子领域」或「大领域（子领域）」，不超过 30 字。
5. digest_zh：80–200 字中文解读，按「背景 → 做了什么 → 结论或意义」组织。
   只允许使用标题与摘要里出现的信息：
   - 不得引入摘要没有的数字、样本量、比较结果或结论；
   - 摘要没写清楚的地方，就写"摘要未给出"，不要补全；
   - 不要写"重大突破""意义深远""值得关注"这类评价性套话。
6. keywords_zh：3–5 个中文关键词，每个不超过 12 字，不要与 field_zh 重复。
7. 摘要为空或只有一两句时，digest_zh 写 1–2 句并说明信息有限，不要扩写。"""

USER_HINT = "研究方向：{direction}\n论文数：{count}\n\n论文列表："

TITLE_MAX = 80
FIELD_MAX = 40
DIGEST_MIN = 40
DIGEST_MAX = 400
KEYWORD_MAX = 24
KEYWORDS_MAX = 6
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class DeepRead:
    """One validated annotation. Missing fields mean "fall back to English"."""

    id: str
    title_zh: str = ""
    field_zh: str = ""
    digest_zh: str = ""
    keywords_zh: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title_zh": self.title_zh,
            "field_zh": self.field_zh,
            "digest_zh": self.digest_zh,
            "keywords_zh": list(self.keywords_zh),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeepRead:
        return cls(
            id=str(data.get("id") or ""),
            title_zh=str(data.get("title_zh") or ""),
            field_zh=str(data.get("field_zh") or ""),
            digest_zh=str(data.get("digest_zh") or ""),
            keywords_zh=[str(item) for item in data.get("keywords_zh") or []],
            warnings=[str(item) for item in data.get("warnings") or []],
        )


def enabled(config: Any) -> bool:
    return bool(config.get("deepread.enabled", True))


def model_name(config: Any) -> str:
    return str(config.get("deepread.model", "") or config.get("llm.model", "deepseek-chat"))


def max_items(config: Any) -> int:
    try:
        return max(0, int(config.get("deepread.max_items", 8) or 0))
    except (TypeError, ValueError):
        return 8


def limit_articles(articles: list[Article], config: Any) -> list[Article]:
    """Highlights are already ranked; this only enforces the cost ceiling."""
    cap = max_items(config)
    if cap <= 0:
        return []
    return articles[:cap]


def _abstract_excerpt(article: Article, max_chars: int) -> str:
    return truncate(article.abstract_raw or "", max_chars)


def build_payload(
    direction: str, articles: list[Article], *, max_abstract_chars: int = 1800
) -> dict[str, Any]:
    papers = [
        {
            "id": article.id,
            "journal": article.journal,
            "title": article.title,
            "abstract": _abstract_excerpt(article, max_abstract_chars),
        }
        for article in articles
    ]
    return {"direction": direction, "papers": papers}


def build_messages(
    direction: str,
    articles: list[Article],
    *,
    max_abstract_chars: int = 1800,
) -> list[dict[str, str]]:
    import json

    payload = build_payload(direction, articles, max_abstract_chars=max_abstract_chars)
    user = USER_HINT.format(direction=direction or "（未填写）", count=len(papers_of(payload)))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": user + "\n" + json.dumps(payload, ensure_ascii=False),
        },
    ]


def papers_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("papers") if isinstance(payload, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]


def _clean_keywords(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = re.split(r"[,，、;；/\s]+", raw)
    elif isinstance(raw, list):
        parts = [str(item) for item in raw]
    else:
        parts = []
    seen: list[str] = []
    for part in parts:
        word = collapse(part).strip(" ·")
        if not word or word in seen:
            continue
        seen.append(truncate(word, KEYWORD_MAX))
    return seen[:KEYWORDS_MAX]


def _foreign_numbers(digest: str, source: str) -> list[str]:
    known = set(_NUMBER_RE.findall(source or ""))
    return sorted({value for value in _NUMBER_RE.findall(digest) if value not in known})


def validate_row(row: Any, article: Article) -> tuple[DeepRead | None, str | None]:
    """Return ``(annotation, rejection_reason)``; warnings ride along on the item."""
    if not isinstance(row, dict):
        return None, "精读条目不是对象"
    raw_id = str(row.get("id") or "").strip()
    if raw_id != article.id:
        return None, f"精读 id 不在本次候选内：{raw_id or '（空）'}"

    warnings: list[str] = []
    title_zh = collapse(str(row.get("title_zh") or ""))
    if not title_zh:
        return None, "缺少 title_zh"
    if len(title_zh) > TITLE_MAX:
        warnings.append(f"中文标题超长，已截断到 {TITLE_MAX} 字")
        title_zh = truncate(title_zh, TITLE_MAX)
    if title_zh.casefold() == article.title.casefold():
        warnings.append("中文标题与原文相同（疑似未翻译）")

    digest_zh = collapse(str(row.get("digest_zh") or ""))
    if not digest_zh:
        return None, "缺少 digest_zh"
    if len(digest_zh) > DIGEST_MAX:
        warnings.append(f"解读超长，已截断到 {DIGEST_MAX} 字")
        digest_zh = truncate(digest_zh, DIGEST_MAX)
    if len(digest_zh) < DIGEST_MIN:
        warnings.append(f"解读偏短（{len(digest_zh)} 字）")

    source = f"{article.title}\n{article.abstract_raw or ''}"
    foreign = _foreign_numbers(digest_zh, source)
    if foreign:
        warnings.append("解读中出现摘要没有的数字：" + "、".join(foreign[:5]))

    field_zh = truncate(collapse(str(row.get("field_zh") or "")), FIELD_MAX)
    if not field_zh:
        warnings.append("缺少 field_zh")

    keywords = _clean_keywords(row.get("keywords_zh"))
    if not keywords:
        warnings.append("缺少 keywords_zh")

    return (
        DeepRead(
            id=article.id,
            title_zh=title_zh,
            field_zh=field_zh,
            digest_zh=digest_zh,
            keywords_zh=keywords,
            warnings=warnings,
        ),
        None,
    )


def parse(
    payload: Any, articles: list[Article]
) -> tuple[dict[str, DeepRead], list[tuple[str, str]], list[str]]:
    """Validate a model payload against the exact candidate list."""
    by_id = {article.id: article for article in articles}
    annotations: dict[str, DeepRead] = {}
    rejected: list[tuple[str, str]] = []
    notes: list[str] = []

    if not isinstance(payload, dict):
        return {}, [], ["精读输出不是 JSON 对象，已整体忽略"]
    rows = payload.get("papers")
    if not isinstance(rows, list):
        return {}, [], ["精读输出缺少 papers 数组，已整体忽略"]
    if len(rows) != len(articles):
        notes.append(f"精读返回 {len(rows)} 条，候选 {len(articles)} 条")

    for row in rows:
        raw_id = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
        article = by_id.get(raw_id)
        if article is None:
            rejected.append((raw_id or "（空）", "精读 id 不在本次候选内"))
            continue
        if raw_id in annotations:
            notes.append(f"精读 id 重复：{raw_id}")
            continue
        annotation, rejected_reason = validate_row(row, article)
        if rejected_reason:
            rejected.append((raw_id, rejected_reason))
            continue
        if annotation is not None:
            annotations[raw_id] = annotation

    missing = [article.id for article in articles if article.id not in annotations]
    if missing:
        notes.append(f"{len(missing)} 篇未返回精读，已退回英文版式")
    return annotations, rejected, notes


def annotate(
    config: Any,
    direction: str,
    articles: list[Article],
    *,
    logger: Any = None,
    model: str | None = None,
    usage: dict | None = None,
) -> tuple[dict[str, DeepRead], dict[str, Any], list[tuple[str, str]], list[str], str]:
    """Call the model once; return (annotations, payload, rejected, notes, label)."""
    from . import llm

    messages = build_messages(
        direction,
        articles,
        max_abstract_chars=int(config.get("deepread.max_abstract_chars", 1800) or 1800),
    )
    payload = llm.chat_json(config, messages, logger=logger, usage=usage)
    annotations, rejected, notes = parse(payload, articles)
    name = model or model_name(config)
    stored = {
        "schema": 2,
        "model": name,
        "screening": "deepread-v1",
        "papers": [item.to_dict() for item in annotations.values()],
    }
    return annotations, stored, rejected, notes, f"deepread-v1（{name}）"
