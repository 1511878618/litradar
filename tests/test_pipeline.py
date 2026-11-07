"""End-to-end pipeline: fetch → store → render → seen, plus the failure paths."""

from datetime import date

from helpers import RDF_FEED, make_context, write_journal

from litradar.config import load_journals
from litradar.fetcher import Response
from litradar.pipeline import fetch_all, load_store, missing_days, render_report
from litradar.state import load_scans, load_seen, read_json
from litradar.util import EngineError
from litradar.window import Window, build_window

WINDOW = Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30))


def _setup(tmp, routes=None, journal=None):
    ctx = make_context(tmp, routes=routes or {"example.org/feed": RDF_FEED},
                       screening={"mode": "keyword"})
    write_journal(tmp, journal or {
        "name": "Example Genetics",
        "adapter": "rss",
        "feed": "https://example.org/feed",
        "surface": "Latest Research",
    })
    journals = load_journals(ctx.config)
    return ctx, journals


def test_fetch_writes_store_with_buckets():
    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp)
        summary = fetch_all(ctx, WINDOW, journals)
        assert len(summary.new_items) == 1
        assert summary.coverage == "complete"
        store = read_json(ctx.paths.store_file("2026-10-30"))
        assert store["schema"] == 2
        assert store["run_id"] == "2026-10-30"
        assert len(store["items"]) == 1
        assert len(store["excluded_type"]) == 1
        assert len(store["date_unverified"]) == 1
        assert store["items"][0]["already_seen"] is False
        assert store["items"][0]["first_seen_at"]


def test_fetch_then_render_produces_report_index_and_seen():
    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp)
        fetch_all(ctx, WINDOW, journals)
        summary = render_report(ctx, "2026-10-30", use_llm=False)
        assert summary.total == 1
        assert summary.report_path.exists()
        report = summary.report_path.read_text(encoding="utf-8")
        assert "## 期刊（1 篇）" in report
        assert "## 覆盖情况" in report
        assert ctx.paths.index_file().exists()
        seen = load_seen(ctx.paths.seen_file)
        assert len(seen) == 1
        assert list(seen.values())[0]["last_run"] == "2026-10-30"
        scans = load_scans(ctx.paths.scans_file)
        assert scans["Example Genetics"]["status"] == "ok"


def test_second_fetch_has_no_new_items_and_render_keeps_report():
    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp)
        fetch_all(ctx, WINDOW, journals)
        first = render_report(ctx, "2026-10-30", use_llm=False)
        before = first.report_path.read_text(encoding="utf-8")

        second = fetch_all(ctx, WINDOW, journals)
        assert second.new_items == []
        assert len(second.already_seen) == 1

        again = render_report(ctx, "2026-10-30", use_llm=False)
        assert again.total == 0
        assert again.report_path.read_text(encoding="utf-8") == before
        index = ctx.paths.index_file().read_text(encoding="utf-8")
        assert index.count("[[2026-10-30]]") == 1


def test_all_failed_coverage_and_warning():
    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp, routes={
            "example.org/feed": Response(url="u", status=403, text="cloudflare"),
        })
        summary = fetch_all(ctx, WINDOW, journals)
        assert summary.all_failed
        assert summary.coverage == "failed"
        result = render_report(ctx, "2026-10-30", use_llm=False)
        assert result.coverage == "failed"
        report = result.report_path.read_text(encoding="utf-8")
        assert "全部期刊抓取失败" in report
        assert "coverage: failed" in report


def test_fallback_chain_is_used_and_annotated():
    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp, routes={
            "example.org/advance": Response(url="u", status=404, text="missing"),
            "example.org/feed": RDF_FEED,
        }, journal={
            "name": "Fallback Journal",
            "adapter": "html",
            "url": "https://example.org/advance",
            "selectors": {"item": "li.article"},
            "fallback": ["rss"],
            "feed": "https://example.org/feed",
        })
        summary = fetch_all(ctx, WINDOW, journals)
        scan = summary.scans[0]
        assert scan.status == "ok"
        assert "已降级到 rss" in (scan.reason or "")
        assert summary.new_items


