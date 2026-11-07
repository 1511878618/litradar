"""Weekly synthesis: "what actually happened this week", with every claim pinned to papers.

This is the one section that is *allowed* to talk about trends, so it carries the
strictest rule in the project: each item must cite at least two paper ids from the
week's candidate list.  An item that invents a paper, or drops below two citations,
is discarded and recorded — a weekly report with two hotspots is better than one
with three where the third cannot be traced back.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .model import Article
from .util import collapse, truncate

SYSTEM_PROMPT = """你是中文学术周报编辑。给你某组期刊在一周内收录的论文（标题 + 英文摘要），
归纳本周真正值得注意的几条共同趋势。

硬性规则：
1. 输出单个 JSON 对象，格式 {"hotspots": [...]}，不要任何解释文字或 Markdown 代码块。
2. 每条热点：{"title": 短标题, "body": 说明, "evidence": [论文 id, ...]}。
3. evidence 里至少 2 个 id，且必须原样复制自给定的 papers；不能引用列表外的论文。
4. title 不超过 20 字；body 60–200 字，说明「哪几篇工作在做什么、共同指向什么」。
5. 只允许使用这些论文标题/摘要里出现的信息，不得引入新的数字、样本量或结论。
6. 不要写"重大突破""意义深远""值得关注"这类评价性套话，也不要重复摘要。
7. 找不出真正共同的方向就少写几条（1–3 条即可），不要为了凑数硬编。"""

USER_HINT = "研究方向：{direction}\n本周候选论文：{count} 篇\n\n论文列表："

TITLE_MAX = 40
BODY_MIN = 40
BODY_MAX = 300
EVIDENCE_MIN = 2
MAX_ITEMS = 5
_ID_RE = re.compile(r"^sha1:[0-9a-f]{6,}$")


@dataclass
class Hotspot:
    title: str
    body: str
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hotspot:
        return cls(
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            evidence=[str(item) for item in data.get("evidence") or []],
            warnings=[str(item) for item in data.get("warnings") or []],
        )


def enabled(config: Any) -> bool:
    return bool(config.get("weekly.enabled", True))


def model_name(config: Any) -> str:
    return str(config.get("llm.model", "deepseek-chat"))


def max_items(config: Any) -> int:
    try:
        return max(1, int(config.get("weekly.max_items", 120) or 120))
    except (TypeError, ValueError):
        return 120


def build_payload(
    direction: str, articles: list[Article], *, max_abstract_chars: int = 900
) -> dict[str, Any]:
    return {
        "direction": direction,
        "papers": [
            {
                "id": article.id,
                "journal": article.journal,
                "title": article.title,
                "abstract": truncate(article.abstract_raw or "", max_abstract_chars),
            }
            for article in articles
        ],
    }


def build_messages(
    direction: str, articles: list[Article], *, max_abstract_chars: int = 900
) -> list[dict[str, str]]:
    payload = build_payload(direction, articles, max_abstract_chars=max_abstract_chars)
    user = USER_HINT.format(direction=direction or "（未填写）", count=len(payload["papers"]))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user + "\n" + json.dumps(payload, ensure_ascii=False)},
    ]


def _clean_evidence(raw: Any, known: set[str]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [], ["evidence 不是列表"]
    kept: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value not in known:
            warnings.append(f"引用了候选外的 id：{value or '（空）'}")
            continue
        if value in kept:
            continue
        kept.append(value)
    return kept, warnings


def parse(
    payload: Any, articles: list[Article]
) -> tuple[list[Hotspot], list[tuple[str, str]], list[str]]:
    known = {article.id for article in articles}
    if not isinstance(payload, dict):
        return [], [], ["热点输出不是 JSON 对象，已整体忽略"]
    rows = payload.get("hotspots")
    if not isinstance(rows, list):
        return [], [], ["热点输出缺少 hotspots 数组，已整体忽略"]

    items: list[Hotspot] = []
    rejected: list[tuple[str, str]] = []
    notes: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            rejected.append(("（条目）", "热点不是对象"))
            continue
        title = collapse(str(row.get("title") or ""))
        body = collapse(str(row.get("body") or ""))
        if not title:
            rejected.append(("（条目）", "热点缺少 title"))
            continue
        if not body:
            rejected.append((title, "热点缺少 body"))
            continue
        if len(body) < BODY_MIN:
            notes.append(f"热点「{title}」偏短（{len(body)} 字）")
        if len(body) > BODY_MAX:
            body = truncate(body, BODY_MAX)
            notes.append(f"热点「{title}」超长，已截断")
        evidence, warnings = _clean_evidence(row.get("evidence"), known)
        if len(evidence) < EVIDENCE_MIN:
            rejected.append((title, f"证据不足（{len(evidence)} 篇，至少 {EVIDENCE_MIN} 篇）"))
            continue
        items.append(
            Hotspot(
                title=truncate(title, TITLE_MAX),
                body=body,
                evidence=evidence,
                warnings=warnings,
            )
        )
        for warning in warnings:
            notes.append(f"热点「{title}」：{warning}")
    if len(items) > MAX_ITEMS:
        notes.append(f"热点超过 {MAX_ITEMS} 条，已截断")
        items = items[:MAX_ITEMS]
    return items, rejected, notes


def annotate(
    config: Any,
    direction: str,
    articles: list[Article],
    *,
    logger: Any = None,
    model: str | None = None,
    usage: dict | None = None,
) -> tuple[list[Hotspot], dict[str, Any], list[tuple[str, str]], list[str], str]:
    from . import llm

    name = model or str(config.get("llm.model", "deepseek-chat"))
    messages = build_messages(
        direction,
        articles,
        max_abstract_chars=int(config.get("weekly.max_abstract_chars", 900) or 900),
    )
    payload = llm.chat_json(config, messages, logger=logger, usage=usage)
    items, rejected, notes = parse(payload, articles)
    stored = {
        "schema": 2,
        "model": name,
        "screening": "hotspots-v1",
        "hotspots": [item.to_dict() for item in items],
    }
    return items, stored, rejected, notes, f"hotspots-v1（{name}）"
