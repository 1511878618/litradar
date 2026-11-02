"""Command line interface.

Exit codes are a contract with front-ends (see ``docs/design/02-engine.md``):

===== ============================================= ==============================
code  meaning                                        UI behaviour
===== ============================================= ==============================
0     success                                        refresh
1     unexpected exception                            red, show last stderr lines
2     configuration error                              yellow, open config
3     ``screen.json`` failed strict validation         yellow, show validation log
4     every journal failed                             red, never say "no new articles"
5     ``doctor`` found a problem / optional extra off  yellow, show the list
6     another run holds the lock                       grey, try later
7     timed out / cancelled                            grey, retryable
===== ============================================= ==============================
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import SCHEMA, __version__
from .config import Config, ConfigError, find_journal, load_config, load_journals, write_skeleton
from .doctor import has_errors, run_doctor, summarize
from .events import Emitter
from .fetcher import Fetcher
from .logs import Logger, rotate_logs
from .paths import Paths
from .pipeline import (
    Context,
    fetch_all,
    load_store,
    missing_days,
    render_report,
    resolve_journals,
)
from .schedule import install as schedule_install
from .schedule import print_commands
from .schedule import uninstall as schedule_uninstall
from .screening import extract_results
from .state import LockBusy, VaultLock, atomic_write_json, load_scans, read_json
from .util import EngineError, iso, parse_date, today
from .validate import default_hosts, validate_results
from .window import Window, build_window

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_SCREEN = 3
EXIT_ALL_FAILED = 4
EXIT_DOCTOR = 5
EXIT_LOCKED = 6
EXIT_CANCELLED = 7

WINDOW_KINDS = ("today", "yesterday", "last_week", "last_n_days", "explicit", "weekly")


# --------------------------------------------------------------------------- helpers
def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", default=argparse.SUPPRESS, help="Obsidian vault 根目录（默认当前目录）")
    parser.add_argument("--config", dest="config_path", default=argparse.SUPPRESS,
                        help="配置文件路径（默认 <vault>/.litradar/config.yaml）")
    parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS,
                        help="stdout 每行一个 JSON 事件（给前端用）")
    parser.add_argument("--json", dest="as_json", action="store_true", default=argparse.SUPPRESS,
                        help="以 JSON 输出结果")
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS, help="只输出错误")
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help="调试日志")


def add_window_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="报告哪一天（YYYY-MM-DD），等价于单日窗口")
    parser.add_argument("--window", choices=WINDOW_KINDS, help="时间窗类型（默认取 config.schedule.window）")
    parser.add_argument("--days", type=int, default=7, help="last_n_days / last_week 的天数（默认 7）")
    parser.add_argument("--start", help="explicit 窗口开始日")
    parser.add_argument("--end", help="explicit 窗口结束日")
    parser.add_argument("--ref", help="把“今天”固定为某天，仅用于补跑与测试")
    parser.add_argument("--kind", choices=["daily", "weekly"], help="报告类型：daily（默认）或 weekly")


def _arg(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def resolve_vault(args: argparse.Namespace) -> Path:
    raw = _arg(args, "vault") or __import__("os").environ.get("LITRADAR_VAULT") or Path.cwd()
    return Path(raw).expanduser().resolve()


def resolve_window(args: argparse.Namespace, config: Config) -> Window:
    timezone = config.tz
    date_arg = _arg(args, "date")
    start = _arg(args, "start")
    end = _arg(args, "end")
    kind = _arg(args, "window")
    report_kind = _arg(args, "kind")
    if report_kind == "weekly" and date_arg:
        raise ConfigError("--kind weekly 不能与 --date 同时使用")
    if report_kind == "weekly" and kind and kind != "weekly":
        raise ConfigError("--kind weekly 与 --window 冲突；用 --window weekly 即可")
    if date_arg and (kind or start or end):
        raise ConfigError("--date 与 --window/--start/--end 互斥，只能给一个")
    if report_kind == "weekly" and not (start or end):
        return build_window("weekly", tz=timezone, ref=_arg(args, "ref"))
    if start or end:
        return build_window("explicit", tz=timezone, start=start, end=end, ref=_arg(args, "ref"))
    if date_arg:
        return build_window(date_str=date_arg, tz=timezone)
    kind = kind or str(config.get("schedule.window", "yesterday") or "yesterday")
    return build_window(kind, tz=timezone, ref=_arg(args, "ref"), days=_arg(args, "days", 7) or 7)


def make_context(args: argparse.Namespace, *, require_config: bool = True) -> Context:
    vault = resolve_vault(args)
    config = load_config(vault, Path(_arg(args, "config_path")) if _arg(args, "config_path") else None,
                         require=require_config)
    paths = Paths(vault, config)
    level = "debug" if _arg(args, "verbose") else ("error" if _arg(args, "quiet") else "info")
    logger = Logger(level=level, file_path=paths.logs_dir / f"cli-{today(config.tz)}.log")
    inline_key = str(config.get("llm.api_key", "") or "")
    if inline_key and "$" not in inline_key:
        logger.add_secret(inline_key)
    emitter = Emitter(bool(_arg(args, "jsonl")))
    fetcher = Fetcher(
        config.section("http"),
        cache_dir=paths.cache_dir,
        no_cache=bool(_arg(args, "no_cache")),
        timeout_s=_arg(args, "timeout"),
        logger=logger,
    )
    return Context(vault=vault, config=config, paths=paths, logger=logger, emitter=emitter,
                   fetcher=fetcher, tz=config.tz)


class RunScope:
    """Emits ``run_start`` / ``run_end`` and guarantees ``run_end.exit`` matches."""

    def __init__(self, args: argparse.Namespace, run_id: str, command: str) -> None:
        self.emitter = Emitter(bool(_arg(args, "jsonl")))
        self.run_id = run_id
        self.command = command
        self.code = EXIT_OK

    def __enter__(self) -> RunScope:
        self.emitter.run_start(self.run_id, self.command)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and self.code == EXIT_OK:
            # An exception leaves the process with a non-zero code; the event
            # stream must not claim success in that case.
            self.code = EXIT_FAIL
        self.emitter.run_end(exc_type is None and self.code == EXIT_OK, self.code)


def _journal_list(args: argparse.Namespace, config: Config) -> list[dict]:
    names = _arg(args, "journal") or []
    return resolve_journals(config, names)


def _fmt_local(value: Any, tz: str) -> str:
    """ISO timestamp -> 'YYYY-MM-DD HH:MM' in the vault timezone (or '—')."""
    if not value:
        return "—"
    try:
        from .util import tzinfo

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tzinfo("UTC"))
        return parsed.astimezone(tzinfo(tz)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(value)[:19]


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- commands
def cmd_init(args: argparse.Namespace) -> int:
    vault = resolve_vault(args)
    paths = write_skeleton(vault, _arg(args, "direction") or "")
    config = load_config(vault)
    journals = load_journals(config)
    if _arg(args, "as_json"):
        _print_json({"vault": str(vault), "config": str(paths.config_file),
                     "journals": [journal["name"] for journal in journals]})
        return EXIT_OK
    print(f"已初始化 {vault}")
    print(f"  配置：{paths.config_file}")
    print(f"  期刊：{len(journals)} 本 — " + "、".join(journal["name"] for journal in journals))
    print("\n下一步：")
    print(f'  1. 编辑 {paths.config_file} 的 direction（研究方向）')
    print(f'  2. LITRADAR_API_KEY=... litradar run --vault "{vault}" --llm')
    print(f'  3. litradar schedule install --vault "{vault}"')
    return EXIT_OK


def cmd_fetch(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        window = resolve_window(args, ctx.config)
        journals = _journal_list(args, ctx.config)
        rotate_logs(ctx.paths.logs_dir)
        with VaultLock(ctx.paths.lock_file):
            with RunScope(args, window.run_id, "fetch") as scope:
                ctx.emitter = scope.emitter
                ctx.logger.add_file(ctx.paths.run_log(window.run_id))
                summary = fetch_all(ctx, window, journals, budget_s=float(_arg(args, "budget", 600)))
                ctx.logger.info(
                    f"抓取完成：{len(summary.new_items)} 条新文章，"
                    f"{len(summary.already_seen)} 条已报告过，覆盖 {summary.coverage}"
                )
                scope.code = EXIT_ALL_FAILED if summary.all_failed else EXIT_OK
                if _arg(args, "as_json"):
                    _print_json({
                        "run_id": summary.run_id,
                        "window": summary.window.to_dict(),
                        "new": len(summary.new_items),
                        "already_seen": len(summary.already_seen),
                        "coverage": summary.coverage,
                        "journals": [scan.to_dict() for scan in summary.scans],
                        "store": str(summary.store_path) if summary.store_path else None,
                    })
                return scope.code
    finally:
        ctx.close()


def cmd_render(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        window = resolve_window(args, ctx.config)
        run_id = _arg(args, "run_id") or window.run_id
        use_llm = True if _arg(args, "llm") else (False if _arg(args, "no_llm") else
                                                  str(ctx.config.get("screening.mode", "llm")) == "llm")
        screen_file = Path(_arg(args, "screen")).expanduser() if _arg(args, "screen") else None
        rotate_logs(ctx.paths.logs_dir)
        if screen_file is not None and not screen_file.exists():
            raise ConfigError(f"--screen 指定的文件不存在：{screen_file}")
        with VaultLock(ctx.paths.lock_file):
            with RunScope(args, run_id, "render") as scope:
                ctx.emitter = scope.emitter
                ctx.logger.add_file(ctx.paths.run_log(run_id))
                summary = render_report(
                    ctx, run_id, use_llm=use_llm, refresh=bool(_arg(args, "refresh")),
                    screen_path=screen_file,
                )
                ctx.logger.info(
                    f"日报已生成：{summary.report_path}（{summary.total} 篇，置顶 {summary.highlights}，"
                    f"覆盖 {summary.coverage}，校验拒收 {summary.rejected}，配图 {summary.images}）"
                )
                if summary.coverage == "failed":
                    scope.code = EXIT_ALL_FAILED
                elif summary.screen_rejected_all:
                    scope.code = EXIT_SCREEN
                else:
                    scope.code = EXIT_OK
                if _arg(args, "as_json"):
                    _print_json({
                        "run_id": summary.run_id,
                        "report": str(summary.report_path) if summary.report_path else None,
                        "items": summary.total,
                        "highlights": summary.highlights,
                        "coverage": summary.coverage,
                        "screening": summary.screening,
                        "rejected": summary.rejected,
                        "images": summary.images,
                    })
                return scope.code
    finally:
        ctx.close()


def cmd_run(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        window = resolve_window(args, ctx.config)
        journals = _journal_list(args, ctx.config)
        rotate_logs(ctx.paths.logs_dir)
        code = EXIT_OK

        def use_llm_default() -> bool:
            if _arg(args, "no_llm"):
                return False
            if _arg(args, "llm"):
                return True
            return str(ctx.config.get("screening.mode", "llm")) == "llm"

        with VaultLock(ctx.paths.lock_file):
            if _arg(args, "catch_up"):
                ref = parse_date(_arg(args, "ref")) if _arg(args, "ref") else None
                days = missing_days(ctx.paths, ctx.tz, int(_arg(args, "max_days", 3) or 3), ref=ref)
                for day in days:
                    day_window = Window(kind="day", start=day, end=day, tz=ctx.tz)
                    ctx.logger.info(f"补跑 {day}")
                    try:
                        with RunScope(args, day_window.run_id, "run") as scope:
                            ctx.emitter = scope.emitter
                            fetch_all(ctx, day_window, journals,
                                      budget_s=float(_arg(args, "budget", 600)))
                            render_report(ctx, day_window.run_id, use_llm=use_llm_default())
                    except EngineError as exc:
                        ctx.logger.warn(f"补跑 {day} 失败：{exc}")

            with RunScope(args, window.run_id, "run") as scope:
                ctx.emitter = scope.emitter
                ctx.logger.add_file(ctx.paths.run_log(window.run_id))
                summary = fetch_all(ctx, window, journals,
                                    budget_s=float(_arg(args, "budget", 600)))
                report = render_report(ctx, window.run_id, use_llm=use_llm_default(),
                                       refresh=bool(_arg(args, "refresh")))
                ctx.logger.info(
                    f"完成：{len(summary.new_items)} 篇新文章，置顶 {report.highlights}，"
                    f"配图 {report.images}；覆盖 {report.coverage} → {report.report_path}"
                )
                if summary.all_failed or report.coverage == "failed":
                    scope.code = EXIT_ALL_FAILED
                elif report.screen_rejected_all:
                    scope.code = EXIT_SCREEN
                code = scope.code
                if _arg(args, "as_json"):
                    _print_json({
                        "run_id": report.run_id,
                        "new": len(summary.new_items),
                        "items": report.total,
                        "highlights": report.highlights,
                        "coverage": report.coverage,
                        "screening": report.screening,
                        "images": report.images,
                        "report": str(report.report_path) if report.report_path else None,
                    })
        return code
    finally:
        ctx.close()


def cmd_embed(args: argparse.Namespace) -> int:
    from .embed import EmbedUnavailable, compute_scores, scores_payload, top_k

    ctx = make_context(args)
    try:
        window = resolve_window(args, ctx.config)
        run_id = _arg(args, "run_id") or window.run_id
        store = load_store(ctx.paths, run_id)
        with VaultLock(ctx.paths.lock_file):
            with RunScope(args, run_id, "embed") as scope:
                ctx.emitter = scope.emitter
                try:
                    scores, profile_size = compute_scores(
                        ctx.config, ctx.paths, store.items,
                        profile=_arg(args, "profile"), logger=ctx.logger,
                    )
                except EmbedUnavailable as exc:
                    ctx.logger.warn(str(exc))
                    scope.code = EXIT_DOCTOR
                    if _arg(args, "as_json"):
                        _print_json({"ok": False, "reason": str(exc)})
                    return EXIT_DOCTOR
                atomic_write_json(
                    ctx.paths.scores_file(run_id),
                    scores_payload(ctx.config, scores, profile_size),
                )
                ctx.logger.info(
                    f"相关度已写入 {ctx.paths.scores_file(run_id)}（{len(scores)} 条）"
                )
                if _arg(args, "as_json"):
                    _print_json({"run_id": run_id, "scores": scores,
                                 "top": top_k(scores, int(_arg(args, "top", 10)))})
                return scope.code
    finally:
        ctx.close()


def cmd_screen(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        window = resolve_window(args, ctx.config)
        run_id = _arg(args, "run_id") or window.run_id
        path = Path(_arg(args, "file")).expanduser()
        payload = read_json(path, default=None)
        if payload is None:
            raise ConfigError(f"读不到 {path}")
        store = load_store(ctx.paths, run_id)
        results = extract_results(payload)
        validation = validate_results(results, store.items, default_hosts(store.items, []))
        if not results:
            validation.notes.append("文件里没有可用的 results")
        summary = {
            "run_id": run_id,
            "results": len(validation.results),
            "rejected": len(validation.rejected),
            "notes": len(validation.notes),
            "missing": len(validation.missing),
        }
        if _arg(args, "apply"):
            write_json = {
                "schema": SCHEMA,
                "run_id": run_id,
                "model": (payload.get("model") if isinstance(payload, dict) else None) or "external",
                "screening": "external",
                "created_at": iso(),
                "results": [
                    {**result.to_dict(), "validated": True}
                    for result in validation.results.values()
                ],
            }
            atomic_write_json(ctx.paths.screen_file(run_id), write_json)
            ctx.logger.info(f"已写入 {ctx.paths.screen_file(run_id)}")
        if _arg(args, "as_json"):
            _print_json({"summary": summary, "validation": validation.to_dict()})
        else:
            print(f"候选 {len(store.items)} 条；通过 {summary['results']}；"
                  f"拒收 {summary['rejected']}；"
                  f"缺失 {summary['missing']}；备注 {summary['notes']} 条")
            for article_id, reason in validation.rejected:
                print(f"  拒收 {article_id}: {reason}")
            for note in validation.notes[:20]:
                print(f"  · {note}")
        if validation.rejected and _arg(args, "strict"):
            return EXIT_SCREEN
        return EXIT_OK
    finally:
        ctx.close()


def cmd_status(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        scans = load_scans(ctx.paths.scans_file)
        stores = sorted(ctx.paths.store_dir.glob("*.json")) if ctx.paths.store_dir.exists() else []
        latest = stores[-1].stem if stores else None
        coverage = None
        if latest:
            try:
                coverage = str((read_json(ctx.paths.store_file(latest), default={}) or {}).get("coverage") or "")
            except EngineError:
                coverage = None
            if not coverage:
                from .model import JournalScan
                from .render import coverage_of

                payload = read_json(ctx.paths.store_file(latest), default={}) or {}
                coverage = coverage_of(
                    [JournalScan.from_dict(entry) for entry in payload.get("journals") or []]
                )
        last_scan_at = None
        for store_path in stores:
            try:
                candidate = str((read_json(store_path, default={}) or {}).get("scan_at") or "")
            except EngineError:
                continue
            if candidate and (last_scan_at is None or candidate > last_scan_at):
                last_scan_at = candidate
        payload = {
            "engine": __version__,
            "schema": SCHEMA,
            "vault": str(ctx.paths.vault),
            "last_run": latest,
            "last_scan_at": last_scan_at,
            "store_files": len(stores),
            "coverage": coverage,
            "journals": scans,
        }
        if _arg(args, "as_json"):
            _print_json(payload)
            return EXIT_OK
        print(f"引擎 {__version__}（schema {SCHEMA}） · vault {ctx.paths.vault}")
        printed_scan = _fmt_local(last_scan_at, ctx.tz) if last_scan_at else "从未"
        print(
            f"最近一次抓取：{printed_scan} · 最新日报日：{latest or '—'}"
            f" · 覆盖：{coverage or '—'} · store {len(stores)} 份"
        )
        if not scans:
            print("没有任何期刊扫描记录，先运行 litradar fetch")
            return EXIT_OK
        print(f"{'期刊':<34}{'状态':<14}{'条目':>4}  {'上次成功':<20}说明")
        for name, entry in sorted(scans.items()):
            status = str(entry.get("status") or "?")
            mark = {"ok": "✅", "metadata-only": "⚠", "blocked": "⛔", "timeout": "⏱", "error": "❌"}.get(status, "?")
            print(
                f"{name[:33]:<34}{mark + ' ' + status:<14}{entry.get('items_seen', 0):>4}  "
                f"{_fmt_local(entry.get('last_ok_at'), ctx.tz):<20}{entry.get('reason') or ''}"
            )
        return EXIT_OK
    finally:
        ctx.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    ctx = make_context(args, require_config=False)
    try:
        checks = run_doctor(ctx, network=not _arg(args, "offline"))
        if _arg(args, "as_json"):
            _print_json({"ok": not has_errors(checks), "summary": summarize(checks),
                         "checks": [check.to_dict() for check in checks]})
        else:
            width = max((len(check.name) for check in checks), default=20) + 2
            for check in checks:
                if check.ok:
                    mark = "✅"
                elif check.severity == "warn":
                    mark = "⚠️"
                else:
                    mark = "❌"
                print(f"{mark} {check.name:<{width}}{check.detail}")
            print(f"\n{summarize(checks)}")
        return EXIT_DOCTOR if has_errors(checks) else EXIT_OK
    finally:
        ctx.close()


def cmd_journals(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        command = _arg(args, "journals_command", "list")
        if command == "list":
            journals = load_journals(ctx.config)
            rows = [
                {"name": journal["name"], "adapter": journal["adapter"],
                 "surface": journal.get("surface") or "", "slug": journal["slug"]}
                for journal in journals
            ]
            if _arg(args, "as_json"):
                _print_json(rows)
                return EXIT_OK
            print(f"{'期刊':<40}{'适配器':<12}栏目")
            for row in rows:
                print(f"{row['name'][:39]:<40}{row['adapter']:<12}{row['surface']}")
            return EXIT_OK
        if command == "add":
            return _journals_add(args, ctx)
        if command == "test":
            return _journals_test(args, ctx)
        raise ConfigError(f"未知子命令 {command}")
    finally:
        ctx.close()


def _journals_add(args: argparse.Namespace, ctx: Context) -> int:
    import yaml

    from .config import validate_journal

    name = str(_arg(args, "name") or "").strip()
    if not name:
        raise ConfigError("journals add 需要刊名")
    adapter = str(_arg(args, "adapter") or "rss").lower()
    entry: dict[str, Any] = {"name": name, "adapter": adapter}
    if adapter == "rss":
        entry["feed"] = _arg(args, "feed") or _arg(args, "url") or ""
    elif adapter == "html":
        entry["url"] = _arg(args, "url") or ""
        item = _arg(args, "item_selector") or ""
        if item:
            entry["selectors"] = {"item": item}
    elif adapter == "crossref":
        entry["issn"] = _arg(args, "issn") or ""
    if _arg(args, "surface"):
        entry["surface"] = _arg(args, "surface")
    if _arg(args, "issn") and adapter != "crossref":
        entry["issn"] = _arg(args, "issn")
    entry = validate_journal(entry, "journals add")
    path = ctx.paths.journals_dir / f"{entry['slug']}.yaml"
    if path.exists():
        raise ConfigError(f"{path} 已存在")
    path.write_text(yaml.safe_dump(entry, allow_unicode=True, sort_keys=False), encoding="utf-8")
    ctx.logger.info(f"已写入 {path}")
    print(f"已写入 {path}\n下一步：litradar journals test \"{name}\"")
    return EXIT_OK


def _journals_test(args: argparse.Namespace, ctx: Context) -> int:
    from .adapters.base import get_adapter
    from .logs import Stopwatch

    name = str(_arg(args, "name") or "").strip()
    journals = load_journals(ctx.config)
    journal = find_journal(journals, name)
    window = build_window("last_n_days", tz=ctx.tz, days=int(_arg(args, "days", 1) or 1))
    adapter = get_adapter(journal, ctx.paths.vault)
    stopwatch = Stopwatch()
    items, scan = adapter.fetch(journal, ctx.fetcher, window)
    if _arg(args, "as_json"):
        _print_json({"scan": scan.to_dict(), "ms": stopwatch.ms,
                     "sample": [article.to_dict() for article in items[:3]]})
        return EXIT_OK if scan.healthy else EXIT_ALL_FAILED
    print(f"{journal['name']} · {journal['adapter']} · {scan.status} · {scan.items_seen} 项 "
          f"({stopwatch.ms}ms)")
    if scan.reason:
        print(f"  原因：{scan.reason}")
    if not items:
        print("  ⚠️ 没有解析出任何条目；检查 feed/url 与选择器")
        return EXIT_ALL_FAILED
    for article in items[:3]:
        print(f"  · {article.title[:80]}")
        print(f"    date={article.online_date or '—'} doi={article.doi or '—'} url={article.url[:80]}")
    dates = sum(1 for article in items if article.online_date)
    dois = sum(1 for article in items if article.doi)
    print(f"  共 {len(items)} 条，日期 {dates} 条，DOI {dois} 条")
    if dates == 0 or dois == 0:
        print("  ⚠️ 日期或 DOI 全空，通常意味着字段映射错了")
    return EXIT_OK if scan.healthy else EXIT_ALL_FAILED


def cmd_schedule(args: argparse.Namespace) -> int:
    ctx = make_context(args)
    try:
        command = _arg(args, "schedule_command", "print")
        if _arg(args, "time"):
            _override_time(ctx, str(_arg(args, "time")))
        if command == "print":
            for line in print_commands(ctx.paths.vault, ctx.config):
                print(line)
            return EXIT_OK
        if command == "install":
            lines = schedule_install(ctx.paths.vault, ctx.config, apply=bool(_arg(args, "apply")))
            for line in lines:
                print(line)
            if not _arg(args, "apply"):
                print("\n未注册（只打印命令）。加 --apply 直接注册到系统调度器。")
            return EXIT_OK
        if command == "uninstall":
            lines = schedule_uninstall(ctx.paths.vault, ctx.config, apply=bool(_arg(args, "apply")))
            for line in lines:
                print(line)
            return EXIT_OK
        raise ConfigError(f"未知子命令 {command}")
    finally:
        ctx.close()


def _override_time(ctx: Context, value: str) -> None:
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
    except ValueError as exc:
        raise ConfigError(f"--time 格式应为 HH:MM，收到 {value!r}") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ConfigError(f"--time 越界：{value!r}")
    ctx.config.raw.setdefault("schedule", {})["time"] = f"{hour:02d}:{minute:02d}"


def cmd_deploy(args: argparse.Namespace) -> int:
    from .config import write_skeleton as build_skeleton

    ctx = make_context(args, require_config=False)
    try:
        paths = build_skeleton(ctx.paths.vault, ctx.config.direction)
        skill_dir = Path(_arg(args, "skill_dir")).expanduser() if _arg(args, "skill_dir") else paths.root / "skills"
        skill_dir.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).parent / "templates" / "literature-monitor.md"
        target = skill_dir / "literature-monitor.md"
        if template.exists() and not target.exists():
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"vault 骨架：{paths.root}")
        print(f"给 agent 的说明书：{target}")
        print("提示：pipx 安装的引擎不需要 deploy；本命令只补配置与说明书。")
        return EXIT_OK
    finally:
        ctx.close()


def cmd_upgrade(args: argparse.Namespace) -> int:
    print(f"当前引擎 {__version__}（schema {SCHEMA}）")
    print("升级：pipx upgrade litradar（或 pip install -U litradar）")
    print("升级后运行 litradar doctor 确认配置兼容。")
    return EXIT_OK


def cmd_zotero(args: argparse.Namespace) -> int:
    from .zotero import ZoteroUnavailable, list_collections, push_items

    ctx = make_context(args)
    try:
        if _arg(args, "api"):
            ctx.config.raw.setdefault("zotero", {})["api"] = str(_arg(args, "api"))
        command = _arg(args, "zotero_command", "collections")
        if command == "collections":
            try:
                collections = list_collections(ctx.config)
            except ZoteroUnavailable as exc:
                ctx.logger.warn(str(exc))
                return EXIT_DOCTOR
            rows = []
            for collection in collections:
                data = collection.get("data") if isinstance(collection.get("data"), dict) else collection
                rows.append({"key": data.get("key"), "name": data.get("name")})
            if _arg(args, "as_json"):
                _print_json(rows)
            else:
                for row in rows:
                    print(f"{row['key']}  {row['name']}")
            return EXIT_OK

        if command == "push":
            window = resolve_window(args, ctx.config)
            run_id = _arg(args, "run_id") or window.run_id
            store = load_store(ctx.paths, run_id)
            articles = store.items
            if _arg(args, "only_relevant"):
                screen = read_json(ctx.paths.screen_file(run_id), default=None)
                if isinstance(screen, dict):
                    keep = {
                        str(entry.get("id"))
                        for entry in screen.get("results") or []
                        if isinstance(entry, dict) and entry.get("relevant")
                    }
                    articles = [article for article in articles if article.id in keep]
            try:
                result = push_items(
                    ctx.config, articles,
                    session_id=f"litradar-{run_id}",
                    only_new=not _arg(args, "force"),
                )
            except ZoteroUnavailable as exc:
                ctx.logger.warn(str(exc))
                return EXIT_DOCTOR
            if _arg(args, "as_json"):
                _print_json(result)
            else:
                print(f"入库 {result['pushed']} 篇，跳过 {result['skipped']} 篇，失败 {result['failed']} 篇")
                for error in result["errors"]:
                    print(f"  ! {error}")
            return EXIT_OK if result["failed"] == 0 else EXIT_FAIL
        raise ConfigError(f"未知子命令 {command}")
    finally:
        ctx.close()


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litradar",
        description="期刊文献雷达：抓取官网 → 日期窗 → 去重 → 校验 → Markdown 日报",
        parents=[],  # common args are added below so --vault works before the subcommand
    )
    add_common(parser)
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.set_defaults(handler=None)

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = subparsers.add_parser("init", parents=[], help="在 vault 里建 .litradar 骨架")
    add_common(init)
    init.add_argument("--direction", help="研究方向（写人话，模型用它判断相关性）")
    init.set_defaults(handler=cmd_init)

    fetch = subparsers.add_parser("fetch", parents=[], help="抓取 + 日期窗 + 去重 → store/<date>.json")
    add_common(fetch)
    add_window_args(fetch)
    fetch.add_argument("--journal", action="append", help="只跑指定刊（可重复）")
    fetch.add_argument("--no-cache", action="store_true", help="忽略 HTTP 缓存")
    fetch.add_argument("--timeout", type=float, help="单请求超时秒数")
    fetch.add_argument("--budget", type=float, default=600, help="全轮 fetch 预算秒数")
    fetch.set_defaults(handler=cmd_fetch)

    render = subparsers.add_parser("render", parents=[], help="读 store → [LLM] → 校验 → 日报 + 索引")
    add_common(render)
    add_window_args(render)
    render.add_argument("--run-id", help="直接指定 run_id（高级用法）")
    render.add_argument("--llm", action="store_true", help="强制调用模型")
    render.add_argument("--no-llm", action="store_true", help="跳过模型，走关键词兜底")
    render.add_argument("--refresh", action="store_true", help="忽略已有 screen.json，重新调用模型")
    render.add_argument("--screen", help="用外部 screen.json 渲染")
    render.set_defaults(handler=cmd_render)

    run = subparsers.add_parser("run", parents=[], help="fetch + render")
    add_common(run)
    add_window_args(run)
    run.add_argument("--journal", action="append", help="只跑指定刊（可重复）")
    run.add_argument("--no-cache", action="store_true", help="忽略 HTTP 缓存")
    run.add_argument("--timeout", type=float, help="单请求超时秒数")
    run.add_argument("--budget", type=float, default=600, help="全轮 fetch 预算秒数")
    run.add_argument("--llm", action="store_true", help="强制调用模型（默认按 config）")
    run.add_argument("--no-llm", action="store_true", help="跳过模型")
    run.add_argument("--refresh", action="store_true", help="忽略已有 screen.json")
    run.add_argument("--catch-up", action="store_true", help="补跑最近缺失的日期")
    run.add_argument("--max-days", type=int, default=3, help="补跑最多几天")
    run.set_defaults(handler=cmd_run)

    embed = subparsers.add_parser("embed", parents=[], help="算相关度 → runs/<date>/scores.json")
    add_common(embed)
    add_window_args(embed)
    embed.add_argument("--run-id", help="直接指定 run_id")
    embed.add_argument("--profile", help="覆盖 embedding.profile")
    embed.add_argument("--top", type=int, default=10, help="输出前 N 条")
    embed.set_defaults(handler=cmd_embed)

    screen = subparsers.add_parser("screen", parents=[], help="校验外部写入的 screen.json")
    add_common(screen)
    add_window_args(screen)
    screen.add_argument("--file", required=True, help="要校验的 screen.json")
    screen.add_argument("--run-id", help="直接指定 run_id")
    screen.add_argument("--apply", action="store_true", help="把校验后的结果写入 runs/<id>/screen.json")
    screen.add_argument("--strict", action="store_true", help="有拒收时以退出码 3 结束")
    screen.set_defaults(handler=cmd_screen)

    status = subparsers.add_parser("status", parents=[], help="上次抓取与三态覆盖")
    add_common(status)
    status.set_defaults(handler=cmd_status)

    doctor = subparsers.add_parser("doctor", parents=[], help="依赖 / 路径 / 每刊可达性自检")
    add_common(doctor)
    doctor.add_argument("--offline", action="store_true", help="跳过网络探测")
    doctor.set_defaults(handler=cmd_doctor)

    journals = subparsers.add_parser("journals", parents=[], help="list / add / test")
    add_common(journals)
    journals_sub = journals.add_subparsers(dest="journals_command")
    journals_list = journals_sub.add_parser("list", parents=[], help="列出已配置期刊")
    add_common(journals_list)
    journals_list.set_defaults(handler=cmd_journals)
    journals_add = journals_sub.add_parser("add", parents=[], help="新增一本刊")
    add_common(journals_add)
    journals_add.add_argument("name", help="期刊全名")
    journals_add.add_argument("--adapter", default="rss", choices=["rss", "html", "crossref", "manual", "custom"])
    journals_add.add_argument("--feed", help="RSS/Atom 地址")
    journals_add.add_argument("--url", help="HTML 栏目页或 crossref 没用")
    journals_add.add_argument("--issn", help="ISSN（crossref 必填）")
    journals_add.add_argument("--surface", help="官网栏目名，如 Latest Research")
    journals_add.add_argument("--item-selector", help="html 适配器的条容器选择器")
    journals_add.set_defaults(handler=cmd_journals)
    journals_test = journals_sub.add_parser("test", parents=[], help="抓一次并打印前 3 条")
    add_common(journals_test)
    journals_test.add_argument("name", help="期刊全名或 slug")
    journals_test.add_argument("--days", type=int, default=1, help="抓最近 N 天（默认 1）")
    journals_test.set_defaults(handler=cmd_journals)
    journals.set_defaults(handler=cmd_journals)
    journals.set_defaults(journals_command="list")

    schedule = subparsers.add_parser("schedule", parents=[], help="print / install / uninstall")
    add_common(schedule)
    schedule_sub = schedule.add_subparsers(dest="schedule_command")
    for name, help_text in (("print", "打印安装命令"), ("install", "写 wrapper 并注册"),
                            ("uninstall", "注销定时任务")):
        child = schedule_sub.add_parser(name, parents=[], help=help_text)
        add_common(child)
        child.add_argument("--time", help="HH:MM，覆盖 config.schedule.time")
        child.add_argument("--apply", action="store_true", help="真的写入系统调度器")
        child.set_defaults(handler=cmd_schedule)
    schedule.set_defaults(handler=cmd_schedule)
    schedule.set_defaults(schedule_command="print")

    deploy = subparsers.add_parser("deploy", parents=[], help="把配置骨架与 agent 说明书装进 vault")
    add_common(deploy)
    deploy.add_argument("--skill-dir", help="说明书输出目录（默认 .litradar/skills）")
    deploy.set_defaults(handler=cmd_deploy)

    upgrade = subparsers.add_parser("upgrade", parents=[], help="显示升级方式")
    add_common(upgrade)
    upgrade.set_defaults(handler=cmd_upgrade)

    zotero = subparsers.add_parser("zotero", parents=[], help="collections / push")
    add_common(zotero)
    zotero_sub = zotero.add_subparsers(dest="zotero_command")
    zotero_collections = zotero_sub.add_parser("collections", parents=[], help="列出收藏夹")
    add_common(zotero_collections)
    zotero_collections.add_argument("--api", help="Zotero 本地 API（默认取配置）")
    zotero_collections.set_defaults(handler=cmd_zotero)
    zotero_push = zotero_sub.add_parser("push", parents=[], help="把某天的文章推进 Zotero")
    add_common(zotero_push)
    add_window_args(zotero_push)
    zotero_push.add_argument("--run-id")
    zotero_push.add_argument("--only-relevant", action="store_true")
    zotero_push.add_argument("--force", action="store_true", help="不按 DOI 查重")
    zotero_push.add_argument("--api", help="Zotero 本地 API（默认取配置）")
    zotero_push.set_defaults(handler=cmd_zotero)
    zotero.set_defaults(handler=cmd_zotero)
    zotero.set_defaults(zotero_command="collections")

    return parser


def _force_utf8_streams() -> None:
    """Windows consoles default to cp1252; the CLI prints Chinese every day."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if _arg(args, "version"):
        if _arg(args, "as_json"):
            print(json.dumps({"engine": __version__, "schema": SCHEMA}, ensure_ascii=False))
        else:
            print(f"litradar {__version__}")
        return EXIT_OK

    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_CONFIG

    try:
        return handler(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG
    except LockBusy as exc:
        print(f"已有任务在运行：{exc}", file=sys.stderr)
        return EXIT_LOCKED
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return EXIT_CANCELLED
    except EngineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - the CLI is the outermost boundary
        if _arg(args, "verbose"):
            traceback.print_exc()
        else:
            print(f"未预期异常：{type(exc).__name__}: {exc}（加 --verbose 看栈）", file=sys.stderr)
        return EXIT_FAIL
