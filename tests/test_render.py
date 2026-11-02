"""The report skeleton is a contract; the body must contain every candidate."""

from datetime import date

from helpers import make_config, sample_article, temp_dir

from litradar.model import JournalScan
from litradar.render import (
    ReportInput,
    coverage_of,
    final_score,
    render_betternote,
    render_markdown,
    select_highlights,
    update_index,
    write_betternotes,
)
from litradar.validate import Result, ValidationReport


def _data(**overrides):
    scans = [
        JournalScan(journal="Journal A", status="ok", surface="Latest", items_seen=3),
        JournalScan(
            journal="Journal B",
            status="metadata-only",
            surface="Advance",
            items_seen=1,
            reason="Crossref 仅提供元数据",
            visibility="metadata-only",
        ),
    ]
    items = [
        sample_article(doi="10.1/a", title="Alpha paper", journal="Journal A"),
        sample_article(
            doi="10.1/b",
            title="Beta paper",
            journal="Journal B",
            date_source="crossref",
            abstract_raw=None,
        ),
        sample_article(doi="10.1/c", title="Gamma paper", journal="Journal A", abstract_raw=None),
    ]
    results = {
        items[0].id: Result(
            id=items[0].id,
            relevant=True,
            relevance=0.91,
            why="用群体基因组数据识别正选择位点",
            topic_tags=["正选择", "群体基因组学"],
            abstract_en=items[0].abstract_raw,
            abstract_mode="verbatim",
        ),
        items[1].id: Result(id=items[1].id, relevant=False, relevance=0.2),
    }
    data = ReportInput(
        run_id="2026-10-30",
        window=__import__("litradar.window", fromlist=["Window"]).Window(
            kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30)
        ),
        scans=scans,
        items=items,
        results=results,
        validation=ValidationReport(results=results),
        screening_label="llm-v1（deepseek-chat）",
        scan_at="2026-10-31T09:00:00+08:00",
        generated_at="2026-10-31T09:00:31+08:00",
        config=make_config(__import__("pathlib").Path("/tmp")),
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def test_frontmatter_has_contract_keys():
    markdown, _ = render_markdown(_data())
    assert markdown.startswith("---\n")
    for key in ("title:", "date:", "tags:", "journals:", "window:", "coverage:", "screening:",
                "new_items:", "highlights:"):
        assert key in markdown


def test_full_list_contains_every_candidate():
    data = _data()
    markdown, _ = render_markdown(data)
    for article in data.items:
        assert article.title in markdown


def test_irrelevant_article_is_still_in_the_body():
    data = _data()
    markdown, highlights = render_markdown(data)
    assert "Beta paper" in markdown
    assert "Beta paper" not in [article.title for article in highlights]


def test_highlight_section_contains_only_relevant():
    markdown, highlights = render_markdown(_data())
    assert len(highlights) == 1
    assert "## 与你最相关（1 篇）" in markdown
    assert "相关性：0.91" in markdown


def test_highlights_are_capped_by_config():
    data = _data()
    data.results = {
        article.id: Result(id=article.id, relevant=True, relevance=0.9 - index / 10)
        for index, article in enumerate(data.items)
    }
    data.config = make_config(__import__("pathlib").Path("/tmp"),
                              report={"highlights": 2, "abstract_chars": 1500})
    highlights = select_highlights(data)
    assert len(highlights) == 2


def test_highlights_sorted_by_score():
    data = _data()
    data.results = {
        data.items[0].id: Result(id=data.items[0].id, relevant=True, relevance=0.5),
        data.items[1].id: Result(id=data.items[1].id, relevant=True, relevance=0.9),
        data.items[2].id: Result(id=data.items[2].id, relevant=True, relevance=0.7),
    }
    highlights = select_highlights(data)
    assert [article.title for article in highlights][0] == "Beta paper"


def test_coverage_labels():
    assert coverage_of([JournalScan(journal="J", status="ok")]) == "complete"
    assert coverage_of([JournalScan(journal="J", status="ok"),
                        JournalScan(journal="K", status="blocked")]) == "partial"
    assert coverage_of([JournalScan(journal="J", status="blocked")]) == "failed"
    assert coverage_of([]) == "failed"


