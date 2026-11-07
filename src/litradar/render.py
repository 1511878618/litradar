"""Markdown report rendering.

The skeleton is a contract: front-matter keys and the four ``##`` headings are what
front-ends may rely on.  Everything else is presentation.

Two rules shape the output:

* the body contains **every** candidate of the window, no matter what the model said;
* audit sections (out-of-window, unverified, excluded types, validation notes) are
  never silently dropped — a missing article must be explainable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .deepread import DeepRead
from .hotspots import Hotspot
from .llm import cost_cny
from .model import Article, JournalScan
from .paths import Paths
from .tags import compiled_rules, tag_article, topic_counts
from .util import collapse, iso, slugify, truncate, tzinfo
from .validate import Result, ValidationReport
from .window import Window

STATUS_LABELS = {
    "ok": "✅ 已读",
    "metadata-only": "⚠ 仅元数据",
    "blocked": "⛔ 被拦截",
    "timeout": "⏱ 超时",
    "error": "❌ 解析失败",
    "off-cycle": "🛌 非出版日",
}
EMBED_WEIGHT = 0.3
LLM_WEIGHT = 0.7


@dataclass
class ReportInput:
    run_id: str
    window: Window
    scans: list[JournalScan]
    items: list[Article]
    results: dict[str, Result] = field(default_factory=dict)
    already_seen: list[Article] = field(default_factory=list)
    out_of_window: list[Article] = field(default_factory=list)
    date_unverified: list[Article] = field(default_factory=list)
    excluded_type: list[Article] = field(default_factory=list)
    validation: ValidationReport = field(default_factory=ValidationReport)
    screening_label: str = "keyword"
    screening_note: str | None = None
    scan_at: str = ""
    generated_at: str = ""
    config: Config | None = None
    scores: dict[str, float] | None = None
    journals: dict[str, dict] = field(default_factory=dict)
    scan_history: dict[str, dict] = field(default_factory=dict)
    engine_version: str = __version__
    kind: str = "journal"
    wordcloud_path: str | None = None
    deepread: dict[str, DeepRead] = field(default_factory=dict)
    deepread_label: str = "off"
    deepread_note: str | None = None
    hotspots: list[Hotspot] = field(default_factory=list)
    hotspots_label: str = "off"
    hotspots_note: str | None = None
    usage: dict = field(default_factory=dict)
    weekly_missing: list[str] = field(default_factory=list)
    variant: str = "main"
    sibling_link: str | None = None
    title_translations: dict[str, str] = field(default_factory=dict)
    translation_note: str = ""


# --------------------------------------------------------------------------- coverage
def coverage_of(scans: list[JournalScan]) -> str:
    """``complete`` only when every journal was read off the publisher's surface."""
    if not scans:
        return "failed"
    if not any(scan.healthy for scan in scans):
        return "failed"
    if all(scan.complete for scan in scans):
        return "complete"
    return "partial"


def is_weekly(data: ReportInput) -> bool:
    return data.kind == "weekly" or data.window.kind in ("weekly", "week_to_date")


def report_title(data: ReportInput) -> str:
    suffix = " · 预印本" if data.variant == "preprint" else ""
    if is_weekly(data):
        return f"文献雷达 {data.window.week_label} 周报{suffix}"
    return "文献雷达 " + data.window.label + suffix


def journals_str(scans: list[JournalScan]) -> str:
    return "、".join(scan.journal for scan in scans) if scans else "（无）"


def journal_meta(data: ReportInput, name: str) -> dict:
    return (data.journals or {}).get(name) or {}


def impact_factor(data: ReportInput, name: str) -> float:
    try:
        return float(journal_meta(data, name).get("impact_factor") or 0)
    except (TypeError, ValueError):
        return 0.0


def is_preprint_journal(data: ReportInput, name: str) -> bool:
    if journal_meta(data, name).get("preprint"):
        return True
    return any(
        article.journal == name and (article.extra or {}).get("preprint")
        for article in data.items
    )


def journal_order(data: ReportInput) -> list[str]:
    """Impact factor descending; preprint servers always last."""
    names: list[str] = []
    for scan in data.scans:
        if scan.journal not in names:
            names.append(scan.journal)
    for article in data.items:
        if article.journal not in names:
            names.append(article.journal)
    return sorted(
        names,
        key=lambda name: (
            1 if is_preprint_journal(data, name) else 0,
            -impact_factor(data, name),
            name,
        ),
    )


def journal_heading_level(data: ReportInput, name: str) -> int:
    """Heading depth is a per-journal property (``heading_level``), else IF-derived."""
    explicit = journal_meta(data, name).get("heading_level")
    if explicit not in (None, ""):
        try:
            return min(6, max(3, int(explicit)))
        except (TypeError, ValueError):
            pass
    if is_preprint_journal(data, name):
        return 5
    value = impact_factor(data, name)
    if value >= 20:
        return 3
    if value >= 12:
        return 4
    return 5


