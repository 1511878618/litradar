"""Repair and backfill batches without discarding previously collected papers."""

from datetime import timedelta

from .config import load_journals
from .pipeline import fetch_all, load_store, render_report
from .state import atomic_write_json, read_json
from .util import iso, today
from .window import Window


def repair(ctx, run_id, *, journal=None, failed_only=True, use_llm=False):
    store = load_store(ctx.paths, run_id)
    failed = {s.journal for s in store.scans if not s.healthy}
    configured = load_journals(ctx.config)
    if journal:
        journals = [j for j in configured if j["name"] == journal or j["slug"] == journal]
        if not journals:
            from .util import ConfigError

            raise ConfigError(f"未找到期刊：{journal}")
    else:
        journals = [j for j in configured if not failed_only or j["name"] in failed]
    if not journals:
        return {"run_id": run_id, "retried": 0}
    # Retry must bypass a cached error response and re-check the source.
    ctx.fetcher.no_cache = True
    fetch_all(ctx, store.window, journals, merge_existing=True)
    result = render_report(ctx, run_id, use_llm=use_llm)
    return {"run_id": run_id, "retried": len(journals), "items": result.total, "coverage": result.coverage}


def maintain(ctx, *, days=3, ref=None, use_llm=False):
    base = ref or today(ctx.tz)
    journals = load_journals(ctx.config)
    ctx.fetcher.no_cache = True
    results = []
    # Each previous day is queried again: catches delayed publisher deposits.
    for offset in range(max(0, min(days, 30)), 0, -1):
        day = base - timedelta(days=offset)
        window = Window(kind="day", start=day, end=day, tz=ctx.tz)
        before = read_json(ctx.paths.store_file(window.run_id), default={})
        old_ids = {r["id"] for r in before.get("items", [])}
        ctx.logger.info(f"回查／补齐 {day}")
        fetch_all(ctx, window, journals, merge_existing=True)
        payload = read_json(ctx.paths.store_file(window.run_id))
        late = 0
        if before:
            for row in payload["items"]:
                if row["id"] not in old_ids and not row.get("already_seen"):
                    row.setdefault("extra", {})["late_arrival"] = True
                    row["extra"]["discovered_at"] = iso()
                    late += 1
            atomic_write_json(ctx.paths.store_file(window.run_id), payload)
        report = render_report(ctx, window.run_id, use_llm=use_llm)
        results.append(
            {
                "run_id": window.run_id,
                "backfilled": not bool(before),
                "late": late,
                "coverage": report.coverage,
            }
        )
    return results
