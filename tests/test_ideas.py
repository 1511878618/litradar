"""Research topics preserve personal work and expose the evidence actually used."""
import json
from unittest.mock import patch

from helpers import capture_stdout, make_context, temp_dir

from litradar import llm
from litradar.cli import main
from litradar.ideas import (
    FIELDS,
    SECTIONS,
    Ideas,
    analyze,
    normalize_paper,
    validate_extraction,
    validate_synthesis,
)
from litradar.state import atomic_write_json
from litradar.util import ConfigError, EngineError

ABSTRACT = "We compared two prediction methods in an external cohort. The findings require further validation."


def paper(doi="10.1/a", abstract=ABSTRACT):
    return {"title": "Prediction methods", "journal": "Example", "doi": doi, "abstract_raw": abstract}


def extraction(p):
    return {"id": p["id"], **{key: {"text": "比较预测方法，结果需要进一步验证。", "quote": ABSTRACT.split(". ")[0] + "."} for key in FIELDS}}


def model(messages):
    data = json.loads(messages[-1]["content"])
    if isinstance(data, list):
        return {"papers": [extraction(p) for p in data]}
    ids = [row["paper"]["id"] for row in data["evidence"]]
    return {key: [{"text": "这些研究比较了预测方法，尚需进一步验证。", "evidence": ids}] for key in SECTIONS}


def expect_error(fn, error=ConfigError):
    try:
        fn()
    except error:
        return
    raise AssertionError("Expected validation failure")


def setup(ctx):
    repo = Ideas(ctx.paths)
    idea = repo.create("预测方法", "哪些方法可以借鉴？")
    repo.add(idea["id"], [paper()])
    return repo, idea["id"]


def test_ideas_share_papers_but_keep_roles_and_notes_independent():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, first = setup(ctx)
        second = repo.create("第二个问题")["id"]
        repo.add(second, [paper("https://doi.org/10.1/A", "")])
        key = repo.detail(first)["papers"][0]["id"]
        repo.member(first, key, "方法", "我的批注")
        assert repo.get(second)["members"][key]["note"] == ""
        assert repo.detail(second)["papers"][0]["abstract_raw"] == ABSTRACT
        repo.member(first, key, remove=True)
        assert not repo.detail(first)["papers"] and len(repo.detail(second)["papers"]) == 1
        assert len(list((repo.root / "papers").glob("*.json"))) == 1
        ctx.close()


def test_ideas_validate_paths_fields_and_complete_import_before_mutating():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, key = setup(ctx)
        before = repo.get(key)
        expect_error(lambda: repo.get("../../secrets"))
        expect_error(lambda: repo.update(key, {"members": {}}))
        expect_error(lambda: repo.add(key, [paper("10.1/b"), {"title": ""}]))
        assert repo.get(key) == before
        broken = repo.root / "ideas" / (key + ".json")
        atomic_write_json(broken, {**before, "format": 99})
        expect_error(lambda: repo.get(key))
        ctx.close()


def test_extractions_require_verbatim_source_and_synthesis_references_selected_papers():
    p = normalize_paper(paper())
    row = extraction(p)
    assert validate_extraction(row, p)["id"] == p["id"]
    row["findings"]["quote"] = "The study proved a cure for everyone."
    expect_error(lambda: validate_extraction(row, p), EngineError)
    row["findings"] = {"text": "模型自行猜测", "quote": ""}
    expect_error(lambda: validate_extraction(row, p), EngineError)
    result = {key: [] for key in SECTIONS}
    result["findings"] = [{"text": "结论", "evidence": ["not-selected"]}]
    expect_error(lambda: validate_synthesis(result, {p["id"]}), EngineError)


def test_analysis_keeps_every_selected_paper_and_labels_abstract_coverage():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        repo.add(idea_id, [paper("10.1/no-abstract", "")])
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)):
            result = analyze(ctx, idea_id, None)
        assert result["coverage"] == {"selected": 2, "used": 1, "fulltext": 0}
        assert len(result["warnings"]) == 1 and len(result["papers"]) == 2
        text = (tmp / result["report_path"]).read_text()
        assert "[[#P1|P1]]" in text and "### P2" in text and "原文依据" in text
        assert "| [[#P1\\|P1]] |" in text, "Wiki aliases must not split Markdown table columns"
        assert ABSTRACT in text
        assert repo.analysis(idea_id, result["id"])["coverage"]["used"] == 1
        ctx.close()


