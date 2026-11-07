"""Deep reading: the model may annotate candidates, never invent or inflate them."""

from datetime import date

from helpers import make_config, sample_article, temp_dir

from litradar import deepread
from litradar.model import Article
from litradar.render import STATUS_LABELS, ReportInput, render_markdown
from litradar.validate import Result, ValidationReport
from litradar.window import Window


def _article(**overrides):
    return sample_article(**overrides)


def _payload(article: Article, **overrides):
    row = {
        "id": article.id,
        "title_zh": "正选择的群体基因组学研究",
        "field_zh": "进化生物学/群体遗传学",
        "digest_zh": (
            "已有研究显示群体基因组数据能揭示自然选择的痕迹，"
            "但多数方法只适用于单一物种。作者比较了多个物种的基因组数据，"
            "识别出若干候选正选择位点，并指出这些位点可能与适应性性状相关。"
        ),
        "keywords_zh": ["正选择", "群体基因组学", "适应"],
    }
    row.update(overrides)
    return {"papers": [row]}


def test_parse_accepts_a_valid_row():
    article = _article()
    annotations, rejected, notes = deepread.parse(_payload(article), [article])
    assert not rejected and not notes
    item = annotations[article.id]
    assert item.title_zh.startswith("正选择")
    assert item.field_zh.startswith("进化生物学")
    assert item.keywords_zh == ["正选择", "群体基因组学", "适应"]
    assert item.warnings == []


def test_parse_rejects_ids_outside_the_candidate_list():
    article = _article()
    payload = _payload(article)
    payload["papers"][0]["id"] = "sha1:deadbeefdeadbeef"
    annotations, rejected, _ = deepread.parse(payload, [article])
    assert not annotations
    assert rejected and "不在本次候选" in rejected[0][1]


def test_parse_rejects_missing_title_and_digest():
    article = _article()
    for field in ("title_zh", "digest_zh"):
        payload = _payload(article, **{field: "  "})
        annotations, rejected, _ = deepread.parse(payload, [article])
        assert not annotations
        assert rejected and field in rejected[0][1]


def test_parse_flags_numbers_absent_from_title_and_abstract():
    article = _article()
    payload = _payload(article, digest_zh="作者扫描了 123456 个样本，发现正选择信号广泛存在。")
    annotations, rejected, _ = deepread.parse(payload, [article])
    assert not rejected
    warnings = annotations[article.id].warnings
    assert any("摘要没有的数字" in warning and "123456" in warning for warning in warnings)


def test_parse_truncates_and_flags_long_fields():
    article = _article()
    payload = _payload(article, title_zh="很" * 200, digest_zh="长" * 500)
    annotations, _, _ = deepread.parse(payload, [article])
    item = annotations[article.id]
    assert len(item.title_zh) <= deepread.TITLE_MAX
    assert len(item.digest_zh) <= deepread.DIGEST_MAX
    assert any("超长" in warning for warning in item.warnings)


def test_parse_notes_missing_articles_and_shapes():
    article = _article()
    annotations, rejected, notes = deepread.parse({"papers": []}, [article])
    assert not annotations and not rejected
    assert any("未返回精读" in note for note in notes)

    annotations, rejected, notes = deepread.parse({"nope": []}, [article])
    assert not annotations
    assert any("papers" in note for note in notes)


def test_keywords_are_cleaned_and_deduplicated():
    article = _article()
    payload = _payload(
        article,
        keywords_zh="正选择, 正选择、群体基因组学； 适应 \n 正选择",
    )
    annotations, _, _ = deepread.parse(payload, [article])
    assert annotations[article.id].keywords_zh == ["正选择", "群体基因组学", "适应"]


def test_build_messages_carries_every_candidate_id():
    articles = [
        _article(doi="10.1/a", title="Alpha"),
        _article(doi="10.1/b", title="Beta"),
    ]
    messages = deepread.build_messages("群体遗传学", articles)
    joined = "\n".join(message["content"] for message in messages)
    for article in articles:
        assert article.id in joined
    assert "群体遗传学" in joined


def test_limit_articles_respects_max_items():
    articles = [_article(doi=f"10.1/{index}") for index in range(5)]
    with temp_dir() as tmp:
        config = make_config(tmp)
        config.raw.setdefault("deepread", {})["max_items"] = 2
        assert len(deepread.limit_articles(articles, config)) == 2
        config.raw["deepread"]["max_items"] = 0
        assert deepread.limit_articles(articles, config) == []


def _report(deepread_items=None):
    article = sample_article(
        doi="10.1/a",
        title="A study of positive selection",
        journal="Journal A",
    )
    result = Result(
        id=article.id,
        relevant=True,
        relevance=0.91,
        why="用群体基因组数据识别正选择位点",
        topic_tags=["正选择"],
        abstract_en=article.abstract_raw,
        abstract_mode="verbatim",
        summary_zh="一句话旧解读",
    )
    return ReportInput(
        run_id="2026-10-30",
        window=Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30)),
        scans=[],
        items=[article],
        results={article.id: result},
        validation=ValidationReport(results={article.id: result}),
        screening_label="llm-v1（deepseek-chat）",
        scan_at="2026-10-31T09:00:00+08:00",
        generated_at="2026-10-31T09:00:31+08:00",
        config=make_config(__import__("pathlib").Path("/tmp")),
        deepread=deepread_items or {},
        deepread_label="deepread-v1（deepseek-chat）" if deepread_items else "off",
    ), article


