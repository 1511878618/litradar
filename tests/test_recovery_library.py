"""Repairs preserve batches; feedback and version links remain reversible."""

from datetime import date

from helpers import FakeFetcher, make_context, sample_article, temp_dir, write_journal

from litradar.adapters.crossref import CrossrefAdapter
from litradar.config import load_journals
from litradar.fetcher import Response
from litradar.library import apply_feedback, feedback, version_links
from litradar.pipeline import fetch_all, load_store, render_report
from litradar.recovery import maintain, repair
from litradar.render import ReportInput, final_score, select_highlights
from litradar.state import atomic_write_json, read_json
from litradar.validate import Result
from litradar.window import Window

W = Window(kind="day", start=date(2026, 11, 4), end=date(2026, 11, 4))


def manual(tmp, name, items):
    return write_journal(tmp, {"name": name, "adapter": "manual", "items": items})


def row(title, doi, day="2026-11-04"):
    return {"title": title, "doi": doi, "url": "https://example.org/" + doi, "date": day}


def test_single_journal_retry_keeps_other_papers_and_new_flags():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        manual(tmp, "B", [row("Cardiac prediction", "10.1/b")])
        fetch_all(ctx, W, load_journals(ctx.config))
        render_report(ctx, W.run_id, use_llm=False)
        manual(tmp, "A", [row("Genome wheat", "10.1/a"), row("Chromatin assembly", "10.1/c")])
        repair(ctx, W.run_id, journal="A")
        store = load_store(ctx.paths, W.run_id)
        assert {a.doi for a in store.items} == {"10.1/a", "10.1/b", "10.1/c"}
        assert len(store.scans) == 2 and not store.already_seen
        repair(ctx, W.run_id, journal="A")
        assert len(load_store(ctx.paths, W.run_id).items) == 3
        ctx.close()


def test_retry_outage_keeps_usable_result_and_records_failed_attempt():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        fetch_all(ctx, W, load_journals(ctx.config))
        j = load_journals(ctx.config)[0]
        j.update(adapter="rss", feed="https://broken.example/feed")
        fetch_all(ctx, W, [j], merge_existing=True)
        store = load_store(ctx.paths, W.run_id)
        assert len(store.items) == 1 and store.scans[0].healthy
        assert "重试失败" in store.scans[0].reason
        ctx.close()


def test_maintain_marks_late_arrivals_but_keeps_original_publication_date():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        fetch_all(ctx, W, load_journals(ctx.config))
        render_report(ctx, W.run_id, use_llm=False)
        manual(tmp, "A", [row("Genome wheat", "10.1/a"), row("Cardiac MRI", "10.1/late")])
        result = maintain(ctx, days=1, ref=date(2026, 11, 5))
        store = load_store(ctx.paths, W.run_id)
        late = next(a for a in store.items if a.doi == "10.1/late")
        assert result[0]["late"] == 1 and late.extra["late_arrival"]
        assert late.online_date == date(2026, 11, 4)
        maintain(ctx, days=1, ref=date(2026, 11, 5))
        assert len(load_store(ctx.paths, W.run_id).items) == 2
        assert next(a for a in load_store(ctx.paths, W.run_id).items if a.doi == "10.1/late").extra[
            "late_arrival"
        ]
        ctx.close()


def test_maintain_fills_missing_days():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        results = maintain(ctx, days=2, ref=date(2026, 11, 5))
        assert len(results) == 2 and all(r["backfilled"] for r in results)
        assert ctx.paths.store_file("2026-11-03").exists() and ctx.paths.store_file("2026-11-04").exists()
        ctx.close()


def test_feedback_changes_ranking_and_undo_removes_adjustment():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        a = sample_article(title="Cardiac magnetic resonance prediction markers")
        result = Result(id=a.id, relevant=True, relevance=0.8)
        base = final_score(a, result, None, ctx.config)
        feedback(ctx.paths, a, "irrelevant")
        apply_feedback(ctx.paths, [a])
        assert final_score(a, result, None, ctx.config) < base
        feedback(ctx.paths, a, "clear")
        apply_feedback(ctx.paths, [a])
        assert final_score(a, result, None, ctx.config) == base and "feedback_reason" not in a.extra
        feedback(ctx.paths, a, "less")
        similar = sample_article(doi="10.1/similar", title="Cardiac magnetic resonance prediction validation")
        apply_feedback(ctx.paths, [similar])
        assert similar.extra["feedback_adjustment"] < 0
        ctx.close()