# --------------------------------------------------------------------------- ranking
def _keyword_score(article: Article, cfg: Config | None) -> float:
    from .screening import keyword_hits

    keywords = list(cfg.get("screening.keywords", []) if cfg else [])
    hits = keyword_hits(article, keywords)
    if not hits:
        return 0.0
    return min(1.0, 0.45 + 0.12 * len(hits))


def keyword_weight(cfg: Config | None) -> float:
    try:
        return min(1.0, max(0.0, float(cfg.get("screening.keyword_weight", 0.5)))) if cfg else 0.5
    except (TypeError, ValueError):
        return 0.5


def profile_keywords(cfg: Config | None) -> list[str]:
    return [str(word) for word in (cfg.get("screening.keywords", []) if cfg else []) if str(word)]


def _base_final_score(article: Article, result: Result | None, scores: dict[str, float] | None, cfg: Config | None) -> float:
    from .screening import keyword_overlap

    llm = None
    if result is not None:
        if result.relevance is not None:
            llm = float(result.relevance)
        elif result.relevant:
            llm = 0.7
    # The model only extracts keywords; this blend is what makes the ranking
    # reproducible: same keywords + same profile => same score.
    matched = keyword_overlap(result.keywords if result else [], profile_keywords(cfg))
    base = llm
    if matched is not None and llm is not None:
        weight = keyword_weight(cfg)
        base = (1 - weight) * llm + weight * matched
    elif matched is not None:
        base = matched

    embed = None
    if scores and article.doi and article.doi in scores:
        embed = float(scores[article.doi])
    if base is not None and embed is not None:
        return LLM_WEIGHT * base + EMBED_WEIGHT * embed
    if base is not None:
        return base
    if embed is not None:
        return embed
    return _keyword_score(article, cfg)


def final_score(article, result, scores=None, cfg=None):
    base = _base_final_score(article, result, scores, cfg)
    return max(0.0, min(1.0, base + float(article.extra.get("feedback_adjustment", 0))))


def highlight_limit(cfg: Config | None) -> int:
    try:
        return max(0, int(cfg.get("report.highlights", 8)) if cfg else 8)
    except (TypeError, ValueError):
        return 8


def select_highlights(data: ReportInput) -> list[Article]:
    limit = highlight_limit(data.config)
    min_relevance = 0.6
    if data.config is not None:
        try:
            min_relevance = float(data.config.get("screening.min_relevance", 0.6))
        except (TypeError, ValueError):
            min_relevance = 0.6
    scored: list[tuple[float, Article]] = []
    for article in data.items:
        if article.extra.get("superseded_by"):
            continue
        result = data.results.get(article.id)
        score = final_score(article, result, data.scores, data.config)
        # ``screening.min_relevance`` is authoritative; the model's boolean is
        # advisory and only used when it supplied no score at all.
        relevant = score >= min_relevance or bool(
            result and result.relevant and result.relevance is None
            and float(article.extra.get("feedback_adjustment", 0)) >= 0
        )
        if relevant:
            scored.append((score, article))
    scored.sort(
        key=lambda pair: (
            pair[0],
            impact_factor(data, pair[1].journal),
            pair[1].online_date or date.min,
        ),
        reverse=True,
    )
    return [article for _, article in scored[:limit]]