def test_seen_not_committed_when_render_fails():
    import litradar.pipeline as pipeline

    with __import__("helpers").temp_dir() as tmp:
        ctx, journals = _setup(tmp)
        fetch_all(ctx, WINDOW, journals)
        original = pipeline.write_report

        def boom(*args, **kwargs):
            raise OSError("disk full")

        pipeline.write_report = boom
        try:
            try:
                render_report(ctx, "2026-10-30", use_llm=False)
            except OSError:
                pass
            else:
                raise AssertionError("expected OSError")
        finally:
            pipeline.write_report = original
        assert load_seen(ctx.paths.seen_file) == {}


def test_missing_days_detects_gaps():
    with __import__("helpers").temp_dir() as tmp:
        ctx, _ = _setup(tmp)
        ref = date(2026, 10, 31)
        ctx.paths.store_file("2026-10-30").write_text("{}", encoding="utf-8")
        gaps = missing_days(ctx.paths, "Asia/Shanghai", max_days=3, ref=ref)
        assert date(2026, 10, 30) not in gaps
        assert date(2026, 10, 29) in gaps
        assert len(gaps) == 2


def test_load_store_rejects_newer_schema():
    with __import__("helpers").temp_dir() as tmp:
        ctx, _ = _setup(tmp)
        ctx.paths.store_file("2026-10-30").write_text('{"schema": 99}', encoding="utf-8")
        try:
            load_store(ctx.paths, "2026-10-30")
        except Exception as exc:
            assert "schema" in str(exc)
            assert exc.__class__.__name__ == "ConfigError"
            return
    raise AssertionError("expected EngineError")


def test_render_without_store_is_a_clear_error():
    with __import__("helpers").temp_dir() as tmp:
        ctx, _ = _setup(tmp)
        try:
            render_report(ctx, "2026-10-30", use_llm=False)
        except EngineError as exc:
            assert "fetch" in str(exc)
            return
    raise AssertionError("expected EngineError")


def test_window_from_cli_roundtrip():
    window = build_window(date_str="2026-10-30")
    assert window.run_id == "2026-10-30"
    assert window.days == 1


def test_one_broken_journal_does_not_stop_the_others():
    from helpers import write_journal

    with __import__("helpers").temp_dir() as tmp:
        ctx = make_context(tmp, routes={"example.org/feed": RDF_FEED})
        write_journal(tmp, {"name": "Good Journal", "adapter": "rss",
                            "feed": "https://example.org/feed"})
        write_journal(tmp, {"name": "Broken Journal", "adapter": "html",
                            "url": "https://example.org/gone",
                            "selectors": {"item": "li.x"}})
        journals = load_journals(ctx.config)
        summary = fetch_all(ctx, WINDOW, journals)
        statuses = {scan.journal: scan.status for scan in summary.scans}
        assert statuses["Good Journal"] == "ok"
        assert statuses["Broken Journal"] in ("error", "blocked")
        assert summary.new_items
        assert summary.coverage == "partial"
        result = render_report(ctx, "2026-10-30", use_llm=False)
        report = result.report_path.read_text(encoding="utf-8")
        assert "Broken Journal" in report
        assert "❌" in report or "⛔" in report


def test_run_log_and_layout_are_written():
    from helpers import temp_dir as _temp

    with _temp() as tmp:
        ctx, journals = _setup(tmp)
        fetch_all(ctx, WINDOW, journals)
        render_report(ctx, "2026-10-30", use_llm=False)
        root = tmp / ".litradar"
        for relative in ("store/2026-10-30.json", "runs/2026-10-30/report.md",
                         "state/seen.json", "state/journal_scans.json", "cache",
                         "logs", "journals"):
            assert (root / relative).exists(), relative
        assert tmp / "02-Reading" / "文献雷达" / "2026" / "10" / "2026-10-30.md"