def test_version_links_require_explicit_evidence_and_preserve_records():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        pre = sample_article(
            doi="10.1/pre",
            title="Same title",
            extra={
                "preprint": True,
                "relations": {"is-preprint-of": [{"id": "10.1/final", "id-type": "doi"}]},
            },
        )
        final = sample_article(doi="10.1/final", title="Same title")
        unrelated = sample_article(doi="10.1/other", title="Same title")
        links = version_links(ctx.paths, [pre, final, unrelated])
        assert "10.1/other" not in links
        assert pre.extra["superseded_by"] == "10.1/final" and final.extra["versions"]
        assert pre.doi == "10.1/pre" and final.doi == "10.1/final"
        assert unrelated.extra.get("versions") is None
        data = ReportInput(
            run_id=W.run_id,
            window=W,
            scans=[],
            items=[pre, final],
            config=ctx.config,
            results={a.id: Result(id=a.id, relevant=True, relevance=0.9) for a in [pre, final]},
        )
        assert all(a.id != pre.id for a in select_highlights(data))
        ctx.close()


def test_crossref_fetches_all_pages_and_deduplicates():
    from urllib.parse import parse_qs, urlparse

    calls = []

    def route(url):
        calls.append(url)
        offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
        return {
            "message": {
                "total-results": 3,
                "items": [
                    {"DOI": f"10.1/{i}", "title": [f"Paper {i}"], "type": "journal-article"}
                    for i in ([0, 1] if offset == 0 else [1, 2])
                ],
            }
        }

    f = FakeFetcher({"api.crossref.org": route})
    items, scan = CrossrefAdapter().fetch({"name": "A", "issn": "1234", "rows": 2}, f, W)
    assert len(calls) == 2 and len(items) == 3 and scan.status == "metadata-only"


def test_crossref_incomplete_pagination_is_visible():
    n = 0

    def route(url):
        nonlocal n
        n += 1
        if n > 1:
            return Response(url=url, status=503, error="outage")
        return {
            "message": {
                "total-results": 3,
                "items": [{"DOI": "10.1/a", "title": ["Paper"], "type": "journal-article"}],
            }
        }

    items, scan = CrossrefAdapter().fetch(
        {"name": "A", "issn": "1234", "rows": 1}, FakeFetcher({"api.crossref.org": route}), W
    )
    assert len(items) == 1 and "分页未完成" in scan.reason


def test_library_rebuild_uses_cache_and_never_rewrites_report():
    from litradar.library import rebuild_presentations

    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        fetch_all(ctx, W, load_journals(ctx.config))
        result = render_report(ctx, W.run_id, use_llm=False)
        before = result.report_path.read_text()
        a = load_store(ctx.paths, W.run_id).items[0]
        feedback(ctx.paths, a, "less")
        rebuild_presentations(ctx)
        presentation = read_json(ctx.paths.run_dir(W.run_id) / "presentation.json")
        assert presentation["extras"][a.id]["feedback_adjustment"] < 0
        assert result.report_path.read_text() == before
        assert not ctx.fetcher.calls
        ctx.close()


def test_repair_moves_newly_verified_paper_out_of_audit():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        manual(tmp, "A", [row("Genome wheat", "10.1/a")])
        fetch_all(ctx, W, load_journals(ctx.config))
        p = ctx.paths.store_file(W.run_id)
        payload = read_json(p)
        a = payload["items"].pop()
        a["online_date"] = None
        a["date_source"] = "missing"
        payload["date_unverified"] = [a]
        atomic_write_json(p, payload)
        repair(ctx, W.run_id, journal="A")
        stored = load_store(ctx.paths, W.run_id)
        assert len(stored.items) == 1 and stored.date_unverified == []
        ctx.close()