# --------------------------------------------------------------------------- helpers
def _fmt_time(value: str | None, tz: str) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tzinfo("UTC"))
        return parsed.astimezone(tzinfo(tz)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _fmt_date_short(value: str | None, tz: str) -> str:
    rendered = _fmt_time(value, tz)
    return rendered[:10] if rendered != "—" else "—"


def image_markdown(article: Article) -> str | None:
    """Embedded figure, if the image step managed to attach one."""
    extra = article.extra or {}
    target = extra.get("image_path") or extra.get("image_url")
    if not target:
        return None
    import re as _re

    alt = _re.sub(r"[\[\]()]", " ", article.title or "").strip()
    return f"![{alt}]({target})"


def is_preprint(article: Article) -> bool:
    return bool((article.extra or {}).get("preprint"))


def article_type_label(article: Article) -> str:
    if article.article_type:
        return article.article_type
    if is_preprint(article):
        return "Preprint"
    if article.content_type == "review":
        return "Review"
    if article.content_type == "research-article":
        return "Research Article"
    return "Other"


def _date_cell(article: Article) -> str:
    if article.online_date is None:
        return "官网未标注"
    text = article.online_date.isoformat()
    if article.date_source == "crossref":
        text += "（元数据日期，未在官网核实）"
    return text


def abstract_limit(config: Config | None) -> int:
    try:
        return max(200, int(config.get("report.abstract_chars", 1500) if config else 1500))
    except (TypeError, ValueError):
        return 1500


def _abstract_or_placeholder(
    article: Article, result: Result | None, limit: int = 1500
) -> str:
    if result is not None and result.abstract_mode in ("verbatim", "faithful-summary") and result.abstract_en:
        prefix = "（概述）" if result.abstract_mode == "faithful-summary" else ""
        return prefix + truncate(collapse(result.abstract_en), limit)
    if article.abstract_raw:
        return truncate(collapse(article.abstract_raw), limit)
    return "No abstract supplied by the journal"


def _authors_line(article: Article) -> str:
    if not article.authors:
        return "未提供"
    shown = article.authors[:3]
    suffix = " 等" if len(article.authors) > 3 else ""
    return "、".join(shown) + suffix


def _doi_link(article: Article) -> str | None:
    return f"https://doi.org/{article.doi}" if article.doi else None


def _fm_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def translated_title(data: ReportInput, article: Article) -> str:
    deep = data.deepread.get(article.id)
    value = (deep.title_zh if deep and deep.title_zh else data.title_translations.get(article.id))
    if not value:
        return article.title
    # Translation is plain text, never Markdown supplied by a remote service.
    value = collapse(value)
    return re.sub(r"([\\`*_{\[\]<>#|])", r"\\\1", value)


# --------------------------------------------------------------------------- sections
def render_frontmatter(data: ReportInput, coverage: str, highlight_count: int) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    generated = data.generated_at or iso()
    rows = [
        f"title: {_fm_value(report_title(data))}",
        "cssclasses: [litradar-report]",
        f"kind: {'weekly' if is_weekly(data) else 'daily'}",
        f"date: {_fmt_date_short(generated, tz)}",
        f"tags: [文献雷达/{'weekly' if is_weekly(data) else 'daily'}]",
        f"direction: {_fm_value(data.config.direction if data.config else '')}",
        "journals: ["
        + ", ".join(
            _fm_value(name)
            for name in (journal_order(data) if data.journals else [scan.journal for scan in data.scans])
        )
        + "]",
        f"window: {_fm_value(data.window.label)}",
        f"coverage: {coverage}",
        f"screening: {_fm_value(data.screening_label)}",
        f"deepread: {_fm_value(data.deepread_label)}",
        f"new_items: {len(data.items)}",
        f"journal_items: {sum(1 for article in data.items if not is_preprint(article))}",
        f"preprint_items: {sum(1 for article in data.items if is_preprint(article))}",
        f"highlights: {highlight_count}",
        f"hotspots: {len(data.hotspots)}",
        f"run_id: {_fm_value(data.run_id)}",
    ]
    return "---\n" + "\n".join(rows) + "\n---\n"


def render_header(data: ReportInput, coverage: str) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    if data.window.single_day:
        window_line = (
            f"严格时间窗：{data.window.kind} → {data.window.start}（含两端）"
        )
    else:
        window_line = (
            f"严格时间窗：{data.window.start} ~ {data.window.end}"
            f"（含两端，共 {data.window.days} 天）"
        )
    lines = [
        f"# {report_title(data)}",
        "",
        "> [!abstract] 本期速览",
        f"> **{len(data.items)} 篇收录** · **{sum(not is_preprint(a) for a in data.items)} 篇期刊** · **{sum(is_preprint(a) for a in data.items)} 篇预印本**",
        ">",
        f"> 扫描时间：{_fmt_time(data.scan_at, tz)}（{tz}）",
        f"> {window_line}",
        "",
        "> [!info]- 收录范围与运行说明",
    ]
    if is_weekly(data):
        lines.append("> 统计口径：本周（周一 ~ 汇总日）全量汇总；日报是流水，周报只做合并与排序")
        if data.weekly_missing:
            missing = "、".join(data.weekly_missing)
            lines.append(
                f"> ⚠️ 这一周有 {len(data.weekly_missing)} 天没有抓取记录（{missing}），"
                "周报里不包含那几天。"
            )
    if data.variant == "preprint":
        scope_line = "> 收录范围：窗口内的**全部**预印本；相关性只决定置顶，不决定取舍"
        preprint_line = "> 本文件只含预印本（bioRxiv / medRxiv），未经同行评审"
    elif is_weekly(data):
        scope_line = "> 收录范围：本周各日日报的全量文章（合并去重）；相关性只决定置顶，不决定取舍"
        preprint_line = (
            "> 预印本（bioRxiv / medRxiv）单独成文，未经同行评审"
            if data.sibling_link
            else "> 预印本（bioRxiv / medRxiv）单列在「预印本」一节，未经同行评审"
        )
    else:
        scope_line = (
            "> 收录范围：窗口内、官网可见、且此前未报告过的**全部**文章；"
            "相关性只决定置顶，不决定取舍"
        )
        preprint_line = (
            "> 预印本（bioRxiv / medRxiv）单独成文，未经同行评审"
            if data.sibling_link
            else "> 预印本（bioRxiv / medRxiv）单列在「预印本」一节，未经同行评审"
        )
    lines += [
        f"> 本期刊：{journals_str(data.scans)}",
        f"> 研究方向：{data.config.direction if data.config else '（未填写）'}",
        scope_line,
        preprint_line,
        f"> 置顶依据：{data.screening_label}",
    ]
    if data.deepread_label and data.deepread_label != "off":
        lines.append(f"> 精读依据：{data.deepread_label}（机器翻译与概括，原文见每篇末）")
    if data.deepread_note:
        lines.append(f"> 注意：{data.deepread_note}")
    if data.hotspots_label and data.hotspots_label != "off":
        lines.append(f"> 热点依据：{data.hotspots_label}（每条都带论文编号，可回查）")
    if data.sibling_link:
        label = "期刊部分" if data.variant == "preprint" else "预印本部分"
        lines.append(f"> {label}单独成文：[[{data.sibling_link}]]")
    if data.translation_note:
        lines.extend(["", "> [!note] 中文标题", f"> {data.translation_note}"])
    if data.screening_note:
        lines.extend(["", "> [!warning] 筛选说明", f"> {data.screening_note}"])
    if coverage == "failed":
        lines.extend(["", "> [!danger] 抓取失败"])
        lines.append(
            "> ⛔ **本轮全部期刊抓取失败**：日报为空不代表没有新文章，请先看「覆盖情况」。"
        )
    lines.append("")
    return "\n".join(lines)


def render_highlights(data: ReportInput, highlights: list[Article]) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    lines = [f"## 与你最相关（{len(highlights)} 篇）", ""]
    if not highlights:
        lines.append("> 本轮没有达到相关阈值的条目；正文仍是全量列表。")
        lines.append("")
        return "\n".join(lines)
    for index, article in enumerate(highlights, start=1):
        result = data.results.get(article.id)
        deep = data.deepread.get(article.id)
        title = translated_title(data, article)
        lines.append(f"### {index}. {title}")
        lines.extend(["", "> [!tip] 优先阅读", f"> {article.journal} · {_date_cell(article)}", ""])
        figure = image_markdown(article)
        if figure:
            lines.append("")
            lines.append(figure)
        if deep and deep.digest_zh:
            # The digest is the part a human reads; keep it as a paragraph, not a bullet.
            lines.append("")
            lines.append(deep.digest_zh)
        lines.append("")
        if deep and deep.field_zh:
            lines.append(f"- 研究领域：{deep.field_zh}")
        if deep and deep.keywords_zh:
            lines.append("- 关键词：" + " · ".join(deep.keywords_zh))
        lines.append(f"- 期刊：{article.journal}")
        lines.append(f"- 文章类型：{article_type_label(article)}")
        if title != article.title:
            lines.append(f"- 原文标题：{article.title}")
        if article.surface:
            lines.append(f"- 出版商状态：{article.surface}")
        if is_preprint(article):
            lines.append("- 预印本：是（未经同行评审，结论需自行核实）")
        if is_preprint(article):
            version = (article.extra or {}).get("version")
            suffix = f"（v{version}）" if version else ""
            lines.append(f"- 预印本发布日期：{_date_cell(article)}{suffix}")
        else:
            lines.append(f"- 官网首次在线日期：{_date_cell(article)}")
        lines.append(f"- 首次观察时间：{_fmt_time(article.first_seen_at or data.scan_at, tz)}")
        doi_link = _doi_link(article)
        if doi_link:
            lines.append(f"- DOI: {doi_link}")
        lines.append(f"- 作者：{_authors_line(article)}")
        why = (result.why if result and result.why else "").strip()
        if result is not None and result.relevance is not None:
            lines.append(f"- 相关性：{result.relevance:.2f}" + (f" —— {why}" if why else ""))
        elif why:
            lines.append(f"- 相关性：—— {why}")
        else:
            lines.append("- 相关性：未评分")
        tags = (result.topic_tags if result and result.topic_tags else [])
        if not tags:
            tags = (article.extra or {}).get("tags_auto") or []
        if tags and not (deep and deep.keywords_zh):
            lines.append("- 主题：" + " · ".join(tags))
        if result and result.keywords and not (deep and deep.keywords_zh):
            lines.append("- 关键词：" + " · ".join(result.keywords))
        if result and result.summary_zh and not (deep and deep.digest_zh):
            lines.append(f"- 一句话解读：{result.summary_zh}")
        lines.append(
            f"- 英文摘要：{_abstract_or_placeholder(article, result, abstract_limit(data.config))}"
        )
        lines.append(f"- 可见性来源：{article.url}")
        lines.append(f"- 摘要来源：{article.abstract_source or article.url}")
        if result and result.conclusion:
            lines.append(f"- 原文结论：{result.conclusion}（{result.conclusion_source}）")
        lines.append("")
    return "\n".join(lines)


def render_full_list(data: ReportInput, highlight_ids: set[str] | None = None) -> str:
    highlight_ids = highlight_ids or set()
    if not data.items:
        return "\n".join([
            f"## {'本周全部收录' if is_weekly(data) else '全部新上线'}（0 篇）",
            "",
            "> 本轮窗口内没有新文章。先确认文末覆盖表没有失败项。",
            "",
        ])

    lines: list[str] = []
    for label, want_preprint in (("期刊", False), ("预印本", True)):
        subset = [article for article in data.items if is_preprint(article) is want_preprint]
        if not subset:
            continue
        heading = f"本周{label}" if is_weekly(data) else label
        lines.append(f"## {heading}（{len(subset)} 篇）")
        lines.append("")
        order = [name for name in journal_order(data)
                 if any(article.journal == name for article in subset)]
        grouped: dict[str, list[Article]] = {name: [] for name in order}
        for article in subset:
            grouped.setdefault(article.journal, []).append(article)
        for journal in order:
            entries = grouped.get(journal) or []
            if not entries:
                continue
            entries.sort(key=lambda item: (item.online_date or date.min), reverse=True)
            level = journal_heading_level(data, journal)
            item_level = min(level + 1, 6)
            lines.append(f"{'#' * level} {journal}（{len(entries)} 篇）")
            lines.append("")
            for article in entries:
                links = [f"[原文]({article.url})"]
                doi_link = _doi_link(article)
                if doi_link:
                    links.append(f"[DOI]({doi_link})")
                meta = [article.online_date.isoformat() if article.online_date else "官网未标注"]
                if article.online_date and article.date_source == "crossref":
                    meta[0] += "（元数据日期，未在官网核实）"
                if article.surface:
                    meta.append(article.surface)
                label = article_type_label(article)
                if label and not (is_preprint(article) and label.casefold() == "preprint"):
                    meta.append(label)
                if is_preprint(article):
                    version = (article.extra or {}).get("version")
                    meta.append("预印本·未经同行评审" + (f" · v{version}" if version else ""))
                auto_tags = (article.extra or {}).get("tags_auto") or []
                if auto_tags:
                    meta.append("标签：" + "、".join(auto_tags))
                title = translated_title(data, article)
                lines.append(f"{'#' * item_level} {title}")
                lines.append("")
                kind = "warning" if is_preprint(article) else "info"
                label = "预印本 · 未经同行评审" if is_preprint(article) else "期刊论文"
                lines.append(f"> [!{kind}] {label}")
                if title != article.title:
                    lines.append("> **原文标题**：" + collapse(article.title))
                    lines.append(">")
                if article.extra.get("late_arrival"):
                    lines.append("> **补收录**：来源延迟登记，按原发表日期归档")
                if article.extra.get("feedback_reason"):
                    lines.append("> 推荐调整：" + article.extra["feedback_reason"])
                for version in article.extra.get("versions", []):
                    lines.append(f"> 关联版本：[DOI](https://doi.org/{version['doi']})（{version['evidence']}）")
                lines.append("> " + " · ".join(meta + links))
                lines.append(">")
                figure = image_markdown(article) if article.id not in highlight_ids else None
                if figure:
                    lines.append("> " + figure)
                    lines.append(">")
                result = data.results.get(article.id)
                abstract = _abstract_or_placeholder(article, result, abstract_limit(data.config))
                lines.append("> **摘要** · " + abstract.replace("\n", " "))
                lines.append("")
                if result and result.relevant and result.why:
                    lines.append(f"> 相关：{result.why}")
                    lines.append("")
    return "\n".join(lines)


def render_coverage(data: ReportInput, coverage: str) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    lines = ["## 覆盖情况", ""]
    wrote = False
    for label, want_preprint in (("期刊", False), ("预印本", True)):
        scans = [
            scan for scan in data.scans
            if is_preprint_journal(data, scan.journal) is want_preprint
        ]
        if not scans:
            continue
        wrote = True
        scans.sort(
            key=lambda scan: (-impact_factor(data, scan.journal), scan.journal)
        )
        lines += [
            f"### {label}",
            "",
            "| 期刊 | 影响因子 | 官网栏目 | 覆盖状态 | 本轮条目 | 上次成功扫描 |",
            "|---|---|---|---|---|---|",
        ]
        for scan in scans:
            status = STATUS_LABELS.get(scan.status, scan.status)
            if scan.reason and scan.status != "ok":
                status += f"（{scan.reason}）"
            history = data.scan_history.get(scan.journal) or {}
            factor = impact_factor(data, scan.journal)
            factor_cell = "—" if factor <= 0 else f"{factor:g}"
            last_ok = history.get("last_ok_at")
            rows = [
                scan.journal,
                factor_cell,
                scan.surface or "—",
                status,
                str(scan.items_seen),
                _fmt_time(last_ok, tz) if last_ok else "—",
            ]
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in rows) + " |")
        lines.append("")

        notes = [
            f"{scan.journal}：{scan.reason}"
            for scan in scans
            if scan.status == "ok" and scan.reason
        ]
        if notes:
            lines.append("> 说明：" + "；".join(notes))
            lines.append("")

        kind_coverage = coverage_of(scans)
        if kind_coverage == "partial":
            degraded = [
                f"{scan.journal}（{scan.reason or STATUS_LABELS.get(scan.status, scan.status)}）"
                for scan in scans
                if not scan.complete
            ]
            lines.append(
                "> ⚠️ **partial coverage**：" + "、".join(degraded)
                + "。这些刊的结论不可表述为「本周无新文章」。"
            )
            lines.append("")
        elif kind_coverage == "failed":
            lines.append(
                f"> ⛔ {label}全部抓取失败，本轮不包含任何{label}文章，"
                "且**不能**作为「无新文章」的证据。"
            )
            lines.append("")
    if not wrote:
        lines.append("> 本轮没有扫描任何期刊。")
        lines.append("")
    return "\n".join(lines)