def test_partial_coverage_warning():
    markdown, _ = render_markdown(_data())
    assert "partial coverage" in markdown
    assert "不可表述为「本周无新文章」" in markdown


def test_all_failed_warning():
    data = _data()
    data.scans = [JournalScan(journal="A", status="blocked", reason="HTTP 403 (Cloudflare)")]
    data.items = []
    data.results = {}
    markdown, _ = render_markdown(data)
    assert "全部期刊抓取失败" in markdown
    assert "coverage: failed" in markdown


def test_metadata_date_is_flagged():
    markdown, _ = render_markdown(_data())
    assert "（元数据日期，未在官网核实）" in markdown


def test_missing_abstract_placeholder():
    markdown, _ = render_markdown(_data())
    assert "No abstract supplied by the journal" in markdown


def test_no_fabricated_trend_section():
    markdown, _ = render_markdown(_data())
    for forbidden in ("总体趋势", "研究热点", "本期亮点"):
        assert forbidden not in markdown


def test_audit_sections():
    data = _data()
    data.out_of_window = [sample_article(doi="10.1/old", title="Old paper")]
    data.date_unverified = [sample_article(doi="10.1/nodate", title="Undated paper")]
    data.excluded_type = [sample_article(doi="10.1/ed", title="Editorial: x")]
    markdown, _ = render_markdown(data)
    assert "## 越窗" in markdown
    assert "## 待核验" in markdown
    assert "## 非研究类型" in markdown


def test_validation_log_is_rendered():
    data = _data()
    data.validation = ValidationReport(
        results=data.results,
        rejected=[("sha1:bad", "不在本轮候选集内")],
        notes=["sha1:x: 相似度 0.71，已降级为概述"],
    )
    markdown, _ = render_markdown(data)
    assert "## 校验记录" in markdown
    assert "拒收：sha1:bad" in markdown


def test_index_inserts_and_replaces():
    line_one = "- [[2026-10-29]] · 9 篇（置顶 2） · 覆盖 partial · 2026-10-30"
    text = update_index(None, line_one, "2026-10-29")
    assert text.startswith("# 文献雷达索引")
    assert line_one in text

    repeated = update_index(text, line_one, "2026-10-29")
    assert repeated.count("2026-10-29") == 1

    line_two = "- [[2026-10-30]] · 4 篇（置顶 1） · 覆盖 complete · 2026-10-31"
    text = update_index(text, line_two, "2026-10-30")
    assert text.index("2026-10-30") < text.index("2026-10-29")

    replaced = update_index(text, line_two.replace("4 篇", "5 篇"), "2026-10-30")
    assert "5 篇" in replaced
    assert replaced.count("[[2026-10-30]]") == 1


def test_index_dataview_mode():
    text = update_index("ignored", "- x", "2026-10-30", mode="dataview")
    assert "```dataview" in text
    assert "TABLE new_items" in text


def test_index_none_mode_is_noop():
    assert update_index("keep", "- x", "2026-10-30", mode="none") == "keep"


def test_betternote_written_once():
    with temp_dir() as tmp:
        config = make_config(tmp, **{"output": {"betternote_dir": "notes"}})
        from litradar.paths import Paths

        paths = Paths(tmp, config)
        article = sample_article(doi="10.1234/abc.1")
        written = write_betternotes(paths, [article], {}, config)
        assert len(written) == 1
        assert written[0].exists()
        assert "@abc.1.md" in str(written[0])
        text = written[0].read_text(encoding="utf-8")
        assert "tags: unread" in text
        written[0].write_text("user edited", encoding="utf-8")
        assert write_betternotes(paths, [article], {}, config) == []
        assert written[0].read_text(encoding="utf-8") == "user edited"


def test_betternote_disabled_by_default():
    with temp_dir() as tmp:
        from litradar.paths import Paths

        config = make_config(tmp)
        assert write_betternotes(Paths(tmp, config), [sample_article()], {}, config) == []


