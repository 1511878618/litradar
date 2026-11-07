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
from . import deepread as deepread_stage
from . import hotspots as hotspots_stage
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
    final_score,
    is_preprint,
    load_scores,
    render_markdown,
    select_highlights,
    subset_report,
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
    hotspots: int = 0
    usage: dict = field(default_factory=dict)
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
        cadence=journal.get("cadence") or "daily",
        weekday=journal.get("weekday"),
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


def _apply_cadence(
    journal: dict, scan: JournalScan, items: list[Article], window: Window
) -> None:
    """Turn "0 items" into an honest answer for journals that only publish weekly.

    Without this a weekly journal looks broken five days out of seven, and a real
    miss on its publishing day looks like an ordinary quiet day.  Both cases are
    named explicitly instead of being reported as "no new articles".
    """
    from .config import WEEKDAY_LABELS

    if journal.get("cadence") != "weekly" or scan.status != "ok":
        return
    weekday = journal.get("weekday")
    if not isinstance(weekday, int) or not 0 <= weekday <= 6:
        return
    label = WEEKDAY_LABELS[weekday]
    # Adapters may hand back rows outside the window (the manual one does); only
    # articles actually dated inside it count as "this issue exists".
    if any(window.contains(article.online_date) for article in items):
        return
    if window.covers_weekday(weekday):
        scan.reason = f"{label}更新，本轮 0 篇；用 `litradar journals test` 核对采集"
    else:
        scan.status = "off-cycle"
        scan.reason = f"{label}更新"
        scan.items_seen = 0


def _enrich_scan(journal: dict, scan: JournalScan, items: list[Article]) -> None:
    """Attach rhythm + issue metadata without making it a hard requirement.

    RSS can expose the current issue at channel level; Crossref exposes volume and
    issue per article.  Both are best-effort, so an absent value stays ``None``
    and the panel renders an em dash instead of inventing an issue number.
    """
    scan.cadence = journal.get("cadence") or "daily"
    scan.weekday = journal.get("weekday")

    def most_common(key: str) -> str | None:
        counts: dict[str, int] = {}
        for article in items:
            value = article.extra.get(key)
            if value in (None, ""):
                continue
            text = str(value).strip()
            if text:
                counts[text] = counts.get(text, 0) + 1
        if not counts:
            return None
        return max(counts, key=counts.get)

    scan.volume = scan.volume or most_common("volume")
    scan.issue = scan.issue or most_common("issue")
    scan.issue_identifier = scan.issue_identifier or most_common("issue_identifier")
    scan.cover_date = scan.cover_date or most_common("cover_date")


def fetch_all(
    ctx: Context,
    window: Window,
    journals: list[dict],
    *,
    budget_s: float = 600,
    save_state: bool = True,
    merge_existing: bool = False,
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
        _apply_cadence(journal, scan, items, window)
        _enrich_scan(journal, scan, items)
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

    previous = read_json(ctx.paths.store_file(window.run_id), default={}) if merge_existing else {}
    if previous:
        previous_items = [Article.from_dict(row) for row in previous.get("items", [])]
        fresh = {a.id: a for a in collected}
        for old in previous_items:
            if old.id in fresh:
                fresh[old.id].extra = {**old.extra, **fresh[old.id].extra}
                fresh[old.id].first_seen_at = old.first_seen_at
                if fresh[old.id].online_date is None and old.online_date:
                    fresh[old.id].online_date = old.online_date
                    fresh[old.id].date_source = old.date_source
                if not fresh[old.id].abstract_raw:
                    fresh[old.id].abstract_raw = old.abstract_raw
                    fresh[old.id].abstract_source = old.abstract_source
        collected = list({**{a.id: a for a in previous_items}, **fresh}.values())
        scans_by_name = {row["journal"]: JournalScan.from_dict(row) for row in previous.get("journals", [])}
        for scan in summary.scans:
            old = scans_by_name.get(scan.journal)
            if old and old.healthy and not scan.healthy:
                old.reason = f"保留上次可用结果；本次重试失败：{scan.reason}"
            else:
                scans_by_name[scan.journal] = scan
        summary.scans = list(scans_by_name.values())
    seen = load_seen(ctx.paths.seen_file) if save_state else {}
    # Re-rendering a batch must keep its original new items new within that batch.
    if previous:
        seen = {key: value for key, value in seen.items()
                if key not in {row["id"] for row in previous.get("items", []) if not row.get("already_seen")}}
    include_types = ctx.config.get("screening.include_types") or ["research-article", "review", "perspective", "letter"]
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
    if previous:
        current_ids = {row["id"] for key in ("items", "date_unverified", "out_of_window", "excluded_type") for row in store[key]}
        for key in ("date_unverified", "out_of_window", "excluded_type"):
            retained = [row for row in previous.get(key, []) if row["id"] not in current_ids]
            store[key] = retained + store[key]
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
    missing_days: list[str] = field(default_factory=list)


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
        missing_days=[str(day) for day in payload.get("missing_days") or []],
    )


