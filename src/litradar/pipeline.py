"""The two-stage pipeline: ``fetch`` writes a store, ``render`` turns it into a report.

Splitting the stages is what makes the tool operable: a late network failure still
leaves today's raw results on disk, and a model outage degrades ranking only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import SCHEMA, __version__
from .config import Config, ConfigError
from .dedupe import dedupe, split_by_window
from .events import Emitter
from .fetcher import Fetcher
from .images import attach_images, images_enabled
from .logs import Logger, Stopwatch
from .model import Article, JournalScan
from .paths import Paths
from .render import (
    ReportInput,
    coverage_of,
    load_scores,
    render_markdown,
    select_highlights,
    write_betternotes,
    write_index,
    write_report,
)
from .screening import build_messages, extract_results, keyword_screen
from .state import (
    atomic_write_json,
    load_scans,
    load_seen,
    merge_scan_history,
    read_json,
    save_scans,
    save_seen,
)
from .util import EngineError, iso
from .validate import default_hosts, validate_results
from .window import Window


@dataclass
class Context:
    vault: Path
    config: Config
    paths: Paths
    logger: Logger
    emitter: Emitter
    fetcher: Fetcher
    tz: str = "Asia/Shanghai"

    def close(self) -> None:
        self.fetcher.close()
        self.logger.close()


@dataclass
class FetchSummary:
    run_id: str
    window: Window
    scans: list[JournalScan] = field(default_factory=list)
    new_items: list[Article] = field(default_factory=list)
    already_seen: list[Article] = field(default_factory=list)
    duplicates: int = 0
    store_path: Path | None = None

    @property
    def all_failed(self) -> bool:
        return not any(scan.healthy for scan in self.scans)

    @property
    def coverage(self) -> str:
        return coverage_of(self.scans)


@dataclass
class RenderSummary:
    run_id: str
    report_path: Path | None = None
    total: int = 0
    highlights: int = 0
    coverage: str = "failed"
    screening: str = "keyword"
    rejected: int = 0
    screen_rejected_all: bool = False
    images: int = 0
    kind: str = "journal"
    skipped: bool = False


# --------------------------------------------------------------------------- fetch
def resolve_journals(config: Config, names: list[str] | None = None) -> list[dict]:
    from .config import find_journal, load_journals

    journals = load_journals(config)
    if not names:
        return journals
    return [find_journal(journals, name) for name in names]


def _scan_failure(journal: dict, reason: str, status: str = "error") -> JournalScan:
    return JournalScan(
        journal=journal["name"],
        status=status,
        surface=journal.get("surface"),
        url=journal.get("url") or journal.get("feed"),
        items_seen=0,
        reason=reason,
        scanned_at=iso(),
    )


def _fetch_one(ctx: Context, journal: dict, window: Window) -> tuple[list[Article], JournalScan]:
    from .adapters.base import get_adapter

    try:
        adapter = get_adapter(journal, ctx.paths.vault)
    except Exception as exc:  # config error inside one journal must not kill the run
        return [], _scan_failure(journal, f"适配器不可用：{exc}")

    budget = float(ctx.config.get("http.per_journal_timeout_s", 60) or 60)
    try:
        ctx.fetcher.deadline = time.monotonic() + budget
        try:
            items, scan = adapter.fetch(journal, ctx.fetcher, window)
        except Exception as exc:  # noqa: BLE001 - a broken adapter is a failure, not a crash
            return [], _scan_failure(journal, f"适配器异常：{type(exc).__name__}: {exc}")

        if scan.healthy or not journal.get("fallback"):
            return items, scan

        for alternative in journal.get("fallback") or []:
            alternate = dict(journal)
            alternate["adapter"] = str(alternative).lower()
            ctx.fetcher.deadline = time.monotonic() + budget
            try:
                alt_adapter = get_adapter(alternate, ctx.paths.vault)
                alt_items, alt_scan = alt_adapter.fetch(alternate, ctx.fetcher, window)
            except Exception as exc:  # noqa: BLE001
                ctx.logger.warn(f"[{journal['name']}] 降级 {alternative} 失败：{exc}")
                continue
            if alt_scan.healthy:
                alt_scan.reason = (
                    f"主适配器 {journal['adapter']} 失败：{scan.reason or scan.status}"
                    f"；已降级到 {alternative}"
                )
                ctx.logger.info(f"[{journal['name']}] 已降级到 {alternative}")
                return alt_items, alt_scan
        return items, scan
    finally:
        ctx.fetcher.deadline = None


def fetch_all(
    ctx: Context,
    window: Window,
    journals: list[dict],
    *,
    budget_s: float = 600,
    save_state: bool = True,
) -> FetchSummary:
    ctx.paths.ensure()
    deadline = time.monotonic() + budget_s
    history = load_scans(ctx.paths.scans_file) if save_state else {}
    summary = FetchSummary(run_id=window.run_id, window=window)

    collected: list[Article] = []
    for journal in journals:
        if time.monotonic() > deadline:
            scan = _scan_failure(journal, f"全轮 fetch 超时（预算 {budget_s:g}s），本刊未执行")
            scan.status = "timeout"
            summary.scans.append(scan)
            ctx.logger.journal(journal["name"], "timeout", "全轮预算耗尽，跳过")
            continue

        ctx.emitter.emit("journal_start", journal=journal["name"], adapter=journal["adapter"])
        stopwatch = Stopwatch()
        items, scan = _fetch_one(ctx, journal, window)
        summary.scans.append(scan)
        collected.extend(items)
        ctx.logger.journal(
            journal["name"],
            scan.status,
            f"{scan.items_seen} 项" + (f" · {scan.reason}" if scan.reason else ""),
            stopwatch.ms,
        )
        ctx.emitter.emit(
            "journal_done",
            journal=journal["name"],
            status=scan.status,
            http=scan.http,
            items=scan.items_seen,
            ms=stopwatch.ms,
            reason=scan.reason,
        )
        if save_state:
            history[journal["name"]] = merge_scan_history(history.get(journal["name"]), scan.to_dict())
            save_scans(ctx.paths.scans_file, history)

    seen = load_seen(ctx.paths.seen_file) if save_state else {}
    include_types = ctx.config.get("screening.include_types") or ["research-article", "review"]
    buckets = split_by_window(collected, window, include_types)
    result = dedupe(buckets["in_window"], seen)
    summary.new_items = result.new
    summary.already_seen = result.already_seen
    summary.duplicates = result.duplicates

    now = iso()
    for article in summary.new_items:
        if not article.first_seen_at:
            article.first_seen_at = now

    store = {
        "schema": SCHEMA,
        "run_id": window.run_id,
        "engine": __version__,
        "scan_at": now,
        "window": window.to_dict(),
        "journals": [scan.to_dict() for scan in summary.scans],
        "items": [
            {**article.to_dict(), "already_seen": False} for article in summary.new_items
        ]
        + [{**article.to_dict(), "already_seen": True} for article in summary.already_seen],
        "date_unverified": [article.to_dict() for article in buckets["date_unverified"]],
        "out_of_window": [article.to_dict() for article in buckets["out_of_window"]],
        "excluded_type": [article.to_dict() for article in buckets["excluded_type"]],
    }
    if save_state:
        path = ctx.paths.store_file(window.run_id)
        atomic_write_json(path, store)
        summary.store_path = path
    return summary


# --------------------------------------------------------------------------- render
@dataclass
class LoadedStore:
    run_id: str
    window: Window
    scans: list[JournalScan]
    items: list[Article]
    already_seen: list[Article]
    date_unverified: list[Article]
    out_of_window: list[Article]
    excluded_type: list[Article]
    scan_at: str


def load_store(paths: Paths, run_id: str) -> LoadedStore:
    payload = read_json(paths.store_file(run_id), default=None)
    if not isinstance(payload, dict):
        raise EngineError(
            f"没有 {run_id} 的抓取结果（{paths.store_file(run_id)}）。先运行 `litradar fetch`。"
        )
    schema = payload.get("schema")
    if isinstance(schema, int) and schema > SCHEMA:
        # A newer store belongs to a newer engine: tell the user to upgrade
        # (exit code 2), do not try to guess the shape.
        raise ConfigError(
            f"{paths.store_file(run_id)} 的 schema={schema} 高于本引擎支持的 {SCHEMA}，"
            "请先升级引擎（pipx upgrade litradar）"
        )
    window = Window.from_dict(payload.get("window") or {})
    raw_items = [entry for entry in payload.get("items") or [] if isinstance(entry, dict)]
    items = [Article.from_dict(entry) for entry in raw_items if not entry.get("already_seen")]
    already = [Article.from_dict(entry) for entry in raw_items if entry.get("already_seen")]
    return LoadedStore(
        run_id=str(payload.get("run_id") or run_id),
        window=window,
        scans=[JournalScan.from_dict(entry) for entry in payload.get("journals") or []],
        items=items,
        already_seen=already,
        date_unverified=[Article.from_dict(entry) for entry in payload.get("date_unverified") or []],
        out_of_window=[Article.from_dict(entry) for entry in payload.get("out_of_window") or []],
        excluded_type=[Article.from_dict(entry) for entry in payload.get("excluded_type") or []],
        scan_at=str(payload.get("scan_at") or iso()),
    )


def _screen(
    ctx: Context,
    store: LoadedStore,
    *,
    use_llm: bool,
    refresh: bool,
    screen_path: Path | None = None,
) -> tuple[list[dict], str, str | None, dict[str, Any] | None]:
    """Return (raw_results, label, note, raw_payload) for one daily report."""
    from . import llm

    path = screen_path or ctx.paths.screen_file(store.run_id)
    model = str(ctx.config.get("llm.model", "deepseek-chat"))

    if path.exists() and not refresh:
        payload = read_json(path, default=None)
        if isinstance(payload, dict):
            results = extract_results(payload)
            label = str(payload.get("screening") or "llm-v1")
            if payload.get("model"):
                label = f"{label}（{payload['model']}）"
            return results, label, None, payload

    min_relevance = float(ctx.config.get("screening.min_relevance", 0.6) or 0.6)
    if not use_llm:
        keywords = list(ctx.config.get("screening.keywords", []))
        return (
            keyword_screen(store.items, keywords, min_relevance),
            "keyword（关键词兜底）",
            None,
            None,
        )

    journals = [scan.journal for scan in store.scans]
    raw_max = ctx.config.get("screening.max_items", 60)
    max_items = 60 if raw_max is None else max(0, int(raw_max))
    candidates = sorted(
        store.items, key=lambda article: (article.online_date or date.min), reverse=True
    )
    truncated = candidates[:max_items]
    note = None
    if len(candidates) > max_items:
        note = f"候选 {len(candidates)} 篇，按日期倒序截断到 {max_items} 篇送模型"
    ctx.emitter.emit("progress", stage="screen", done=0, total=len(truncated))
    messages = build_messages(
        ctx.config.direction,
        journals,
        truncated,
        max_abstract_chars=int(ctx.config.get("llm.max_abstract_chars", 1200) or 1200),
    )
    try:
        payload = llm.chat_json(ctx.config, messages, logger=ctx.logger)
    except llm.LlmError as exc:
        keywords = list(ctx.config.get("screening.keywords", []))
        return (
            keyword_screen(store.items, keywords, min_relevance),
            "keyword（关键词兜底）",
            f"{exc}；已按关键词降级，日报照常生成",
            None,
        )
    results = extract_results(payload)
    screen_payload = {
        "schema": SCHEMA,
        "run_id": store.run_id,
        "model": model,
        "screening": "llm-v1",
        "created_at": iso(),
        "results": results,
        "truncated": len(truncated) < len(candidates),
    }
    ctx.emitter.emit("progress", stage="screen", done=len(truncated), total=len(truncated))
    return results, f"llm-v1（{model}）", note, screen_payload


def render_report(
    ctx: Context,
    run_id: str,
    *,
    use_llm: bool = True,
    refresh: bool = False,
    screen_path: Path | None = None,
    commit: bool = True,
) -> RenderSummary:
    from . import __version__ as version
    from .wordcloud import write_wordcloud

    store = load_store(ctx.paths, run_id)

    journals_by_name: dict[str, dict] = {}
    try:
        from .config import load_journals

        journals_by_name = {journal["name"]: journal for journal in load_journals(ctx.config)}
    except Exception:  # noqa: BLE001 - metadata is optional for rendering
        journals_by_name = {}

    # A journal marked ``preprint: true`` may come from an adapter that does not
    # tag its articles (manual/custom); propagate the flag so every downstream
    # label, count and section split agrees.
    for article in (
        list(store.items) + list(store.already_seen) + list(store.date_unverified)
        + list(store.out_of_window) + list(store.excluded_type)
    ):
        meta = journals_by_name.get(article.journal) or {}
        if meta.get("preprint") and not (article.extra or {}).get("preprint"):
            article.extra["preprint"] = meta.get("name") or "preprint"

    screen_file = screen_path or ctx.paths.screen_file(store.run_id)
    screen_existed = screen_file.exists()
    existing_report = ctx.paths.report_file(store.window)
    if commit and not store.items and store.already_seen and existing_report.exists():
        # Re-running the same day must not replace a good report with an empty one.
        ctx.logger.warn(
            f"{run_id} 本轮没有新文章（{len(store.already_seen)} 篇已报告过），保留已有日报不覆盖"
        )
        return RenderSummary(
            run_id=store.run_id,
            report_path=existing_report,
            total=0,
            highlights=0,
            coverage=coverage_of(store.scans),
            screening="-",
        )

    results_raw, label, note, payload = _screen(
        ctx, store, use_llm=use_llm, refresh=refresh, screen_path=screen_path
    )

    allowed = default_hosts(store.items, [])
    validation = validate_results(results_raw, store.items, allowed)
    if note:
        validation.notes.insert(0, note)

    scores = load_scores(ctx.paths, store.run_id)
    history = load_scans(ctx.paths.scans_file) if commit else {}

    data = ReportInput(
        run_id=store.run_id,
        window=store.window,
        scans=store.scans,
        items=store.items,
        results=validation.results,
        already_seen=store.already_seen,
        out_of_window=store.out_of_window,
        date_unverified=store.date_unverified,
        excluded_type=store.excluded_type,
        validation=validation,
        screening_label=label,
        screening_note=note,
        scan_at=store.scan_at,
        generated_at=iso(),
        config=ctx.config,
        scores=scores,
        journals=journals_by_name,
        scan_history=history,
        engine_version=version,
        kind="daily",
    )

    if commit and bool(ctx.config.get("report.wordcloud", True)) and store.items:
        data.wordcloud_path = write_wordcloud(
            ctx.paths.report_file(store.window).parent,
            store.run_id,
            "daily",
            store.items,
        )

    highlights = select_highlights(data)
    image_stats = None
    if commit and images_enabled(ctx.config):
        scope = str(ctx.config.get("images.scope", "highlights") or "highlights")
        targets = data.items if scope == "all" else highlights
        image_stats = attach_images(
            ctx,
            ctx.paths.report_file(store.window).parent,
            targets,
            journals_by_name,
        )
    markdown, highlights = render_markdown(data, highlights=highlights)
    coverage = coverage_of(store.scans)

    summary = RenderSummary(
        run_id=store.run_id,
        total=len(store.items),
        highlights=len(highlights),
        coverage=coverage,
        screening=label,
        rejected=len(validation.rejected),
        screen_rejected_all=bool(store.items) and not validation.results,
        images=image_stats.attached if image_stats else 0,
    )
    report_path = write_report(ctx.paths, data, markdown)
    summary.report_path = report_path

    if commit:
        ctx.paths.run_dir(store.run_id).mkdir(parents=True, exist_ok=True)
        # Only persist when this run produced the file (or the user asked for a
        # refresh). An existing screen.json is an input we must not rewrite.
        if payload is not None and (refresh or not screen_existed):
            atomic_write_json(screen_file, payload)
        write_index(ctx.paths, data, coverage, len(highlights))
        write_betternotes(ctx.paths, highlights, validation.results, ctx.config)
        commit_seen(ctx.paths, store.items, store.run_id, store.scan_at)
        save_scans(ctx.paths.scans_file, history)

    ctx.emitter.emit(
        "report_written",
        path=str(report_path.relative_to(ctx.paths.vault))
        if report_path.is_relative_to(ctx.paths.vault)
        else str(report_path),
        items=len(store.items),
        highlights=len(highlights),
        coverage=coverage,
    )
    return summary


def commit_seen(paths: Paths, items: list[Article], run_id: str, scan_at: str) -> None:
    """Only ever called after a report has been written successfully."""
    seen = load_seen(paths.seen_file)
    now = iso()
    for article in items:
        existing = seen.get(article.id) or {}
        seen[article.id] = {
            "title": article.title,
            "journal": article.journal,
            "doi": article.doi,
            "url": article.url,
            "first_seen_at": existing.get("first_seen_at")
            or article.first_seen_at
            or scan_at
            or now,
            "last_reported_at": now,
            "last_run": run_id,
        }
    save_seen(paths.seen_file, seen)


# --------------------------------------------------------------------------- catch-up
def missing_days(paths: Paths, tz: str, max_days: int = 3, ref: date | None = None) -> list[date]:
    """Recent days with no store file — a laptop that was asleep at 09:00."""
    base = ref or datetime_now(tz).date()
    missing: list[date] = []
    for offset in range(max_days, 0, -1):
        day = base - timedelta(days=offset)
        if not paths.store_file(day.isoformat()).exists():
            missing.append(day)
    return missing


def datetime_now(tz: str):
    from .util import now

    return now(tz)