def test_scans_are_persisted_even_when_a_journal_fails():
    from helpers import temp_dir as _temp

    with _temp() as tmp:
        ctx = make_context(tmp, routes={"example.org/feed": RDF_FEED})
        write_journal(tmp, {"name": "Good", "adapter": "rss", "feed": "https://example.org/feed"})
        write_journal(tmp, {"name": "Bad", "adapter": "html", "url": "https://example.org/none",
                            "selectors": {"item": "li.x"}})
        fetch_all(ctx, WINDOW, load_journals(ctx.config))
        scans = load_scans(ctx.paths.scans_file)
        assert scans["Good"]["status"] == "ok"
        assert scans["Bad"]["status"] in ("error", "blocked")
        assert scans["Bad"]["consecutive_failures"] == 1


def test_render_no_llm_does_not_touch_existing_screen_json():
    from helpers import temp_dir as _temp

    with _temp() as tmp:
        ctx, journals = _setup(tmp)
        fetch_all(ctx, WINDOW, journals)
        screen = ctx.paths.screen_file("2026-10-30")
        screen.parent.mkdir(parents=True, exist_ok=True)
        screen.write_text('{"results": [], "screening": "external"}', encoding="utf-8")
        before = screen.read_text(encoding="utf-8")
        render_report(ctx, "2026-10-30", use_llm=False)
        assert screen.read_text(encoding="utf-8") == before


def test_render_attaches_images_to_highlights():
    from helpers import temp_dir as _temp

    with _temp() as tmp:
        ctx = make_context(
            tmp,
            routes={
                "example.org/feed": RDF_FEED,
                "example.org/a1": '<figure><img src="https://example.org/fig1.png"></figure>',
            },
            screening={"mode": "keyword"},
            images={"enabled": True, "max_per_run": 3, "download": True},
        )
        write_journal(tmp, {"name": "Example Genetics", "adapter": "rss",
                            "feed": "https://example.org/feed"})
        ctx.fetcher.bytes_routes["example.org/fig1.png"] = b"PNGDATA"
        fetch_all(ctx, WINDOW, load_journals(ctx.config))
        summary = render_report(ctx, "2026-10-30", use_llm=False)
        assert summary.highlights >= 1
        assert summary.images == 1
        text = summary.report_path.read_text(encoding="utf-8")
        assert "](assets/" in text
        asset = next((summary.report_path.parent / "assets").glob("*.png"))
        assert asset.read_bytes() == b"PNGDATA"


def test_one_report_contains_journal_and_preprint_sections():
    from helpers import temp_dir as _temp

    with _temp() as tmp:
        ctx = make_context(tmp, screening={"mode": "keyword"})
        write_journal(tmp, {
            "name": "Journal X", "adapter": "manual",
            "items": [{"title": "A journal paper on genomes", "url": "https://example.org/j",
                       "date": "2026-10-30",
                       "abstract": "We analyse genome variants in patients."}],
        })
        write_journal(tmp, {
            "name": "bioRxiv", "adapter": "manual", "preprint": True,
            "items": [{"title": "A preprint on genomes", "url": "https://example.org/p",
                       "date": "2026-10-30",
                       "abstract": "We analyse genome variants."}],
        })
        journals = load_journals(ctx.config)
        fetch_all(ctx, WINDOW, journals)

        summary = render_report(ctx, "2026-10-30", use_llm=False)
        assert summary.report_path.name == "2026-10-30.md"
        text = summary.report_path.read_text(encoding="utf-8")
        assert "## 期刊（1 篇）" in text
        assert "## 预印本（1 篇）" in text
        assert "A journal paper on genomes" in text
        assert "A preprint on genomes" in text
        assert text.index("## 期刊") < text.index("## 预印本")
        assert not (summary.report_path.parent / "2026-10-30-preprint.md").exists()
        assert len(load_seen(ctx.paths.seen_file)) == 2


def test_apply_cadence_marks_non_publishing_day():
    from datetime import date

    from litradar.model import JournalScan
    from litradar.pipeline import _apply_cadence
    from litradar.window import Window

    journal = {"name": "Weekly Review", "cadence": "weekly", "weekday": 2}  # Wednesday
    thursday = Window(kind="day", start=date(2026, 11, 5), end=date(2026, 11, 5))
    scan = JournalScan(journal="Weekly Review", status="ok", items_seen=0)
    _apply_cadence(journal, scan, [], thursday)
    assert scan.status == "off-cycle"
    assert scan.reason == "周三更新"
    assert scan.items_seen == 0