def _screen(
    ctx: Context,
    store: LoadedStore,
    *,
    use_llm: bool,
    refresh: bool,
    screen_path: Path | None = None,
    usage: dict | None = None,
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
        payload = llm.chat_json(ctx.config, messages, logger=ctx.logger, usage=usage)
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


def assemble_weekly(ctx: Context, window: Window) -> Path:
    """Merge the week's daily stores into ``store/<week>.json`` — no network.

    A weekly report must contain everything the daily runs reported that week, so
    it is built from the dailies instead of re-fetching: no risk of re-reporting
    or missing a day the machine was asleep (days without a store are listed).
    """
    ctx.paths.ensure()
    merged: dict[str, Article] = {}
    scans: dict[str, JournalScan] = {}
    missing: list[str] = []
    scan_at = ""
    for offset in range(window.days):
        day = window.start + timedelta(days=offset)
        path = ctx.paths.store_file(day.isoformat())
        payload = read_json(path, default=None) if path.exists() else None
        if not isinstance(payload, dict):
            missing.append(day.isoformat())
            continue
        scan_at = max(scan_at, str(payload.get("scan_at") or ""))
        # ``already_seen`` only means "a daily run already printed it this week";
        # the weekly recap wants it too.
        for entry in payload.get("items") or []:
            if not isinstance(entry, dict):
                continue
            article = Article.from_dict(entry)
            merged.setdefault(article.id, article)
        for raw in payload.get("journals") or []:
            if isinstance(raw, dict):
                scan = JournalScan.from_dict(raw)
                scans[scan.journal] = scan

    if missing:
        ctx.logger.warn(
            f"{window.week_label} 有 {len(missing)} 天没有抓取记录：{'、'.join(missing)}"
        )
    store = {
        "schema": SCHEMA,
        "run_id": window.run_id,
        "engine": __version__,
        "scan_at": scan_at or iso(),
        "window": window.to_dict(),
        "journals": [scan.to_dict() for scan in scans.values()],
        "items": [article.to_dict() for article in merged.values()],
        "date_unverified": [],
        "out_of_window": [],
        "excluded_type": [],
        "missing_days": missing,
        "assembled_from": "daily",
    }
    path = ctx.paths.store_file(window.run_id)
    atomic_write_json(path, store)
    return path


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
    from . import llm
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

    usage = llm.new_usage()
    results_raw, label, note, payload = _screen(
        ctx, store, use_llm=use_llm, refresh=refresh, screen_path=screen_path, usage=usage
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
        kind="weekly" if store.window.kind in ("weekly", "week_to_date") else "daily",
        weekly_missing=list(store.missing_days),
        usage=usage,
    )

    from .library import apply_feedback, version_links
    apply_feedback(ctx.paths, data.items)
    if commit:
        version_links(ctx.paths, data.items)
    highlights = select_highlights(data)
    deepread_payload = None
    deepread_file = ctx.paths.run_dir(store.run_id) / "deepread.json"
    # Only annotate when this run actually talked to the model (or a previous
    # annotation exists): re-rendering from a cached screen.json must stay offline.
    deepread_ready = (not screen_existed or refresh) or deepread_file.exists()
    if use_llm and deepread_ready and deepread_stage.enabled(ctx.config):
        targets = deepread_stage.limit_articles(highlights, ctx.config)
        if targets:
            stored = read_json(deepread_file, default=None) if deepread_file.exists() and not refresh else None
            if isinstance(stored, dict):
                items = [
                    deepread_stage.DeepRead.from_dict(row)
                    for row in stored.get("papers") or []
                    if isinstance(row, dict)
                ]
                data.deepread = {item.id: item for item in items if item.id}
                data.deepread_label = (
                    f"{stored.get('screening') or 'deepread-v1'}"
                    f"（{stored.get('model') or deepread_stage.model_name(ctx.config)}）"
                )
            else:
                ctx.emitter.emit("progress", stage="deepread", done=0, total=len(targets))
                try:
                    annotations, deepread_data, rejected, notes, read_label = deepread_stage.annotate(
                        ctx.config, ctx.config.direction, targets, logger=ctx.logger, usage=usage
                    )
                except llm.LlmError as exc:
                    data.deepread_note = f"精读跳过：{exc}；已退回英文版式，日报照常生成"
                except Exception as exc:  # noqa: BLE001 - never let a nicety break the report
                    data.deepread_note = f"精读跳过：{type(exc).__name__}: {exc}；已退回英文版式"
                else:
                    data.deepread = annotations
                    data.deepread_label = read_label
                    deepread_payload = deepread_data
                    for item_id, reason in rejected:
                        validation.notes.append(f"精读被拒（{item_id}）：{reason}")
                    validation.notes.extend(f"精读：{note}" for note in notes)
                    ctx.emitter.emit("progress", stage="deepread", done=len(targets), total=len(targets))

    hotspots_payload = None
    hotspots_file = ctx.paths.run_dir(store.run_id) / "hotspots.json"
    if (
        data.kind == "weekly"
        and use_llm
        and hotspots_stage.enabled(ctx.config)
        and store.items
        and ((not screen_existed or refresh) or hotspots_file.exists())
    ):
        targets = sorted(
            store.items, key=lambda article: article.online_date or date.min, reverse=True
        )[: hotspots_stage.max_items(ctx.config)]
        stored = (
            read_json(hotspots_file, default=None)
            if hotspots_file.exists() and not refresh
            else None
        )
        if isinstance(stored, dict):
            data.hotspots = [
                hotspots_stage.Hotspot.from_dict(row)
                for row in stored.get("hotspots") or []
                if isinstance(row, dict)
            ]
            data.hotspots_label = (
                f"{stored.get('screening') or 'hotspots-v1'}"
                f"（{stored.get('model') or hotspots_stage.model_name(ctx.config)}）"
            )
        else:
            try:
                found, hotspots_data, rejected, notes, hot_label = hotspots_stage.annotate(
                    ctx.config, ctx.config.direction, targets, logger=ctx.logger, usage=usage
                )
            except llm.LlmError as exc:
                data.hotspots_note = f"热点归纳跳过：{exc}"
            except Exception as exc:  # noqa: BLE001
                data.hotspots_note = f"热点归纳跳过：{type(exc).__name__}: {exc}"
            else:
                data.hotspots = found
                data.hotspots_label = hot_label
                hotspots_payload = hotspots_data
                for title, reason in rejected:
                    validation.notes.append(f"热点被拒（{title}）：{reason}")
                validation.notes.extend(f"热点：{note}" for note in notes)
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
    usage_file = ctx.paths.run_dir(store.run_id) / "usage.json"
    if not usage.get("calls") and usage_file.exists():
        # Re-rendering from cache still shows what the original run cost.
        stored_usage = read_json(usage_file, default=None)
        if isinstance(stored_usage, dict) and stored_usage.get("calls"):
            data.usage = stored_usage
    from .translation import translate_titles

    data.title_translations, data.translation_note = translate_titles(
        ctx.config, ctx.paths, data.items,
        existing={key: deep.title_zh for key, deep in data.deepread.items() if deep.title_zh},
        commit=commit, logger=ctx.logger,
    )
    if commit:
        atomic_write_json(ctx.paths.run_dir(store.run_id) / "presentation.json", {
            "titles": data.title_translations,
            "extras": {a.id: {**a.extra, "highlight": a.id in {h.id for h in highlights},
                                "rank_score": final_score(a, data.results.get(a.id), data.scores, data.config)} for a in data.items},
            "summaries": {key: result.summary_zh for key, result in data.results.items() if result.summary_zh},
            "why": {key: result.why for key, result in data.results.items() if result.why},
        })
    split = bool(ctx.config.get("output.split_preprints", False)) and any(
        is_preprint(article) for article in store.items
    )
    main_hl = pre_hl = []
    if split:
        main_data = subset_report(data, want_preprint=False, variant="main")
        pre_data = subset_report(data, want_preprint=True, variant="preprint")
        if commit and bool(ctx.config.get("report.wordcloud", True)):
            for part in (main_data, pre_data):
                part.wordcloud_path = write_wordcloud(
                    ctx.paths.report_file(store.window, part.variant).parent,
                    store.run_id, f"{data.kind}-{part.variant}", part.items,
                )
        main_data.sibling_link = ctx.paths.report_file(store.window, "preprint").stem
        pre_data.sibling_link = ctx.paths.report_file(store.window, "main").stem
        main_hl = [article for article in highlights if not is_preprint(article)]
        pre_hl = [article for article in highlights if is_preprint(article)]
        main_md, main_hl = render_markdown(main_data, highlights=main_hl)
        pre_md, pre_hl = render_markdown(pre_data, highlights=pre_hl)
        data = main_data
        markdown = main_md
        highlights = main_hl + pre_hl
    else:
        if commit and bool(ctx.config.get("report.wordcloud", True)) and data.items:
            data.wordcloud_path = write_wordcloud(
                ctx.paths.report_file(store.window).parent, store.run_id, data.kind, data.items,
            )
        markdown, highlights = render_markdown(data, highlights=highlights)
    coverage = coverage_of(store.scans)
    if usage.get("calls"):
        ctx.logger.info(
            f"模型用量：{usage['calls']} 次调用，输入 {usage['prompt_tokens']} tokens，"
            f"输出 {usage['completion_tokens']} tokens，"
            f"约 ¥{llm.cost_cny(usage, ctx.config):.4f}"
            + ("（含估算）" if usage.get("estimated") else "")
        )

    summary = RenderSummary(
        run_id=store.run_id,
        total=len(store.items),
        highlights=len(highlights),
        coverage=coverage,
        screening=label,
        rejected=len(validation.rejected),
        screen_rejected_all=bool(store.items) and not validation.results,
        images=image_stats.attached if image_stats else 0,
        hotspots=len(data.hotspots),
        usage=usage,
    )
    report_path = write_report(ctx.paths, data, markdown)
    if split:
        write_report(ctx.paths, pre_data, pre_md)
    summary.report_path = report_path

    if commit:
        ctx.paths.run_dir(store.run_id).mkdir(parents=True, exist_ok=True)
        # Only persist when this run produced the file (or the user asked for a
        # refresh). An existing screen.json is an input we must not rewrite.
        if payload is not None and (refresh or not screen_existed):
            atomic_write_json(screen_file, payload)
        if deepread_payload is not None and (refresh or not deepread_file.exists()):
            atomic_write_json(deepread_file, deepread_payload)
        if hotspots_payload is not None and (refresh or not hotspots_file.exists()):
            atomic_write_json(hotspots_file, hotspots_payload)
        if usage.get("calls"):
            usage_payload = {
                **usage,
                "model": str(ctx.config.get("llm.model", "")),
                "cost_cny": float(llm.cost_cny(usage, ctx.config)),
                "recorded_at": iso(),
            }
            atomic_write_json(usage_file, usage_payload)
        if split:
            write_index(ctx.paths, main_data, coverage_of(main_data.scans), len(main_hl))
            write_index(ctx.paths, pre_data, coverage_of(pre_data.scans), len(pre_hl))
        else:
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
