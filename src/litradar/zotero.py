"""Zotero integration through the local API (no cloud account, no OAuth).

Read side feeds the embedding profile ("what am I interested in"), write side pushes
a day's articles into the library through the connector endpoint.  Zotero 7 must have
"Allow other applications on this computer to communicate with Zotero" enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Config
from .model import Article
from .util import EngineError


class ZoteroUnavailable(EngineError):
    """Local Zotero is closed or the API is disabled."""


def _base(config: Config) -> str:
    return str(config.get("zotero.api", "http://127.0.0.1:23119") or "http://127.0.0.1:23119").rstrip("/")


def _request(config: Config, path: str, *, payload: dict | None = None, timeout: float = 10) -> Any:
    url = _base(config) + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ZoteroUnavailable(f"Zotero 返回 HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ZoteroUnavailable(
            "连不上 Zotero 本地 API（127.0.0.1:23119）。请打开 Zotero 7 并在设置里允许其他应用通信。"
        ) from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ZoteroUnavailable(f"Zotero 返回的不是 JSON：{raw[:200]}") from exc


def sqlite_path(config: Config) -> Path:
    """Where the local Zotero database lives (``zotero.sqlite`` override wins)."""
    explicit = str(config.get("zotero.sqlite", "") or os.environ.get("LITRADAR_ZOTERO_SQLITE", "")).strip()
    if explicit:
        return Path(os.path.expanduser(explicit))
    return Path.home() / "Zotero" / "zotero.sqlite"


@contextmanager
def _readonly_db(config: Config):
    """Read a *copy* of zotero.sqlite: Zotero holds the original exclusively."""
    path = sqlite_path(config)
    if not path.exists():
        raise ZoteroUnavailable(
            f"找不到 Zotero 数据库 {path}；可在 config.yaml 设置 zotero.sqlite，或打开 Zotero 的本地 API。"
        )
    temp = Path(tempfile.mkdtemp(prefix="litradar-zotero-"))
    connection = None
    try:
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{path}{suffix}")
            if source.exists():
                shutil.copy2(source, temp / f"zotero.sqlite{suffix}")
        connection = sqlite3.connect(str(temp / "zotero.sqlite"), timeout=5)
        connection.row_factory = sqlite3.Row
        yield connection
    except sqlite3.Error as exc:
        raise ZoteroUnavailable(f"读取 Zotero 数据库失败：{exc}") from exc
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(temp, ignore_errors=True)


_ITEM_COLUMNS = """
    select i.itemID,
           t.value as title,
           a.value as abstract,
           d.value as date,
           doi.value as doi
      from items i
      left join itemData dt on dt.itemID = i.itemID and dt.fieldID = 1
      left join itemDataValues t on t.valueID = dt.valueID
      left join itemData da on da.itemID = i.itemID and da.fieldID = 2
      left join itemDataValues a on a.valueID = da.valueID
      left join itemData dd on dd.itemID = i.itemID and dd.fieldID = 6
      left join itemDataValues d on d.valueID = dd.valueID
      left join itemData ddoi on ddoi.itemID = i.itemID and ddoi.fieldID = 8
      left join itemDataValues doi on doi.valueID = ddoi.valueID
     where i.itemID not in (select itemID from itemAttachments)
       and i.itemID not in (select itemID from itemNotes)
       and i.itemID not in (select itemID from deletedItems)