def render_hotspots(data: ReportInput) -> str:
    """Weekly only: 2–5 trends, each pinned to the papers that support it."""
    if not data.hotspots:
        return ""
    by_id = {article.id: article for article in data.items}
    lines = ["## 本周热点", ""]
    for index, hotspot in enumerate(data.hotspots, start=1):
        lines.append(f"**{index}. {hotspot.title}**")
        lines.append("")
        lines.append(hotspot.body)
        lines.append("")
        anchors = []
        for item_id in hotspot.evidence:
            article = by_id.get(item_id)
            if article:
                anchors.append(f"{truncate(article.title, 70)}（{article.journal}）")
        if anchors:
            lines.append("> 依据：" + "；".join(anchors))
            lines.append("")
    if data.hotspots_note:
        lines.append(f"> 注：{data.hotspots_note}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_wordcloud(data: ReportInput) -> str:
    if not data.wordcloud_path:
        return ""
    label = "本周词云" if is_weekly(data) else "今日词云"
    return (f"**{label} · 当前文件收录的 {len(data.items)} 篇文献**\n\n"
            f"![{label}]({data.wordcloud_path})\n")


def render_topics(data: ReportInput) -> str:
    """Mermaid pie + counts table for the current subset, plus an optional word cloud."""
    config = data.config
    if not data.items:
        return ""
    if config is not None and not bool(config.get("report.topic_chart", True)):
        return render_wordcloud(data)
    counts, untagged = topic_counts(data.items)
    if not counts:
        return render_wordcloud(data)
    top = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
    topic_title = "本周主题分布" if is_weekly(data) else "今日主题分布"
    lines = [f"## {topic_title}（{len(data.items)} 篇）", "", "```mermaid", "pie showData",
             f"    title 主题分布（{len(data.items)} 篇）"]
    for tag, count in top:
        safe = tag.replace('"', "'")
        lines.append(f'    "{safe}" : {count}')
    lines += ["```", "", "| 主题 | 篇数 |", "|---|---|"]
    for tag, count in top:
        lines.append(f"| {tag.replace('|', '/')} | {count} |")
    lines.append("")
    lines.append("> 标签按标题/摘要关键词命中，一篇可命中多个，所以合计大于篇数。")
    lines.append("")
    if untagged:
        lines.append(f"> 未打标签 {untagged} 篇（标题里没有命中已配置的主题词）。")
        lines.append("")
    summary = " · ".join(f"**{tag}** {count}" for tag, count in top[:4])
    chart = ["> " + line if line else ">" for line in lines[2:]]
    # Keep the SVG outside the initially hidden callout. Live Preview can defer
    # image sizing in hidden content; a visible standalone embed is reliable.
    cloud = ["", render_wordcloud(data), ""] if data.wordcloud_path else []
    return "\n".join(lines[:2] + [summary, ""] + cloud +
                     ["> [!example]- 展开主题统计图表"] + chart)


def render_audit(data: ReportInput) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    lines: list[str] = []

    if data.out_of_window:
        lines.append("## 越窗（官网日期不在本轮窗口内，未计入）")
        lines.append("")
        for article in data.out_of_window:
            day = article.online_date.isoformat() if article.online_date else "无日期"
            lines.append(f"- {article.title} — {article.journal} · 官网日期 {day}")
        lines.append("")

    if data.date_unverified:
        lines.append("## 待核验（官网未给出可判定日期）")
        lines.append("")
        for article in data.date_unverified:
            lines.append(f"- {article.title} — {article.journal} · [原文]({article.url})")
        lines.append("")

    if data.excluded_type:
        lines.append(f"## 非研究类型（已排除 {len(data.excluded_type)} 篇）")
        lines.append("")
        for article in data.excluded_type:
            kind = article.article_type or article.content_type
            lines.append(f"- {article.title} — {article.journal} · {kind}")
        lines.append("")

    if data.already_seen:
        lines.append(f"## 已报告过（去重跳过 {len(data.already_seen)} 篇）")
        lines.append("")
        lines.append("> 这些文章在更早的日报中出现过，不重复计入。")
        lines.append("")
        for article in data.already_seen:
            first = _fmt_date_short(article.first_seen_at, tz)
            lines.append(f"- {article.title} — {article.journal} · 首次观察 {first}")
        lines.append("")

    notes = list(data.validation.notes)
    rejected = list(data.validation.rejected)
    if notes or rejected:
        lines.append("## 校验记录")
        lines.append("")
        for article_id, reason in rejected:
            lines.append(f"- 拒收：{article_id}: {reason}")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def render_footer(data: ReportInput) -> str:
    generated = data.generated_at or iso()
    line = f"*由 litradar v{data.engine_version} 生成 · {generated}*"
    usage = data.usage or {}
    if usage.get("calls"):
        estimated = "（估算）" if usage.get("estimated") else ""
        cost = cost_cny(usage, data.config) if data.config is not None else 0.0
        money = f" · 约 ¥{cost:.4f}" if cost else ""
        line += (
            f"\n*模型用量{estimated}：{usage['calls']} 次调用 · 输入 {usage['prompt_tokens']} tokens"
            f" · 输出 {usage['completion_tokens']} tokens{money}*"
        )
    return line + "\n"


def render_markdown(
    data: ReportInput, highlights: list[Article] | None = None
) -> tuple[str, list[Article]]:
    coverage = coverage_of(data.scans)
    rules = compiled_rules((data.config.get("tags.extra", {}) if data.config else {}) or {})
    for article in data.items:
        article.extra["tags_auto"] = tag_article(article, rules)
    if highlights is None:
        highlights = select_highlights(data)
    sections = [
        render_frontmatter(data, coverage, len(highlights)),
        render_header(data, coverage),
        render_highlights(data, highlights),
        render_hotspots(data),
        render_topics(data),
        render_full_list(data, {article.id for article in highlights}),
        render_coverage(data, coverage),
    ]
    audit = render_audit(data)
    if audit:
        sections.append(audit)
    sections.append(render_footer(data))
    # Exactly one blank line between sections, no trailing whitespace.
    markdown = "\n\n".join(
        section.rstrip("\n") for section in sections if section and section.strip()
    ) + "\n"
    return markdown, highlights


# --------------------------------------------------------------------------- index
INDEX_HEADER = "# 文献雷达索引"
DATAVIEW_BLOCK = (
    INDEX_HEADER
    + "\n\n```dataview\n"
    "TABLE new_items AS 篇数, highlights AS 置顶, coverage AS 覆盖\n"
    'FROM "02-Reading/文献雷达"\n'
    "SORT file.name DESC\n"
    "```\n"
)


def index_line(
    data: ReportInput, coverage: str, highlight_count: int, link_name: str | None = None
) -> str:
    tz = data.config.tz if data.config else "Asia/Shanghai"
    kind = "周报 · " if is_weekly(data) else ""
    return (
        f"- [[{link_name or data.run_id}]] · {kind}{len(data.items)} 篇（置顶 {highlight_count}）"
        f" · 覆盖 {coverage} · {_fmt_date_short(data.generated_at or iso(), tz)}"
    )


def update_index(text: str | None, line: str, run_id: str, mode: str = "append") -> str:
    if mode == "none":
        return text or ""
    if mode == "dataview":
        return DATAVIEW_BLOCK
    existing = (text or "").replace("\r\n", "\n")
    if not existing.strip():
        existing = INDEX_HEADER + "\n"
    lines = existing.split("\n")
    needle = f"[[{run_id}]]"
    for position, current in enumerate(lines):
        if needle in current and current.lstrip().startswith("- "):
            lines[position] = line
            break
    else:
        insert_at = 1
        for position, current in enumerate(lines):
            if current.strip() == INDEX_HEADER:
                insert_at = position + 1
                break
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        lines.insert(insert_at, line)
    # Keep the index newest-first, and put the journal row above the preprint row
    # when both exist for the same day.
    rows = [line for line in lines if line.lstrip().startswith("- [[")]
    if len(rows) > 1:
        def link_name(text: str) -> str:
            match = re.search(r"\[\[([^\]]+)\]\]", text)
            return match.group(1) if match else ""

        rows.sort(key=link_name, reverse=True)
        rows.sort(key=lambda text: 1 if link_name(text).endswith("-preprint") else 0)
        cursor = 0
        for index, line in enumerate(lines):
            if line.lstrip().startswith("- [["):
                lines[index] = rows[cursor]
                cursor += 1
    rendered = "\n".join(lines)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def write_index(paths: Paths, data: ReportInput, coverage: str, highlight_count: int) -> Path | None:
    mode = str(data.config.get("output.index_mode", "append") if data.config else "append")
    if mode == "none":
        return None
    path = paths.index_file()
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    from .state import atomic_write_text

    link_name = paths.report_file(data.window, data.variant).stem
    atomic_write_text(
        path,
        update_index(
            existing,
            index_line(data, coverage, highlight_count, link_name),
            link_name,
            mode,
        ),
    )
    return path


# --------------------------------------------------------------------------- betternotes
def betternote_paths(paths: Paths, highlights: list[Article], config: Config | None) -> list[tuple[Path, Article]]:
    directory = str(config.get("output.betternote_dir", "") if config else "").strip()
    if not directory:
        return []
    prefix = str(config.get("output.betternote_prefix", "@") if config else "@")
    planned: list[tuple[Path, Article]] = []
    for article in highlights:
        suffix = article.doi.split("/")[-1] if article.doi else slugify(article.title)[:60]
        suffix = "".join(char for char in suffix if char.isalnum() or char in "-_.")
        planned.append((paths.vault / directory / f"{prefix}{suffix}.md", article))
    return planned


def render_betternote(article: Article, result: Result | None, limit: int = 1500) -> str:
    lines = [
        "---",
        f"title: {_fm_value(article.title)}",
        f"journal: {_fm_value(article.journal)}",
        f"date: {article.online_date.isoformat() if article.online_date else ''}",
        f"doi: {article.doi or ''}",
        f"url: {article.url}",
        "tags: unread",
        "rating:",
        "comment:",
        "---",
        "",
        f"# {article.title}",
        "",
        _abstract_or_placeholder(article, result, limit),
        "",
    ]
    return "\n".join(lines)


def write_betternotes(
    paths: Paths, highlights: list[Article], results: dict[str, Result], config: Config | None
) -> list[Path]:
    from .state import atomic_write_text

    written: list[Path] = []
    for path, article in betternote_paths(paths, highlights, config):
        if path.exists():  # never clobber a note the user may have edited
            continue
        atomic_write_text(
            path,
            render_betternote(article, results.get(article.id), abstract_limit(config)),
        )
        written.append(path)
    return written


# --------------------------------------------------------------------------- writing
def write_report(paths: Paths, data: ReportInput, markdown: str) -> Path:
    from .state import atomic_write_text

    target = paths.report_file(data.window, data.variant)
    atomic_write_text(target, markdown)
    if data.variant == "main":
        atomic_write_text(paths.report_copy(data.run_id), markdown)
    return target


def subset_report(data: ReportInput, *, want_preprint: bool, variant: str) -> ReportInput:
    """One file's worth of a split report (journals or preprints only)."""
    items = [article for article in data.items if is_preprint(article) is want_preprint]
    scans = [
        scan for scan in data.scans
        if is_preprint_journal(data, scan.journal) is want_preprint
    ]
    ids = {article.id for article in items}
    deepread = {key: value for key, value in data.deepread.items() if key in ids}
    hotspots = []
    for hotspot in data.hotspots:
        evidence = [item_id for item_id in hotspot.evidence if item_id in ids]
        if len(evidence) >= 2:
            hotspots.append(replace(hotspot, evidence=evidence))
    subset = replace(
        data,
        items=items,
        scans=scans,
        deepread=deepread,
        hotspots=hotspots,
        variant=variant,
        sibling_link=None,
        wordcloud_path=data.wordcloud_path if variant == "main" else None,
    )
    return subset


def report_date_from_window(window: Window) -> date:
    return window.end


def parse_scan_time(value: str | None) -> str:
    return value or iso()


def load_scores(paths: Paths, run_id: str) -> dict[str, float] | None:
    from .state import read_json

    payload = read_json(paths.scores_file(run_id), default=None)
    if not isinstance(payload, dict):
        return None
    scores = payload.get("scores")
    if not isinstance(scores, dict):
        return None
    return {str(key): float(value) for key, value in scores.items() if isinstance(value, (int, float))}


def window_kind_label(window: Window) -> str:
    return window.kind


__all__ = [
    "ReportInput",
    "coverage_of",
    "final_score",
    "index_line",
    "load_scores",
    "render_markdown",
    "select_highlights",
    "update_index",
    "write_betternotes",
    "write_index",
    "write_report",
    "parse_scan_time",
    "report_date_from_window",
    "window_kind_label",
]
