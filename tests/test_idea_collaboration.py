"""Handoffs preserve paper identity, recover partial writes and never invoke a model."""
import json
from copy import deepcopy
from unittest.mock import patch

from helpers import capture_stdout, make_context, temp_dir
from test_ideas import expect_error, paper, setup

from litradar import llm
from litradar.cli import main
from litradar.idea_collaboration import Collaboration, relocate_links
from litradar.zotero import ZoteroUnavailable


class API:
    def __init__(self):
        self.collections = []
        self.items = {}
        self.writes = []
        self.fail = False

    def get(self, path):
        if "/collections?" in path:
            return deepcopy(self.collections)
        if "/collections/" in path:
            return next(c for c in self.collections if c["key"] == path.rsplit("/", 1)[1])
        if "/items/top?" in path:
            return deepcopy(list(self.items.values()))
        key = path.rsplit("/", 1)[1]
        if key not in self.items:
            raise ZoteroUnavailable("Zotero HTTP 404：不存在")
        return deepcopy(self.items[key])

    def write(self, path, body, **kwargs):
        self.writes.append((path, deepcopy(body), kwargs))
        if path.endswith("/collections"):
            key = f"COLL{len(self.collections):04d}"
            row = {"key": key, "data": {**body[0], "key": key}}
            self.collections.append(row)
            return {"successful": {"0": row}}
        if self.fail:
            raise ZoteroUnavailable("模拟连接中断")
        if path.endswith("/items"):
            key = f"ITEM{len(self.items):04d}"
            row = {"key": key, "version": 1, "data": {**body[0], "key": key}}
            self.items[key] = row
            return {"successful": {"0": row}}
        key = path.rsplit("/", 1)[1]
        assert kwargs.get("version") == self.items[key]["version"]
        self.items[key]["data"].update(body)
        self.items[key]["version"] += 1


def create(ctx):
    repo, idea_id = setup(ctx)
    collab = Collaboration(ctx.paths)
    papers = repo.detail(idea_id)["papers"]
    return repo, collab, collab.create(idea_id, [p["id"] for p in papers])


def test_handoff_freezes_question_roles_and_all_selected_papers_without_llm():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        with patch.object(llm, "chat_json", side_effect=AssertionError("No LLM calls")):
            repo, collab, task = create(ctx)
            repo.update(task["idea_id"], {"question": "修改后的问题", "notes": "新的想法"})
            repo.member(task["idea_id"], task["papers"][0]["id"], "背景", "新的批注")
            ready = collab.sync(ctx.config, task["idea_id"], task["id"], API())
            assert ready["status"] == "ready" and ready["results"] == []
            assert "修改后的问题" not in ready["prompt"] and "新的批注" not in ready["prompt"]
            assert "ITEM0000" in ready["prompt"] and "全文/摘要/元数据/失败" in ready["prompt"]
            assert task["id"] in ready["prompt"] and str(tmp) in ready["prompt"]
        ctx.close()


def test_handoff_rejects_invalid_selection_before_any_write():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, collab, task = create(ctx)
        idea_id, key = task["idea_id"], task["papers"][0]["id"]
        for selected in ([], None, "bad", [key, key], ["unknown"], [key] * 51, [{}]):
            expect_error(lambda selected=selected: collab.create(idea_id, selected))
        repo.update(idea_id, {"question": ""})
        expect_error(lambda: collab.create(idea_id, [key]))
        assert len(collab.list(idea_id)) == 1
        ctx.close()


def test_sync_reuses_exact_zotero_key_and_preserves_all_original_collections():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        repo.add(idea_id, [{**paper(), "zotero_key": "ITEM0000"}])
        collab = Collaboration(ctx.paths)
        task = collab.create(idea_id, list(repo.get(idea_id)["members"]))
        api = API()
        api.items["ITEM0000"] = {"key": "ITEM0000", "version": 8,
                                 "data": {"title": "Prediction methods", "DOI": "10.1/a", "collections": ["PERSONAL"]}}
        result = collab.sync(ctx.config, idea_id, task["id"], api)
        assert len(api.items) == 1 and len(api.collections) == 3
        assert api.items["ITEM0000"]["data"]["collections"] == ["PERSONAL", result["collection_key"]]
        before = deepcopy(api.writes)
        note = tmp / task["request_path"]
        note.write_text(note.read_text() + "个人补充")
        collab.sync(ctx.config, idea_id, task["id"], api)
        assert api.writes == before and "个人补充" in note.read_text()
        ctx.close()


def test_partial_sync_keeps_task_and_collection_for_retry():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        _, collab, task = create(ctx)
        api = API()
        api.fail = True
        expect_error(lambda: collab.sync(ctx.config, task["idea_id"], task["id"], api), ZoteroUnavailable)
        partial = collab.get(task["idea_id"], task["id"])
        assert partial["status"] == "prepared" and partial["collection_key"]
        assert not (tmp / task["request_path"]).exists()
        api.fail = False
        ready = collab.sync(ctx.config, task["idea_id"], task["id"], api)
        assert ready["collection_key"] == partial["collection_key"]
        assert len(api.collections) == 3 and len(api.items) == 1
        ctx.close()


def test_sync_refuses_a_key_that_now_identifies_a_different_paper():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        repo.add(idea_id, [{**paper(), "zotero_key": "ITEM0000"}])
        collab = Collaboration(ctx.paths)
        task = collab.create(idea_id, list(repo.get(idea_id)["members"]))
        api = API()
        api.items["ITEM0000"] = {"key": "ITEM0000", "version": 1, "data": {"title": "Other", "DOI": "10.1/other"}}
        expect_error(lambda: collab.sync(ctx.config, idea_id, task["id"], api), ZoteroUnavailable)
        assert not any("/items" in path for path, _, _ in api.writes)
        assert collab.get(idea_id, task["id"])["status"] == "prepared"
        ctx.close()


