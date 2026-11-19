"""Attach selected topic papers to a fresh llm-for-zotero conversation."""
from __future__ import annotations

import time
import uuid
from copy import deepcopy

from .idea_collaboration import item_key, matches_paper
from .ideas import Ideas
from .model import Article
from .state import VaultLock, atomic_write_json
from .util import ConfigError
from .zotero import ZoteroUnavailable, to_zotero_item
from .zotero_workflow import LocalAPI, _created_object, find_item

ENDPOINT = "/api/users/0/litradar/llm-context"


def check_papers(config, paths, idea_id, selected, api=None):
    repo = Ideas(paths)
    with repo.lock():
        detail = deepcopy(repo.detail(idea_id))
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 30
            or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)
            or set(selected) - set(detail["idea"]["members"])):
        raise ConfigError("请选择专题内 1–30 篇不重复的文献")
    api = api or LocalAPI(config)
    papers = {p["id"]: p for p in detail["papers"]}
    result = []
    for id in selected:
        paper = papers[id]
        article = Article(**{k: paper[k] for k in ("title", "journal", "doi", "url", "abstract_raw", "authors")})
        row = find_item(api, article)
        state, reason = "missing", "尚未入库；讨论时会先保存文献，需要在 Zotero 补充全文附件"
        if row:
            if not matches_paper(row, paper):
                raise ZoteroUnavailable("Zotero 文献匹配不一致，请刷新后重试")
            try:
                files = api.get("/api/users/0/litradar/files?key=" + item_key(row))
                if not isinstance(files, dict) or "contextAvailable" not in files:
                    raise ZoteroUnavailable("全文检查接口尚不可用，请升级 Zotero Bridge 0.3.1")
                state = "ready" if files["contextAvailable"] else "missing"
                reason = "本地全文可用" if state == "ready" else "默认全文附件缺失或未下载，请在 Zotero 补充"
            except ZoteroUnavailable as exc:
                state, reason = "unknown", "无法确认本地全文，请在 Zotero 核对：" + str(exc)
        result.append({"id": id, "title": paper["title"], "state": state, "reason": reason})
    return {"items": result}


def discuss(config, paths, idea_id, selected, api=None, progress=None):
    repo = Ideas(paths)
    with repo.lock():
        detail = deepcopy(repo.detail(idea_id))
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 30
            or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
        raise ConfigError("请选择 1–30 篇不重复的文献")
    if set(selected) - set(detail["idea"]["members"]):
        raise ConfigError("所选文献已不在专题中，请刷新后重选")
    papers = {paper["id"]: paper for paper in detail["papers"]}
    api = api or LocalAPI(config)
    def report(phase, completed=0):
        if progress:
            progress(phase, completed, len(selected))
    report("检查 Zotero 与讨论插件")
    # Preflight before importing anything. The endpoint inherits native Zotero auth.
    try:
        capability = api.get(ENDPOINT)
    except ZoteroUnavailable as exc:
        if "HTTP 404" in str(exc):
            raise ZoteroUnavailable("请安装 Litradar Zotero Bridge 0.3.0，并启用 llm-for-zotero 3.9.17") from exc
        raise
    if not isinstance(capability, dict) or capability.get("provider") != "llm-for-zotero" or capability.get("max_items") != 30:
        raise ZoteroUnavailable("上下文桥接版本不兼容，请更新 Litradar Zotero Bridge")
    # Serialize imports across topics so a shared paper isn't imported twice.
    with VaultLock(repo._path(".zotero-context.lock"), wait_seconds=3):
        keys = []
        for paper_id in selected:
            report("匹配并准备文献；首次写入可能需要在 Zotero 允许访问", len(keys))
            paper = papers[paper_id]
            with repo.lock():
                current = repo._load(repo._path("papers", paper_id + ".json"))
            key = current.get("zotero_key")
            row = None
            if key:
                item_key({"key": key})
                try:
                    row = api.get(f"/api/users/0/items/{key}")
                except ZoteroUnavailable as exc:
                    if "HTTP 404" not in str(exc):
                        raise
            article = Article(**{k: paper[k] for k in ("title", "journal", "doi", "url", "abstract_raw", "authors")})
            if row is None:
                row = find_item(api, article)
            if row is not None and not matches_paper(row, paper):
                raise ZoteroUnavailable(f"Zotero 条目与「{paper['title']}」不符，请核对后重新导入")
            if row is None:
                row = _created_object(api.write("/api/users/0/items", [to_zotero_item(article)], method="POST"), "入库")
            key = item_key(row)
            with repo.lock():
                path = repo._path("papers", paper_id + ".json")
                current = repo._load(path)
                current["zotero_key"] = key
                atomic_write_json(path, current)
            keys.append(key)
        if len(set(keys)) != len(keys):
            raise ZoteroUnavailable("所选文献对应重复的 Zotero 条目，请合并专题中的重复文献")
        request_id = uuid.uuid4().hex
        report("附加文献到新会话", len(keys))
        try:
            receipt = api.write(ENDPOINT, {"request_id": request_id, "keys": keys}, method="POST")
            deadline = time.monotonic() + 30
            while True:
                _validate_receipt(receipt, request_id, keys)
                if receipt["status"] != "opening":
                    break
                report("等待 Zotero 确认上下文", sum(row["status"] == "attached" for row in receipt["items"]))
                if time.monotonic() >= deadline:
                    raise ZoteroUnavailable("等待上下文确认超时")
                time.sleep(0.25)
                receipt = api.get(ENDPOINT + "?request_id=" + request_id)
        except ZoteroUnavailable as exc:
            raise ZoteroUnavailable(f"{exc}。请先在 Zotero 检查是否已打开会话，再决定重试；本次未自动重发。") from exc
        for paper_id, row in zip(selected, receipt["items"], strict=True):
            row["paper_id"] = paper_id
        return receipt


def _validate_receipt(receipt, request_id, keys):
    if (not isinstance(receipt, dict) or receipt.get("provider") != "llm-for-zotero"
            or receipt.get("request_id") != request_id or receipt.get("keys") != keys
            or receipt.get("status") not in ("opening", "attached", "partial", "unavailable", "failed")
            or not isinstance(receipt.get("items"), list)
            or any(not isinstance(row, dict) for row in receipt["items"])
            or [row.get("key") for row in receipt["items"]] != keys
            or any(row.get("status") not in ("pending", "attached", "skipped", "unconfirmed") for row in receipt["items"])):
        raise ZoteroUnavailable("上下文确认与本次选择不符，未能确认附加成功")
    states = [row["status"] for row in receipt["items"]]
    status = receipt["status"]
    if (status == "attached" and any(state != "attached" for state in states)
            or status == "partial" and ("attached" not in states or all(state == "attached" for state in states))
            or status in ("attached", "partial") and not receipt.get("conversation_id")
            or status in ("attached", "partial", "unavailable") and "pending" in states):
        raise ZoteroUnavailable("上下文确认状态不一致，请在 Zotero 核对文献")