def test_final_score_fusion():
    article = sample_article(doi="10.1/a")
    result = Result(id=article.id, relevance=0.8)
    assert abs(final_score(article, result, None, None) - 0.8) < 1e-9
    assert abs(final_score(article, result, {"10.1/a": 0.5}, None) - (0.7 * 0.8 + 0.3 * 0.5)) < 1e-9
    assert abs(final_score(article, None, {"10.1/a": 0.5}, None) - 0.5) < 1e-9


def test_render_betternote_frontmatter():
    article = sample_article(doi="10.1/zzz", title='Title with "quotes"')
    text = render_betternote(article, None)
    assert 'title: "Title with \\"quotes\\""' in text
    assert "doi: 10.1/zzz" in text


def test_abstract_is_truncated_by_config():
    from pathlib import Path

    from litradar.render import abstract_limit

    data = _data()
    data.config = make_config(Path("/tmp"), report={"highlights": 0, "abstract_chars": 200})
    data.results = {}
    data.items[0].abstract_raw = "word " * 800
    assert abstract_limit(data.config) == 200
    markdown, _ = render_markdown(data)
    line = next(line for line in markdown.splitlines() if line.startswith("word "))
    assert line.endswith("…")
    assert len(line) <= 210


def test_highlight_requires_min_relevance_even_if_model_says_relevant():
    data = _data()
    data.results = {article.id: Result(id=article.id, relevant=True, relevance=0.2) for article in data.items}
    assert select_highlights(data) == []


def test_preprint_is_labelled_everywhere():
    data = _data()
    preprint = sample_article(
        doi="10.1101/2026.10.30.000001",
        title="A bioRxiv preprint about selection",
        journal="bioRxiv",
        extra={"preprint": "bioRxiv"},
    )
    data.items.append(preprint)
    data.results[preprint.id] = Result(id=preprint.id, relevant=True, relevance=0.95,
                                       why="与方向相关")
    markdown, highlights = render_markdown(data)
    assert preprint in highlights
    assert "预印本：是（未经同行评审" in markdown
    assert "预印本·未经同行评审" in markdown


def test_healthy_scan_reason_is_visible():
    data = _data()
    data.scans[0].reason = "仅纳入分类：genomics"
    markdown, _ = render_markdown(data)
    assert "> 说明：" in markdown
    assert "仅纳入分类：genomics" in markdown


def test_metadata_only_reason_is_not_repeated_as_note():
    data = _data()
    markdown, _ = render_markdown(data)
    assert "> 说明：" not in markdown


def test_preprint_version_is_shown():
    data = _data()
    preprint = sample_article(doi="10.1101/x.1", title="Revised preprint", journal="bioRxiv",
                              extra={"preprint": "bioRxiv", "version": "2"})
    data.items.append(preprint)
    data.results[preprint.id] = Result(id=preprint.id, relevant=True, relevance=0.9)
    markdown, _ = render_markdown(data)
    assert "预印本发布日期：" in markdown
    assert "（v2）" in markdown
    assert "预印本·未经同行评审 · v2" in markdown


def test_image_is_embedded_once_for_highlights():
    data = _data()
    data.items[0].extra["image_path"] = "assets/abc123.png"
    markdown, _ = render_markdown(data)
    assert "![Alpha paper](assets/abc123.png)" in markdown
    assert markdown.count("assets/abc123.png") == 1


def test_body_image_shown_for_non_highlighted_items():
    data = _data()
    data.items[1].extra["image_path"] = "assets/beta.png"
    markdown, _ = render_markdown(data)
    assert "![Beta paper](assets/beta.png)" in markdown
    assert markdown.count("assets/beta.png") == 1


def test_remote_image_url_is_used_when_not_downloaded():
    data = _data()
    data.items[0].extra["image_url"] = "https://example.org/fig.png"
    markdown, _ = render_markdown(data)
    assert "![Alpha paper](https://example.org/fig.png)" in markdown


