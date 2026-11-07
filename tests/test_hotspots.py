"""Weekly recap: trends are allowed, but only when each one is pinned to papers."""

import datetime
import json

from helpers import make_context, sample_article, temp_dir

from litradar import hotspots, llm
from litradar.model import JournalScan
from litradar.paths import Paths
from litradar.pipeline import assemble_weekly, render_report
from litradar.state import atomic_write_json
from litradar.window import Window, build_window


def _article(doi="10.1/a", title="Alpha paper", date="2026-11-02"):
    return sample_article(
        doi=doi, title=title, journal="Journal A",
        online_date=datetime.date.fromisoformat(date),
    )


def _payload(article, *more, **overrides):
    row = {
        "title": "本周方向：正选择",
        "body": "本周多篇论文从群体基因组数据出发，共同指向受选择位点的定位方法；摘要未给出功能验证结果。",
        "evidence": [article.id, *(item.id for item in more)],
    }
    row.update(overrides)
    return {"hotspots": [row]}


def test_parse_accepts_evidence_backed_hotspot():
    article = _article()
    items, rejected, notes = hotspots.parse(_payload(article, _article(doi="10.1/b")), [article, _article(doi="10.1/b")])
    assert len(items) == 1 and not rejected
    assert len(items[0].evidence) == 2


def test_parse_rejects_a_single_source():
    article = _article()
    items, rejected, _ = hotspots.parse(_payload(article), [article])
    assert not items
    assert "证据不足" in rejected[0][1]


def test_parse_drops_unknown_evidence_ids():
    article = _article()
    other = _article(doi="10.1/b")
    payload = _payload(article, other)
    payload["hotspots"][0]["evidence"] = [article.id, "sha1:deadbeef0000"]
    items, rejected, notes = hotspots.parse(payload, [article, other])
    assert not items  # one real + one invented => still below two
    assert any("候选外" in note for note in notes) or rejected


def test_parse_truncates_long_body_and_notes_short_one():
    article = _article()
    other = _article(doi="10.1/b")
    long_row = _payload(article, other, body="长" * 500)
    items, _, notes = hotspots.parse(long_row, [article, other])
    assert len(items[0].body) <= hotspots.BODY_MAX
    assert any("超长" in note for note in notes)

    short_row = _payload(article, other, body="太短")
    items, _, notes = hotspots.parse(short_row, [article, other])
    assert any("偏短" in note for note in notes)


def test_parse_ignores_payload_without_array():
    article = _article()
    items, rejected, notes = hotspots.parse({"nope": []}, [article])
    assert not items and not rejected
    assert any("hotspots" in note for note in notes)


def test_build_messages_carries_ids():
    articles = [_article(doi="10.1/a"), _article(doi="10.1/b", title="Beta")]
    text = "\n".join(message["content"] for message in hotspots.build_messages("方向", articles))
    for article in articles:
        assert article.id in text


def _write_store(paths: Paths, run_id: str, window: Window, items, scans=None):
    payload = {
        "schema": 2,
        "run_id": run_id,
        "engine": "2.1.0",
        "scan_at": "2026-11-06T09:00:00+08:00",
        "window": window.to_dict(),
        "journals": [scan.to_dict() for scan in (scans or [])],
        "items": [article.to_dict() for article in items],
        "date_unverified": [],
        "out_of_window": [],
        "excluded_type": [],
    }
    atomic_write_json(paths.store_file(run_id), payload)
    return payload


def test_assemble_weekly_merges_daily_stores_and_lists_gaps():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        monday = Window(kind="day", start=datetime.date(2026, 11, 2), end=datetime.date(2026, 11, 2))
        tuesday = Window(kind="day", start=datetime.date(2026, 11, 3), end=datetime.date(2026, 11, 3))
        scan = JournalScan(journal="Journal A", status="ok", items_seen=1)
        _write_store(ctx.paths, "2026-11-02", monday, [_article(doi="10.1/a")], [scan])
        _write_store(ctx.paths, "2026-11-03", tuesday, [_article(doi="10.1/b", title="Beta")], [scan])

        week = build_window("week_to_date", ref="2026-11-06")
        path = assemble_weekly(ctx, week)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert {item["doi"] for item in payload["items"]} == {"10.1/a", "10.1/b"}
        assert payload["missing_days"] == ["2026-11-04", "2026-11-05", "2026-11-06"]
        assert payload["window"]["kind"] == "week_to_date"
        assert len(payload["journals"]) == 1


def test_assemble_weekly_includes_already_seen_items():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        monday = Window(kind="day", start=datetime.date(2026, 11, 2), end=datetime.date(2026, 11, 2))
        article = _article()
        payload = _write_store(ctx.paths, "2026-11-02", monday, [article])
        payload["items"][0]["already_seen"] = True
        atomic_write_json(ctx.paths.store_file("2026-11-02"), payload)

        week = build_window("week_to_date", ref="2026-11-02")
        merged = json.loads(assemble_weekly(ctx, week).read_text(encoding="utf-8"))
        assert len(merged["items"]) == 1


def test_weekly_render_writes_hotspots_and_section():
    with temp_dir() as tmp:
        ctx = make_context(tmp, screening={"mode": "llm"})
        article = _article()
        second = _article(doi="10.1/b", title="Beta paper")
        week = build_window("week_to_date", ref="2026-11-06")
        scan = JournalScan(journal="Journal A", status="ok", items_seen=2)
        _write_store(ctx.paths, week.run_id, week, [article, second], [scan])

        calls = []

        def fake_chat(config, messages, **kwargs):
            calls.append(messages[0]["content"])
            if "中文文献编辑" in messages[0]["content"]:
                return {"papers": []}
            if "中文学术周报编辑" in messages[0]["content"]:
                return {"hotspots": [{
                    "title": "本周方向：正选择",
                    "body": "本周两篇论文都使用群体基因组数据定位受选择位点；摘要未给出功能验证结果，适合作为方法学参考。",
                    "evidence": [article.id, second.id],
                }]}
            return {"results": [
                {"id": article.id, "relevant": True, "relevance": 0.9},
                {"id": second.id, "relevant": True, "relevance": 0.8},
            ]}

        original = llm.chat_json
        llm.chat_json = fake_chat
        try:
            summary = render_report(ctx, week.run_id, use_llm=True)
        finally:
            llm.chat_json = original

        assert len(calls) == 3  # screening + deepread + hotspots
        text = summary.report_path.read_text(encoding="utf-8")
        assert "kind: weekly" in text
        assert "## 本周热点" in text
        assert "本周方向：正选择" in text
        assert "依据：" in text
        assert summary.hotspots == 1
        stored = json.loads(
            (ctx.paths.run_dir(week.run_id) / "hotspots.json").read_text(encoding="utf-8")
        )
        assert stored["hotspots"][0]["evidence"] == [article.id, second.id]