def test_render_uses_chinese_title_digest_and_keywords():
    annotation = deepread.DeepRead(
        id="ignored",
        title_zh="正选择研究的新证据",
        field_zh="进化生物学/群体遗传学",
        digest_zh="这是一段中文解读，按背景、方法与结论组织。",
        keywords_zh=["正选择", "基因组"],
    )
    data, article = _report()
    annotation.id = article.id
    data.deepread = {article.id: annotation}
    data.deepread_label = "deepread-v1（deepseek-chat）"
    markdown, _ = render_markdown(data)
    assert "### 1. 正选择研究的新证据" in markdown
    assert "- 研究领域：进化生物学/群体遗传学" in markdown
    assert "- 关键词：正选择 · 基因组" in markdown
    assert "这是一段中文解读" in markdown
    assert f"- 原文标题：{article.title}" in markdown
    assert "deepread: " in markdown and "deepread-v1" in markdown
    # the raw abstract stays for verification
    assert article.abstract_raw in markdown


def test_render_falls_back_to_english_without_deepread():
    data, article = _report()
    markdown, _ = render_markdown(data)
    assert f"### 1. {article.title}" in markdown
    assert "一句话解读：一句话旧解读" in markdown
    assert "deepread: \"off\"" in markdown


def test_off_cycle_status_is_labelled():
    assert STATUS_LABELS["off-cycle"] == "🛌 非出版日"


def test_deepread_file_roundtrip():
    item = deepread.DeepRead(
        id="sha1:abc",
        title_zh="标题",
        field_zh="领域",
        digest_zh="解读" * 30,
        keywords_zh=["a", "b"],
        warnings=["w"],
    )
    restored = deepread.DeepRead.from_dict(item.to_dict())
    assert restored == item


def test_render_runs_deepread_after_screening_and_persists_it():
    import datetime

    from helpers import RDF_FEED, make_context, write_journal

    from litradar import llm
    from litradar.config import load_journals
    from litradar.pipeline import fetch_all, render_report
    from litradar.state import read_json

    with temp_dir() as tmp:
        ctx = make_context(tmp, routes={"example.org/feed": RDF_FEED}, screening={"mode": "llm"})
        write_journal(tmp, {"name": "Example Genetics", "adapter": "rss",
                            "feed": "https://example.org/feed", "surface": "Latest"})
        window = Window(kind="day", start=datetime.date(2026, 10, 30),
                        end=datetime.date(2026, 10, 30))
        fetched = fetch_all(ctx, window, load_journals(ctx.config))
        article = fetched.new_items[0]

        digest = (
            "已有研究提示群体基因组数据能揭示自然选择的痕迹，但多数方法只适用于单一物种。"
            "作者比较了多个物种的基因组数据，识别出若干候选正选择位点，"
            "并指出这些位点可能与适应性性状相关。"
        )
        calls = []

        def fake_chat(config, messages, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"results": [{
                    "id": article.id, "relevant": True, "relevance": 0.9,
                    "why": "正选择扫描", "topic_tags": ["正选择"],
                    "abstract_en": article.abstract_raw, "abstract_mode": "verbatim",
                }]}
            return {"papers": [{
                "id": article.id,
                "title_zh": "正选择的群体基因组学研究",
                "field_zh": "进化生物学/群体遗传学",
                "digest_zh": digest,
                "keywords_zh": ["正选择", "基因组"],
            }]}

        original = llm.chat_json
        llm.chat_json = fake_chat
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original

        assert len(calls) == 2
        assert summary.screening.startswith("llm-v1")
        text = summary.report_path.read_text(encoding="utf-8")
        assert "正选择的群体基因组学研究" in text
        assert "进化生物学/群体遗传学" in text
        assert digest in text
        stored = read_json(ctx.paths.run_dir("2026-10-30") / "deepread.json")
        assert stored["papers"][0]["id"] == article.id
        assert stored["model"] == "deepseek-chat"

        # A second render must reuse the stored annotation instead of paying for it.
        calls.clear()
        original = llm.chat_json
        llm.chat_json = fake_chat
        try:
            render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        assert calls == []  # screen.json and deepread.json both reused


def test_render_survives_a_deepread_failure():
    import datetime

    from helpers import RDF_FEED, make_context, write_journal

    from litradar import llm
    from litradar.config import load_journals
    from litradar.pipeline import fetch_all, render_report

    with temp_dir() as tmp:
        ctx = make_context(tmp, routes={"example.org/feed": RDF_FEED}, screening={"mode": "llm"})
        write_journal(tmp, {"name": "Example Genetics", "adapter": "rss",
                            "feed": "https://example.org/feed", "surface": "Latest"})
        window = Window(kind="day", start=datetime.date(2026, 10, 30),
                        end=datetime.date(2026, 10, 30))
        fetched = fetch_all(ctx, window, load_journals(ctx.config))
        article = fetched.new_items[0]

        def flaky(config, messages, **kwargs):
            if "中文文献编辑" in messages[0]["content"]:
                raise llm.LlmError("模型超时")
            return {"results": [{
                "id": article.id, "relevant": True, "relevance": 0.9, "why": "相关",
                "abstract_en": article.abstract_raw, "abstract_mode": "verbatim",
            }]}

        original = llm.chat_json
        llm.chat_json = flaky
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original

        text = summary.report_path.read_text(encoding="utf-8")
        assert "精读跳过：模型超时" in text
        assert 'deepread: "off"' in text
        assert article.title in text  # the English layout is still there
