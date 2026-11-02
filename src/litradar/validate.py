"""Verification of model output — the part that keeps the digest honest.

Seven rules, all degradations instead of crashes.  Nothing here ever blocks a
report: a rejected item disappears from the highlighted list but the article itself
still appears in the full list, because the full list is produced by the engine, not
by the model.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .model import Article, normalize_doi
from .util import similar

VERBATIM_MIN_SIMILARITY = 0.90
ABSTRACT_MODES = ("verbatim", "faithful-summary", "none")


@dataclass
class Result:
    id: str
    relevant: bool = False
    relevance: float | None = None
    why: str | None = None
    topic_tags: list[str] = field(default_factory=list)
    abstract_en: str | None = None
    abstract_mode: str = "none"
    conclusion: str | None = None
    conclusion_source: str | None = None
    summary_zh: str | None = None
    doi: str | None = None
    notes: list[str] = field(default_factory=list)
    model_supplied: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "relevant": self.relevant,
            "relevance": self.relevance,
            "why": self.why,
            "topic_tags": self.topic_tags,
            "abstract_en": self.abstract_en,
            "abstract_mode": self.abstract_mode,
            "conclusion": self.conclusion,
            "conclusion_source": self.conclusion_source,
            "summary_zh": self.summary_zh,
            "doi": self.doi,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Result:
        return cls(
            id=str(data.get("id") or ""),
            relevant=bool(data.get("relevant")),
            relevance=data.get("relevance"),
            why=data.get("why"),
            topic_tags=list(data.get("topic_tags") or []),
            abstract_en=data.get("abstract_en"),
            abstract_mode=data.get("abstract_mode") or "none",
            conclusion=data.get("conclusion"),
            conclusion_source=data.get("conclusion_source"),
            summary_zh=data.get("summary_zh"),
            doi=data.get("doi"),
        )


@dataclass
class ValidationReport:
    results: dict[str, Result] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": {key: value.to_dict() for key, value in self.results.items()},
            "rejected": [{"id": i, "reason": r} for i, r in self.rejected],
            "notes": list(self.notes),
            "missing": list(self.missing),
        }


def host_allowed(url: str | None, allowed_hosts: set[str]) -> bool:
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


def default_hosts(items: list[Article], journals: list[dict] | None = None) -> set[str]:
    hosts: set[str] = {"doi.org"}
    for article in items:
        parsed = urllib.parse.urlparse(article.url or "")
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    for journal in journals or []:
        for key in ("url", "feed", "base_url"):
            value = journal.get(key)
            if not value:
                continue
            parsed = urllib.parse.urlparse(str(value))
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
    return hosts


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_results(
    raw_results: list[dict],
    items: list[Article],
    allowed_hosts: set[str],
    *,
    max_tags: int = 5,
    max_why_chars: int = 200,
) -> ValidationReport:
    """Apply the seven contract rules to one ``screen.json`` payload."""
    report = ValidationReport()
    candidates = {article.id: article for article in items}
    seen_ids: set[str] = set()

    for entry in raw_results:
        if not isinstance(entry, dict):
            report.notes.append("丢弃：results 里出现非对象条目")
            continue
        raw_id = str(entry.get("id") or "")
        candidate = candidates.get(raw_id)
        if candidate is None:
            # Rule 1: unknown id — either hallucinated or from a stale run.
            report.rejected.append((raw_id or "<空>", "不在本轮候选集内（可能是编造或过期结果）"))
            continue
        if raw_id in seen_ids:
            report.notes.append(f"重复条目已忽略：{raw_id}")
            continue
        seen_ids.add(raw_id)

        # Rule 6: an item without an openable URL cannot be shown.
        if not candidate.url or urllib.parse.urlparse(candidate.url).scheme not in ("http", "https"):
            report.rejected.append((raw_id, "候选条目缺少可打开的原文链接"))
            continue

        result = Result(id=raw_id, relevant=bool(entry.get("relevant")))
        result.doi = candidate.doi

        # Rule 7: relevance outside [0, 1] is clamped, with a note.
        relevance = _coerce_float(entry.get("relevance"))
        if relevance is None:
            result.notes.append("relevance 缺失或非数字")
            result.relevance = None
        elif relevance < 0 or relevance > 1:
            clamped = min(1.0, max(0.0, relevance))
            result.notes.append(f"relevance {relevance:g} 越界，已夹到 {clamped:g}")
            result.relevance = clamped
        else:
            result.relevance = relevance

        why = entry.get("why")
        result.why = str(why).strip()[:max_why_chars] if why else None
        summary_zh = entry.get("summary_zh")
        if summary_zh:
            result.summary_zh = str(summary_zh).strip()[:160] or None
        tags = entry.get("topic_tags") or []
        if isinstance(tags, list):
            result.topic_tags = [str(tag).strip() for tag in tags if str(tag).strip()][:max_tags]

        # Rule 2: a DOI supplied by the model must match the candidate's.
        model_doi = normalize_doi(entry.get("doi"))
        if entry.get("doi") and model_doi != candidate.doi:
            result.notes.append("模型给出的 DOI 与候选不一致，已忽略该 DOI")
            result.doi = candidate.doi

        mode = str(entry.get("abstract_mode") or "none").strip()
        if mode not in ABSTRACT_MODES:
            result.notes.append(f"abstract_mode {mode!r} 非法，按 none 处理")
            mode = "none"
        abstract = entry.get("abstract_en")
        abstract = str(abstract).strip() if abstract else None

        if mode == "verbatim":
            if not candidate.abstract_raw:
                # Rule 4: cannot be a verbatim copy of something that does not exist.
                mode = "none"
                abstract = None
                result.notes.append("候选没有摘要却声称 verbatim，已降级为 none")
            else:
                score = similar(abstract or "", candidate.abstract_raw)
                if score < VERBATIM_MIN_SIMILARITY:
                    # Rule 3: not a faithful copy -> call it what it is.
                    result.notes.append(
                        f"标为原文但与原摘要不一致（相似度 {score:.2f}），已降级为概述"
                    )
                    mode = "faithful-summary"
                    if not abstract:
                        mode = "none"
        elif mode == "faithful-summary" and not candidate.abstract_raw:
            mode = "none"
            abstract = None
            result.notes.append("候选没有摘要，概述无从谈起，已降级为 none")
        if not abstract and mode != "none":
            mode = "none"

        result.abstract_mode = mode
        result.abstract_en = abstract

        # Rule 5: a conclusion is only allowed with a whitelisted source.
        conclusion = entry.get("conclusion")
        conclusion = str(conclusion).strip() if conclusion else None
        source = entry.get("conclusion_source")
        source = str(source).strip() if source else None
        if conclusion:
            if not source or not host_allowed(source, allowed_hosts):
                result.notes.append("结论缺少白名单内的来源，已隐藏该结论")
            else:
                result.conclusion = conclusion
                result.conclusion_source = source

        for note in result.notes:
            report.notes.append(f"{raw_id}: {note}")
        report.results[raw_id] = result

    for article in items:
        if article.id not in report.results:
            report.missing.append(article.id)
    if report.missing:
        report.notes.append(
            f"模型漏掉了 {len(report.missing)} 条候选；这些条目仍会进入正文，但不参与置顶"
        )
    return report