def _impact_data():
    from pathlib import Path

    from litradar.model import JournalScan
    from litradar.render import ReportInput
    from litradar.window import Window

    scans = [
        JournalScan(journal="JAMIA", status="ok", surface="Advance"),
        JournalScan(journal="bioRxiv", status="ok", surface="Preprints"),
        JournalScan(journal="Nature", status="ok", surface="Latest"),
    ]
    items = [
        sample_article(doi="10.1/j", title="JAMIA paper", journal="JAMIA"),
        sample_article(doi="10.1/b", title="bioRxiv paper", journal="bioRxiv",
                       extra={"preprint": "bioRxiv", "version": "1"}),
        sample_article(doi="10.1/n", title="Nature paper", journal="Nature"),
    ]
    return ReportInput(
        run_id="2026-10-30",
        window=Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30)),
        scans=scans,
        items=items,
        results={},
        config=make_config(Path("/tmp")),
        journals={
            "Nature": {"impact_factor": 48.5, "heading_level": 3},
            "JAMIA": {"impact_factor": 6.0, "heading_level": 5},
            "bioRxiv": {"preprint": True, "heading_level": 5},
        },
        generated_at="2026-10-31T09:00:00+08:00",
    )


def test_journals_sorted_by_impact_factor_with_preprints_last():
    markdown, _ = render_markdown(_impact_data())
    assert markdown.index("Nature（1 篇）") < markdown.index("JAMIA（1 篇）") < markdown.index("bioRxiv（1 篇）")


def test_heading_levels_follow_journal_config():
    markdown, _ = render_markdown(_impact_data())
    assert "### Nature（1 篇）" in markdown
    assert "#### Nature paper" in markdown
    assert "##### JAMIA（1 篇）" in markdown
    assert "###### JAMIA paper" in markdown
    assert "##### bioRxiv（1 篇）" in markdown


def test_coverage_table_shows_impact_factor():
    markdown, _ = render_markdown(_impact_data())
    assert "影响因子" in markdown
    assert "48.5" in markdown
    assert "6" in markdown
    assert "—" in markdown  # preprint has no IF


def test_preprint_flag_from_article_extra():
    data = _impact_data()
    data.journals = {"Nature": {"impact_factor": 48.5, "heading_level": 3},
                     "JAMIA": {"impact_factor": 6.0, "heading_level": 5}}
    markdown, _ = render_markdown(data)
    # bioRxiv is not in the metadata map; the article extra marks it as a preprint.
    assert markdown.index("JAMIA（1 篇）") < markdown.index("bioRxiv（1 篇）")


def test_combined_report_title_and_kind():
    data = _impact_data()
    markdown, _ = render_markdown(data)
    assert 'title: "文献雷达 2026-10-30"' in markdown
    assert "kind: daily" in markdown
    assert "## 期刊（2 篇）" in markdown
    assert "## 预印本（1 篇）" in markdown


def test_topic_section_chart_and_wordcloud():
    data = _data()
    data.wordcloud_path = "assets/wc.svg"
    markdown, _ = render_markdown(data)
    assert "## 今日主题分布" in markdown
    assert "```mermaid" in markdown
    assert "pie showData" in markdown
    assert "| 主题 | 篇数 |" in markdown
    assert "![今日词云](assets/wc.svg)" in markdown
    assert "标签：" in markdown


def test_topic_section_can_be_disabled():
    from pathlib import Path

    data = _data()
    data.config = make_config(Path("/tmp"), report={"topic_chart": False})
    markdown, _ = render_markdown(data)
    assert "## 今日主题分布" not in markdown


def test_summary_zh_is_rendered_for_highlights():
    data = _data()
    result = data.results[data.items[0].id]
    result.summary_zh = "用群体基因组数据识别正选择位点"
    markdown, _ = render_markdown(data)
    assert "- 一句话解读：用群体基因组数据识别正选择位点" in markdown


def test_index_keeps_one_row_per_day():
    day = "- [[2026-10-30]] · 4 篇（置顶 1） · 覆盖 complete · 2026-10-31"
    text = update_index(None, day, "2026-10-30")
    text = update_index(text, day.replace("4 篇", "5 篇"), "2026-10-30")
    assert text.count("[[2026-10-30]]") == 1
    assert "5 篇" in text
    older = "- [[2026-10-29]] · 2 篇（置顶 0） · 覆盖 complete · 2026-10-30"
    text = update_index(text, older, "2026-10-29")
    assert text.index("[[2026-10-30]]") < text.index("[[2026-10-29]]")
