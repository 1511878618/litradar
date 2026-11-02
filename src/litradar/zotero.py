"""Zotero integration through the local API (no cloud account, no OAuth).

Read side feeds the embedding profile ("what am I interested in"), write side pushes
a day's articles into the library through the connector endpoint.  Zotero 7 must have
"Allow other applications on this computer to communicate with Zotero" enabled.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
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


def list_collections(config: Config) -> list[dict]:
    payload = _request(config, "/api/users/0/collections")
    if isinstance(payload, dict):
        payload = payload.get("collections") or []
    return [entry for entry in payload or [] if isinstance(entry, dict)]


def _collection_key(config: Config, name_or_key: str) -> str:
    needle = name_or_key.casefold()
    for collection in list_collections(config):
        data = collection.get("data") if isinstance(collection.get("data"), dict) else collection
        if str(data.get("key", "")).casefold() == needle or str(data.get("name", "")).casefold() == needle:
            return str(data.get("key"))
    raise ZoteroUnavailable(f"找不到 Zotero 收藏夹 {name_or_key!r}")


def collection_items(config: Config, name_or_key: str, limit: int = 100) -> list[dict]:
    key = _collection_key(config, name_or_key)
    query = urllib.parse.urlencode({"format": "json", "limit": limit})
    payload = _request(config, f"/api/users/0/collections/{key}/items?{query}")
    if isinstance(payload, dict):
        payload = payload.get("items") or []
    return [entry for entry in payload or [] if isinstance(entry, dict)]


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
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})
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
) -> dict[str, Any]:
    """Send articles to Zotero via the connector (Zotero shows a save dialog)."""
    pushed = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    batch: list[dict] = []
    for article in articles:
        if only_new and article.doi:
            try:
                if find_by_doi(config, article.doi):
                    skipped += 1
                    continue
            except ZoteroUnavailable:
                raise
        batch.append(to_zotero_item(article))
        if len(batch) >= 50:
            pushed += _send(config, batch, session_id, errors)
            batch = []
    if batch:
        pushed += _send(config, batch, session_id, errors)
    failed = len(errors)
    return {"pushed": pushed, "skipped": skipped, "failed": failed, "errors": errors}


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