def test_apply_cadence_flags_a_missed_issue():
    from datetime import date

    from litradar.model import JournalScan
    from litradar.pipeline import _apply_cadence
    from litradar.window import Window

    journal = {"name": "Weekly Review", "cadence": "weekly", "weekday": 2}
    wednesday = Window(kind="day", start=date(2026, 11, 4), end=date(2026, 11, 4))
    scan = JournalScan(journal="Weekly Review", status="ok", items_seen=0)
    _apply_cadence(journal, scan, [], wednesday)
    assert scan.status == "ok"
    assert "本轮 0 篇" in scan.reason


def test_apply_cadence_leaves_daily_and_non_empty_scans_alone():
    from datetime import date

    from litradar.model import JournalScan
    from litradar.pipeline import _apply_cadence
    from litradar.window import Window

    window = Window(kind="day", start=date(2026, 11, 5), end=date(2026, 11, 5))
    daily = JournalScan(journal="Daily", status="ok", items_seen=0)
    _apply_cadence({"name": "Daily", "cadence": "daily"}, daily, [], window)
    assert daily.status == "ok" and daily.reason is None

    weekly = JournalScan(journal="Weekly Review", status="ok", items_seen=1)
    from helpers import sample_article

    _apply_cadence({"name": "Weekly Review", "cadence": "weekly", "weekday": 2},
                   weekly, [sample_article(online_date=date(2026, 11, 5))], window)
    assert weekly.status == "ok" and weekly.reason is None


def test_split_preprints_writes_two_files():
    from helpers import make_context, write_journal
    from helpers import temp_dir as _temp_dir

    temp_dir = _temp_dir
    from litradar.config import load_journals
    from litradar.pipeline import fetch_all, render_report

    with temp_dir() as tmp:
        ctx = make_context(tmp, output={"split_preprints": True})
        write_journal(tmp, {
            "name": "Journal X", "adapter": "manual",
            "items": [{"title": "A journal paper on genomes chromatin wheat assembly variants", "url": "https://example.org/j",
                       "date": "2026-10-30", "abstract": "We analyse genome variants."}],
        })
        write_journal(tmp, {
            "name": "bioRxiv", "adapter": "manual", "preprint": True,
            "items": [{"title": "A preprint on genomes clinical cardiac markers imaging", "url": "https://example.org/p",
                       "date": "2026-10-30", "abstract": "We analyse genome variants."}],
        })
        fetch_all(ctx, WINDOW, load_journals(ctx.config))
        summary = render_report(ctx, "2026-10-30", use_llm=False)

        main = summary.report_path
        preprint = main.with_name(main.stem + "-preprint.md")
        assert main.exists() and preprint.exists()
        main_text = main.read_text(encoding="utf-8")
        preprint_text = preprint.read_text(encoding="utf-8")
        assert "A journal paper on genomes" in main_text
        assert "A preprint on genomes" not in main_text
        assert "A preprint on genomes" in preprint_text
        assert "A journal paper on genomes" not in preprint_text
        assert "[[2026-10-30-preprint]]" in main_text
        assert "[[2026-10-30]]" in preprint_text
        # Separate assets must reflect each report, not the combined batch.
        import re
        main_cloud = re.search(r"!\[今日词云\]\(([^)]+)\)", main_text).group(1)
        pre_cloud = re.search(r"!\[今日词云\]\(([^)]+)\)", preprint_text).group(1)
        assert main_cloud != pre_cloud
        main_svg = (main.parent / main_cloud).read_text()
        pre_svg = (preprint.parent / pre_cloud).read_text()
        assert "wheat" in main_svg and "cardiac" not in main_svg
        assert "cardiac" in pre_svg and "wheat" not in pre_svg
        assert "1 articles" in main_svg and "1 articles" in pre_svg

        index = (tmp / "02-Reading" / "文献雷达" / "文献雷达索引.md").read_text(encoding="utf-8")
        assert "[[2026-10-30]]" in index and "[[2026-10-30-preprint]]" in index