def test_analysis_reuses_extractions_preserves_user_edits_and_saves_versions():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)) as call:
            first = analyze(ctx, idea_id, None)
            assert call.call_count == 2
        note = tmp / first["report_path"]
        note.write_text(note.read_text() + "\n我自己的后续批注\n")
        repo.update(idea_id, {"notes": "我想改用外部验证数据"})
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)) as call:
            second = analyze(ctx, idea_id, None)
            assert call.call_count == 1
        assert first["report_path"] != second["report_path"]
        assert "我自己的后续批注" in note.read_text()
        assert repo.get(idea_id)["notes"] == "我想改用外部验证数据"
        assert first["idea"]["notes"] == ""
        assert len(repo.get(idea_id)["analyses"]) == 2
        ctx.close()


def test_failed_synthesis_preserves_previous_versions_and_completed_extractions():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        def fail(cfg, messages, **kwargs):
            if isinstance(json.loads(messages[-1]["content"]), list):
                return model(messages)
            raise llm.LlmError("模拟服务不可用")
        with patch.object(llm, "chat_json", side_effect=fail):
            expect_error(lambda: analyze(ctx, idea_id, None), EngineError)
        assert repo.get(idea_id)["analyses"] == []
        assert len(list((repo.root / "extractions").glob("*.json"))) == 1
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)) as call:
            analyze(ctx, idea_id, None)
            assert call.call_count == 1
        ctx.close()


def test_cache_invalidates_when_source_changes_and_selection_never_silently_truncates():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)):
            analyze(ctx, idea_id, None)
        repo.add(idea_id, [paper(abstract=ABSTRACT + " Additional information.")])
        with patch.object(llm, "chat_json", side_effect=lambda cfg, messages, **kwargs: model(messages)) as call:
            analyze(ctx, idea_id, None)
            assert call.call_count == 2
        repo.add(idea_id, [paper(f"10.1/{n}") for n in range(51)])
        with patch.object(llm, "chat_json", side_effect=AssertionError("No network expected")):
            expect_error(lambda: analyze(ctx, idea_id, None))
            expect_error(lambda: analyze(ctx, idea_id, ["unknown"]))
        ctx.close()


def test_zotero_candidates_skip_notes_keep_keys_and_report_page_limit():
    from litradar.ideas import zotero_candidates
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        rows = [{"key": "ABCD1234", "data": {"title": "Study", "DOI": "10.1/a", "abstractNote": ABSTRACT}},
                {"key": "NOTE1234", "data": {"itemType": "note", "title": "Private note"}}]
        with patch("litradar.zotero.collection_items", return_value=rows):
            result = zotero_candidates(ctx.config, "COLL1234")
        assert len(result["papers"]) == 1 and not result["possibly_truncated"]
        assert result["papers"][0]["zotero_key"] == "ABCD1234"
        ctx.close()


def test_cli_ideas_roundtrip_and_invalid_json_are_explicit():
    with temp_dir() as tmp:
        assert capture_stdout(lambda: main(["init", "--vault", str(tmp)]))[0] == 0
        ctx = make_context(tmp)
        def cli(*args):
            code, out = capture_stdout(lambda: main(["ideas", *args, "--vault", str(tmp), "--json"]))
            return code, json.loads(out) if out.strip() else None
        code, idea = cli("create", "--name", "测试专题", "--question", "背景是什么？")
        assert code == 0
        code, result = cli("add", "--idea-id", idea["id"], "--article-json", json.dumps(paper()))
        assert code == 0 and len(result["papers"]) == 1
        code, result = cli("get", "--idea-id", idea["id"])
        assert result["idea"]["question"] == "背景是什么？"
        assert cli("add", "--idea-id", idea["id"], "--article-json", "broken")[0] == 2
        ctx.close()


def test_batch_add_undo_preserves_existing_and_edited_members():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        old = repo.detail(idea_id)
        existing_id = old['papers'][0]['id']
        result = repo.add(idea_id, [old['papers'][0], {'title': 'New paper A'}, {'title': 'New paper B'}])
        assert existing_id not in result['added'] and len(result['added']) == 2
        edited = next(iter(result['added']))
        repo.member(idea_id, edited, note='Later edit')
        receipt = repo.undo_add(idea_id, result['added'])
        assert receipt == {'removed': 1, 'preserved': 1}
        assert set(repo.get(idea_id)['members']) == {existing_id, edited}
        assert repo.get(idea_id)['members'][edited]['note'] == 'Later edit'
        ctx.close()
