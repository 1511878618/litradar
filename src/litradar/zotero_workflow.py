"""Zotero-owned reading states, item creation and PDF links (native local API 10+)."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .model import Article, normalize_doi
from .zotero import ZoteroUnavailable, _base, to_zotero_item

TODO_TAG = "litradar/待读"
READ_TAG = "litradar/已读"


class LocalAPI:
    def __init__(self, config: Any):
        self.base = _base(config)
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme != "http":
            raise ZoteroUnavailable("Zotero 阅读联动仅允许本机 HTTP API")
        self.config = config
        self.server_id = ""
        # Keep authorization out of the synced vault and never print the key.
        self.auth_path = Path.home() / ".config" / "litradar" / "zotero-local-auth.json"

    def request(self, path: str, *, method: str = "GET", body: Any = None,
                headers: dict | None = None, timeout: float = 15) -> tuple[Any, dict]:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                meta = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 403 and "not enabled" in detail:
                raise ZoteroUnavailable("Zotero 本地 API 未启用：请在 Zotero 设置 → 高级，勾选“允许此计算机上的其他应用程序与 Zotero 通信”") from exc
            if exc.code == 429 and "authorization" in detail.lower():
                raise ZoteroUnavailable("Zotero 授权请求过于频繁，请稍后重试；如需连续操作，可在授权弹窗选择“始终允许”") from exc
            raise ZoteroUnavailable(f"Zotero HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ZoteroUnavailable("无法连接 Zotero；请打开 Zotero 并启用本地 API") from exc
        if path == "/api/":
            return {}, meta
        if not raw.strip():
            return None, meta
        try:
            return json.loads(raw), meta
        except json.JSONDecodeError as exc:
            raise ZoteroUnavailable("Zotero 返回了无效 JSON") from exc

    def get(self, path: str) -> Any:
        return self.request(path)[0]

    def write(self, path: str, body: Any, *, method: str = "PATCH", version: Any = None) -> Any:
        _, meta = self.request("/api/")
        self.server_id = meta.get("zotero-server-id", "")
        if not self.server_id:
            raise ZoteroUnavailable("阅读状态写入需要 Zotero 10 或更高版本")
        cached = {}
        try:
            cached = json.loads(self.auth_path.read_text())
        except (OSError, ValueError):
            pass
        key = cached.get("key") if cached.get("server_id") == self.server_id else None
        for attempt in range(2):
            if not key:
                auth, _ = self.request("/api/local/authorize", method="POST", body={"appName": "Litradar 文献雷达"},
                    headers={"Zotero-Server-ID": self.server_id}, timeout=110)
                key = auth.get("key") if isinstance(auth, dict) else None
                if not key:
                    raise ZoteroUnavailable("未获得 Zotero 写入授权")
                if auth.get("remember"):
                    self.auth_path.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(self.auth_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as file:
                        json.dump({"key": key, "server_id": self.server_id}, file)
            headers = {"Zotero-Server-ID": self.server_id, "Zotero-API-Key": key}
            if version is not None:
                headers["If-Unmodified-Since-Version"] = str(version)
            try:
                return self.request(path, method=method, body=body, headers=headers)[0]
            except ZoteroUnavailable as exc:
                if "HTTP 401" in str(exc) and attempt == 0:
                    key = None
                    continue
                if "HTTP 412" in str(exc):
                    raise ZoteroUnavailable("Zotero 条目已被修改，请刷新后重试；未覆盖其他修改") from exc
                raise
        raise ZoteroUnavailable("Zotero 授权失效，请重试")


def _data(entry: dict) -> dict:
    return entry.get("data") if isinstance(entry.get("data"), dict) else entry


def find_item(api: LocalAPI, article: Article) -> dict | None:
    query = normalize_doi(article.doi) or article.title
    if not query:
        raise ZoteroUnavailable("论文缺少 DOI 和标题，无法匹配 Zotero")
    rows = api.get("/api/users/0/items/top?" + urllib.parse.urlencode({"q": query, "qmode": "everything", "limit": 100})) or []
    matches = []
    for row in rows:
        data = _data(row)
        if normalize_doi(article.doi):
            match = normalize_doi(data.get("DOI")) == normalize_doi(article.doi)
        else:
            match = str(data.get("title", "")).strip().casefold() == article.title.strip().casefold()
        if match:
            matches.append(row)
    if len(matches) > 1:
        raise ZoteroUnavailable("Zotero 中有多条匹配文献，请先在 Zotero 合并重复条目")
    return matches[0] if matches else None


DAILY_COLLECTION_ROOT = "Litradar 文献雷达"


def _created_object(result: Any, label: str) -> dict:
    if not isinstance(result, dict) or result.get("failed"):
        raise ZoteroUnavailable(f"Zotero {label}失败：" + str(result.get("failed") if isinstance(result, dict) else "返回结构异常"))
    successful = result.get("successful") or {}
    if not successful:
        raise ZoteroUnavailable(f"Zotero 未确认{label}，已停止保存以避免条目未分类")
    return next(iter(successful.values()))


def daily_collection(api: LocalAPI, collection_date: str | None = None) -> str:
    """Resolve/create a dedicated root and day collection before saving an item."""
    day = collection_date or datetime.now(ZoneInfo(api.config.tz)).date().isoformat()
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(day)
    except ValueError as exc:
        raise ZoteroUnavailable("每日收藏夹日期必须是 YYYY-MM-DD") from exc
    collections = api.get("/api/users/0/collections") or []
    def resolve(name: str, parent: str) -> str:
        matches = [row for row in collections if _data(row).get("name") == name and
                   (_data(row).get("parentCollection") or "") == parent]
        if len(matches) > 1:
            raise ZoteroUnavailable(f"Zotero 中有多个同名收藏夹 {name!r}，请在 Litradar 设置中明确选择保存位置")
        if matches:
            return str(matches[0].get("key") or _data(matches[0])["key"])
        result = api.write("/api/users/0/collections", [{"name": name, "parentCollection": parent or False}], method="POST")
        row = _created_object(result, "创建收藏夹")
        collections.append(row)
        return str(row.get("key") or _data(row)["key"])
    parent = resolve(str(api.config.get("zotero.daily_collection_root", DAILY_COLLECTION_ROOT)), "")
    return resolve(day, parent)


def ensure_item(api: LocalAPI, article: Article, collection: str = "", collection_date: str | None = None) -> dict:
    row = find_item(api, article)
    destination = collection or daily_collection(api, collection_date)
    if collection:
        api.get(f"/api/users/0/collections/{urllib.parse.quote(collection, safe='')}")
    if row:
        data = _data(row)
        existing = list(data.get("collections") or [])
        if destination not in existing:
            key = str(row.get("key") or data["key"])
            api.write(f"/api/users/0/items/{key}", {"collections": existing + [destination]}, version=row.get("version", data.get("version")))
            return api.get(f"/api/users/0/items/{key}")
        return row
    item = to_zotero_item(article)
    item["collections"] = [destination]
    return _created_object(api.write("/api/users/0/items", [item], method="POST"), "入库")


def item_view(api: LocalAPI, row: dict, *, with_pdf: bool = False) -> dict:
    data = _data(row)
    key = str(row.get("key") or data.get("key") or "")
    status = reading_status(data.get("extra", ""))
    pdfs = []
    if with_pdf:
        children = api.get(f"/api/users/0/items/{key}/children") or []
        for child in children:
            child_data = _data(child)
            if child_data.get("contentType") == "application/pdf":
                pdf_key = str(child.get("key") or child_data.get("key") or "")
                if pdf_key and child_data.get("linkMode") != "linked_url":
                    pdfs.append({"key": pdf_key, "url": f"zotero://open-pdf/library/items/{pdf_key}"})
    article = Article(title=str(data.get("title") or ""), journal=str(data.get("publicationTitle") or ""),
        doi=data.get("DOI"), url=str(data.get("url") or ""), abstract_raw=data.get("abstractNote"),
        authors=[" ".join(filter(None, [creator.get("firstName"), creator.get("lastName")])) or str(creator.get("name") or "")
                 for creator in data.get("creators") or [] if creator.get("creatorType") == "author"])
    return {"key": key, "status": status, "article": article.to_dict(),
            "updatedAt": data.get("dateModified", ""), "selectUrl": f"zotero://select/library/items/{key}", "pdfs": pdfs}


READING_ENDPOINT = "/api/users/0/litradar/reading"
LEGACY_TAGS = (TODO_TAG, "litradar/待读", READ_TAG)


def reading_status(extra: str) -> str:
    values = re.findall(r"^Read_Status:(.+)$", str(extra or ""), flags=re.I | re.M)
    return values[0].strip() if len(values) == 1 else ""


def reading_list_payload(api: LocalAPI, *, metadata_only: bool = False) -> dict:
    try:
        payload = api.get(READING_ENDPOINT + ("?metadata=1" if metadata_only else ""))
    except ZoteroUnavailable as exc:
        if "HTTP 404" in str(exc):
            raise ZoteroUnavailable("请将 Zotero 中的 Litradar PDF Bridge 升级为 Zotero Bridge 0.2.0，并启用 Reading List") from exc
        raise
    if not isinstance(payload, dict) or payload.get("provider") != "reading-list":
        raise ZoteroUnavailable("Reading List 桥接接口返回格式异常，请更新 Zotero Bridge")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list) or any(not isinstance(row, dict) or not isinstance(row.get("value"), str) or not row["value"] for row in statuses):
        raise ZoteroUnavailable("Reading List 状态配置异常")
    return {"provider": "reading-list", "statuses": statuses,
            "legacyCount": int(payload.get("legacyCount") or 0),
            "items": [item_view(api, row) for row in payload.get("entries") or []]}


def reading_list(api: LocalAPI) -> list[dict]:
    return reading_list_payload(api)["items"]


def _resolve_status(status: str, options: list[dict]) -> str:
    names = [row["value"] for row in options]
    if status in names:
        return status
    if status in ("none", ""):
        return ""
    aliases = {"todo": ("To Read", "待读", "待读"), "read": ("Read", "已读")}
    matches = [name for name in names if name in aliases.get(status, ())]
    if len(matches) == 1:
        return matches[0]
    raise ZoteroUnavailable("Reading List 中没有对应状态；请刷新并选择真实状态，自定义状态的迁移需手动选择映射")


def set_status(api: LocalAPI, article: Article, status: str, collection: str = "", collection_date: str | None = None) -> dict:
    payload = reading_list_payload(api, metadata_only=True)  # Check dependency before creating an item.
    resolved = _resolve_status(status, payload["statuses"])
    row = find_item(api, article)
    if row is None:
        if not resolved:
            return {"status": "", "article": article.to_dict(), "key": "", "pdfs": []}
        row = ensure_item(api, article, collection, collection_date)
    elif resolved and collection_date:
        row = ensure_item(api, article, collection, collection_date)
    data = _data(row)
    key = str(row.get("key") or data["key"])
    result = api.write(READING_ENDPOINT,
        {"key": key, "status": resolved, "clear": not resolved, "version": row.get("version", data.get("version"))}, method="POST")
    if not isinstance(result, dict) or result.get("key") != key:
        raise ZoteroUnavailable("Reading List 未确认状态更新，请刷新后核对")
    return item_view(api, result)


def migrate_legacy(api: LocalAPI, todo_status: str, read_status: str) -> dict:
    payload = reading_list_payload(api)
    todo = _resolve_status(todo_status, payload["statuses"])
    read = _resolve_status(read_status, payload["statuses"])
    if not todo or not read or todo == read:
        raise ZoteroUnavailable("请选择不同的待读、已读目标状态")
    rows = {}
    for tag in LEGACY_TAGS:
        query = urllib.parse.urlencode({"tag": tag, "format": "json"})
        for row in api.get("/api/users/0/items/top?" + query) or []:
            rows[row.get("key") or _data(row).get("key")] = row
    batch = []
    for key, row in rows.items():
        data = _data(row)
        tags = {tag.get("tag") for tag in data.get("tags") or [] if isinstance(tag, dict)}
        if not tags.intersection(LEGACY_TAGS):
            continue
        batch.append({"key": key, "status": read if READ_TAG in tags else todo,
                      "version": row.get("version", data.get("version"))})
    result = {"migrated": 0, "preserved": 0, "failed": []}
    for start in range(0, len(batch), 500):
        current = api.write(READING_ENDPOINT, {"action": "migrate", "items": batch[start:start + 500]}, method="POST")
        result["migrated"] += current["migrated"]
        result["preserved"] += current["preserved"]
        result["failed"].extend(current["failed"])
    return result


def lookup(api: LocalAPI, article: Article) -> dict:
    row = find_item(api, article)
    return item_view(api, row, with_pdf=True) if row else {"key": "", "status": "none", "pdfs": []}