"""


def collection_tree(entries: list[dict]) -> list[dict]:
    """Normalize API/SQLite collections to stable keys and unambiguous paths."""
    rows = {}
    for entry in entries:
        data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
        key = str(entry.get("key") or data.get("key") or "")
        if not key:
            continue
        parent = data.get("parentCollection") or data.get("parent") or ""
        rows[key] = {"key": key, "name": str(data.get("name") or ""), "parent": str(parent)}

    def path_of(key: str, visited: set[str]) -> list[str]:
        row = rows[key]
        parent = row["parent"]
        if parent not in rows or parent in visited or parent == key:
            return [row["name"]]
        return path_of(parent, visited | {key}) + [row["name"]]

    for key, row in rows.items():
        parts = path_of(key, set())
        row["path"] = " / ".join(parts)
        row["depth"] = len(parts) - 1
    return sorted(rows.values(), key=lambda row: (row["path"].casefold(), row["key"]))


def _sqlite_collections(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "select c.collectionID, c.key, c.collectionName, p.key as parentKey "
        "from collections c left join collections p on p.collectionID = c.parentCollectionID "
        "where c.collectionID not in (select collectionID from deletedCollections)"
    ).fetchall()
    return collection_tree(
        [{"key": row["key"], "name": row["collectionName"], "parent": row["parentKey"]} for row in rows]
    )


def _sqlite_collection_id(connection: sqlite3.Connection, key: str) -> int:
    row = connection.execute("select collectionID from collections where key = ?", (key,)).fetchone()
    if row is None and str(key).isdigit():
        row = connection.execute(
            "select collectionID from collections where collectionID = ?", (int(key),)
        ).fetchone()
    if row is None:
        raise ZoteroUnavailable(f"找不到 Zotero 收藏夹 key {key!r}")
    return int(row[0])


def _sqlite_items(connection: sqlite3.Connection, collection_key: str | None, limit: int) -> list[dict]:
    sql = _ITEM_COLUMNS
    params: list[Any] = []
    if collection_key:
        sql += " and i.itemID in (select itemID from collectionItems where collectionID = ?)"
        params.append(_sqlite_collection_id(connection, collection_key))
    sql += " order by i.dateAdded desc limit ?"
    params.append(int(limit))
    items = []
    for row in connection.execute(sql, params).fetchall():
        items.append(
            {
                "data": {
                    "title": row["title"] or "",
                    "abstractNote": row["abstract"] or "",
                    "date": row["date"] or "",
                    "DOI": row["doi"] or "",
                }
            }
        )
    return items


def _sqlite_tag_counts(connection: sqlite3.Connection, collection_key: str | None) -> dict[str, int]:
    sql = """
        select tg.name as name, count(*) as n
          from itemTags it
          join tags tg on tg.tagID = it.tagID
         where it.itemID not in (select itemID from itemAttachments)
           and it.itemID not in (select itemID from itemNotes)
    """
    params: list[Any] = []
    if collection_key:
        sql += " and it.itemID in (select itemID from collectionItems where collectionID = ?)"
        params.append(_sqlite_collection_id(connection, collection_key))
    sql += " group by tg.tagID order by n desc limit 80"
    return {row["name"]: int(row["n"]) for row in connection.execute(sql, params).fetchall()}


def _api_available(config: Config) -> bool:
    try:
        _request(config, "/api/users/0/collections", timeout=3)
        return True
    except ZoteroUnavailable:
        return False


def list_collections(config: Config) -> list[dict]:
    try:
        payload = _request(config, "/api/users/0/collections")
    except ZoteroUnavailable:
        with _readonly_db(config) as connection:
            return _sqlite_collections(connection)
    if isinstance(payload, dict):
        payload = payload.get("collections") or []
    return collection_tree([entry for entry in payload or [] if isinstance(entry, dict)])


def _resolve_collection(collections: list[dict], name_or_key: str) -> str:
    needle = name_or_key.casefold()
    for row in collections:
        if row["key"].casefold() == needle:
            return row["key"]
    matches = [row for row in collections if row.get("path", "").casefold() == needle]
    if not matches:
        matches = [row for row in collections if row["name"].casefold() == needle]
    if len(matches) == 1:
        return matches[0]["key"]
    if matches:
        raise ZoteroUnavailable(f"收藏夹名 {name_or_key!r} 不唯一，请选择完整路径或 key")
    raise ZoteroUnavailable(f"找不到 Zotero 收藏夹 {name_or_key!r}")


def _collection_key(config: Config, name_or_key: str) -> str:
    return _resolve_collection(list_collections(config), name_or_key)


def collection_items(config: Config, name_or_key: str, limit: int = 100) -> list[dict]:
    key = _collection_key(config, name_or_key)
    query = urllib.parse.urlencode({"format": "json", "limit": limit})
    try:
        payload = _request(config, f"/api/users/0/collections/{key}/items?{query}")
    except ZoteroUnavailable:
        with _readonly_db(config) as connection:
            return _sqlite_items(connection, key, limit)
    if isinstance(payload, dict):
        payload = payload.get("items") or []
    return [entry for entry in payload or [] if isinstance(entry, dict)]


def profile_data(
    config: Config, collections: list[str] | None = None, *, limit: int = 300
) -> tuple[list[dict], dict[str, int], str]:
    """(items, tag counts, source) for building a research profile.

    Prefers the local API; falls back to a read-only copy of zotero.sqlite, so the
    profile still works when "allow other applications" is off.
    """
    names = [name for name in (collections or []) if name]
    if _api_available(config):
        items: list[dict] = []
        tags: dict[str, int] = {}
        if names:
            entries = []
            for name in names:
                entries.extend(collection_items(config, name, limit=limit))
        else:
            query = urllib.parse.urlencode({"format": "json", "limit": limit})
            payload = _request(config, f"/api/users/0/items?{query}")
            if isinstance(payload, dict):
                payload = payload.get("items") or []
            entries = [entry for entry in payload or [] if isinstance(entry, dict)]
        for entry in entries:
            data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
            items.append(data)
            for tag in data.get("tags") or []:
                if isinstance(tag, dict) and tag.get("tag"):
                    tags[str(tag["tag"])] = tags.get(str(tag["tag"]), 0) + 1
        return items, tags, "local-api"

    with _readonly_db(config) as connection:
        all_collections = _sqlite_collections(connection)
        keys: list[str] = []
        for name in names:
            keys.append(_resolve_collection(all_collections, name))
        merged: dict[str, dict] = {}
        tags: dict[str, int] = {}
        for key in keys or [None]:
            for entry in _sqlite_items(connection, key, limit):
                merged.setdefault(str(entry["data"].get("DOI") or entry["data"].get("title")), entry)
            for name, count in _sqlite_tag_counts(connection, key).items():
                tags[name] = tags.get(name, 0) + count
        source = "sqlite" if not names else "sqlite:" + ",".join(names)
        return [entry["data"] for entry in merged.values()], tags, source


def collection_texts(config: Config, name_or_key: str, limit: int = 200) -> list[str]:
    texts: list[str] = []
    for entry in collection_items(config, name_or_key, limit=limit):
        data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
        title = str(data.get("title") or "").strip()
        abstract = str(data.get("abstractNote") or "").strip()
        if title:
            texts.append(f"{title}. {abstract}".strip())
    return texts


def find_by_doi(config: Config, doi: str) -> dict | None:
    if not doi:
        return None
    query = urllib.parse.urlencode({"q": doi, "qmode": "everything", "format": "json", "limit": 5})
    payload = _request(config, f"/api/users/0/items?{query}")
    if isinstance(payload, dict):
        payload = payload.get("items") or []
    for entry in payload or []:
        data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
        if str(data.get("DOI") or "").casefold() == doi.casefold():
            return entry
    return None


def _creators(article: Article) -> list[dict]:
    creators: list[dict] = []
    for full_name in article.authors:
        parts = full_name.split()
        if len(parts) >= 2:
            creators.append(
                {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}
            )
        elif parts:
            creators.append({"creatorType": "author", "name": parts[0]})
    return creators


def to_zotero_item(article: Article) -> dict:
    item = {
        "itemType": "journalArticle",
        "title": article.title,
        "DOI": article.doi or "",
        "url": article.url,
        "publicationTitle": article.journal,
        "date": article.online_date.isoformat() if article.online_date else "",
        "abstractNote": article.abstract_raw or "",
        "creators": _creators(article),
        "extra": "litradar",
    }
    return {key: value for key, value in item.items() if value not in ("", [], None)}


def push_items(
    config: Config,
    articles: list[Article],
    *,
    session_id: str,
    only_new: bool = True,
    collection_date: str | None = None,
) -> dict[str, Any]:
    """Archive the batch in a dedicated daily collection through Zotero 10's API."""
    from .zotero_workflow import LocalAPI, _created_object, daily_collection, ensure_item, find_item

    pushed = skipped = archived = 0
    errors: list[str] = []
    if not articles:
        return {"pushed": 0, "skipped": 0, "failed": 0, "archived": 0, "errors": []}
    api = LocalAPI(config)
    destination = daily_collection(api, collection_date)
    for article in articles:
        try:
            if only_new:
                existing = find_item(api, article)
                ensure_item(api, article, destination)
                if existing:
                    skipped += 1
                else:
                    pushed += 1
            else:
                item = to_zotero_item(article)
                item["collections"] = [destination]
                _created_object(api.write("/api/users/0/items", [item], method="POST"), "入库")
                pushed += 1
            archived += 1
        except ZoteroUnavailable as exc:
            errors.append(str(exc))
    return {
        "pushed": pushed,
        "skipped": skipped,
        "failed": len(errors),
        "archived": archived,
        "errors": errors,
    }


def _send(config: Config, batch: list[dict], session_id: str, errors: list[str]) -> int:
    payload = {
        "sessionID": session_id,
        "uri": "https://github.com/wanglab/litradar",
        "items": batch,
    }
    try:
        _request(config, "/connector/saveItems", payload=payload, timeout=20)
        return len(batch)
    except ZoteroUnavailable as exc:
        errors.append(str(exc))
        return 0