def test_no_abstract_papers_are_included_and_radar_papers_get_zotero_identity():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, collab, first = create(ctx)
        repo.add(first["idea_id"], [paper("10.1/b", "")])
        task = collab.create(first["idea_id"], list(repo.get(first["idea_id"])["members"]))
        ready = collab.sync(ctx.config, task["idea_id"], task["id"], API())
        assert len(ready["zotero_items"]) == 2 and '"编号": "P2"' in ready["prompt"]
        assert ready["status"] == "ready", "Preparation must never claim analysis completion"
        ctx.close()


def test_receive_validates_task_marker_archives_versions_and_deduplicates_reimport():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        _, collab, task = create(ctx)
        collab.sync(ctx.config, task["idea_id"], task["id"], API())
        source = tmp / task["result_path"]
        expect_error(lambda: collab.receive(task["idea_id"], task["id"]))
        source.write_text(f'---\nlitradar_task_id: "{task["id"]}"\n---\n')
        expect_error(lambda: collab.receive(task["idea_id"], task["id"]))
        source.write_text("---\nlitradar_task_id: wrong-task\n---\n# 背景\n分析正文")
        expect_error(lambda: collab.receive(task["idea_id"], task["id"]))
        assert collab.get(task["idea_id"], task["id"])["status"] == "ready"
        source.write_text(f'---\nlitradar_task_id: "{task["id"]}"\n---\n# 背景\nP1 的主要方法。')
        result = collab.receive(task["idea_id"], task["id"])
        assert result["identity_verified"] and not result["coverage_verified"]
        assert collab.receive(task["idea_id"], task["id"])["id"] == result["id"]
        archive = tmp / result["report_path"]
        archive.write_text(archive.read_text() + "\n我的想法")
        source.write_text(source.read_text() + "\n第二版结果")
        second = collab.receive(task["idea_id"], task["id"])
        assert second["id"] != result["id"] and "我的想法" in archive.read_text()
        assert len(collab.get(task["idea_id"], task["id"])["results"]) == 2
        ctx.close()


def test_manual_receive_requires_vault_markdown_and_never_accepts_another_task():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        _, collab, task = create(ctx)
        collab.sync(ctx.config, task["idea_id"], task["id"], API())
        (tmp / "export.md").write_text("# 人工导出的结果\n需要检查结论和引用")
        for path in ("../secret.md", ".litradar/config.yaml", str(tmp / "export.md"), task["request_path"]):
            expect_error(lambda path=path: collab.receive(task["idea_id"], task["id"], path))
        # An explicit selection can associate exports without a task marker; that fact is recorded.
        result = collab.receive(task["idea_id"], task["id"], "export.md")
        assert not result["identity_verified"] and not result["coverage_verified"]
        (tmp / "export.md").write_text("---\nlitradar_task_id: another-task\n---\n结果")
        expect_error(lambda: collab.receive(task["idea_id"], task["id"], "export.md"))
        (tmp / "export.md").write_text("")
        expect_error(lambda: collab.receive(task["idea_id"], task["id"], "export.md"))
        ctx.close()


def test_symlinks_cannot_escape_vault_and_relative_assets_survive_archiving():
    with temp_dir() as tmp, temp_dir() as outside:
        ctx = make_context(tmp)
        collab = Collaboration(ctx.paths)
        (outside / "result.md").write_text("private")
        (tmp / "escape.md").symlink_to(outside / "result.md")
        expect_error(lambda: collab.note_path("escape.md"))
        content = "![图](assets/figure.png)\n[来源](https://example.com/paper)\n```md\n[x](example.md)\n```\n"
        moved = relocate_links(content, tmp / "Exports", tmp)
        assert "(/Exports/assets/figure.png)" in moved and "(https://example.com/paper)" in moved
        assert "[x](example.md)" in moved
        ctx.close()


def test_cli_handoff_detail_sync_and_receive_roundtrip_without_credentials():
    with temp_dir() as tmp:
        assert capture_stdout(lambda: main(["init", "--vault", str(tmp)]))[0] == 0
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        def cli(*args):
            code, out = capture_stdout(lambda: main(["ideas", *args, "--vault", str(tmp), "--json"]))
            assert code == 0, out
            return json.loads(out)
        task = cli("handoff", "--idea-id", idea_id, "--paper-ids-json", json.dumps(list(repo.get(idea_id)["members"])))
        with patch("litradar.idea_collaboration.LocalAPI", return_value=API()):
            ready = cli("sync", "--idea-id", idea_id, "--task-id", task["id"])
        assert ready["status"] == "ready"
        detail = cli("get", "--idea-id", idea_id)
        assert detail["tasks"][0]["id"] == task["id"]
        assert "prompt" not in detail["tasks"][0] and "abstract_raw" not in detail["tasks"][0]["papers"][0]
        assert "ITEM0000" in cli("task", "--idea-id", idea_id, "--task-id", task["id"])["prompt"]
        (tmp / "manual.md").write_text("# 研究背景\nP1 的摘要材料")
        result = cli("receive", "--idea-id", idea_id, "--task-id", task["id"], "--result-file", "manual.md")
        assert result["provider"] == "llm-for-zotero" and (tmp / result["report_path"]).is_file()
        ctx.close()
