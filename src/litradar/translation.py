"""Optional public title translation, with persistent success-only caching.

google-web is a public web endpoint, not the supported Cloud Translation API.
Only titles are sent; an outage must never prevent a report from being written.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from urllib.parse import urlencode

from .fetcher import Fetcher
from .state import atomic_write_json, read_json
from .util import collapse


def translate_titles(config, paths, articles, *, existing=None, fetcher=None,
                     commit=True, logger=None):
    translated = dict(existing or {})
    if not config.get("translation.enabled", False):
        return translated, ""
    provider = str(config.get("translation.provider", "google-web"))
    if provider != "google-web":
        return translated, f"未知标题翻译接口：{provider}；保留原题"
    cache_path = paths.cache_dir / "title-translations.json"
    try:
        cache = read_json(cache_path, default={})
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}
    client = fetcher or Fetcher({"timeout_s": 8, "retries": 0, "rate_limit_s": .2})
    deadline = time.monotonic() + max(1, float(config.get("translation.budget_s", 180)))
    limit = max(0, int(config.get("translation.max_per_run", 200)))
    failures = calls = missing = 0
    changed = False
    try:
        for article in articles:
            title = collapse(article.title)
            if article.id in translated or not title or re.search(r"[\u4e00-\u9fff]", title):
                continue
            key = hashlib.sha256((provider + "\0zh-CN\0" + title).encode()).hexdigest()
            cached = cache.get(key)
            if isinstance(cached, str) and re.search(r"[\u4e00-\u9fff]", cached):
                translated[article.id] = cached
                continue
            # Dry-run rendering only reads the cache; never sends titles or writes.
            if not commit or failures >= 3 or calls >= limit or time.monotonic() >= deadline:
                missing += 1
                continue
            calls += 1
            try:
                url = "https://translate.googleapis.com/translate_a/single?" + urlencode({
                    "client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": title,
                })
                response = client.get(url)
                if not response.ok:
                    raise ValueError(response.reason)
                payload = json.loads(response.text)
                value = collapse(html.unescape("".join(row[0] for row in payload[0] if row[0])))
                if not re.search(r"[\u4e00-\u9fff]", value) or len(value) > max(500, len(title) * 4):
                    raise ValueError("invalid translation")
                translated[article.id] = value
                cache[key] = value
                changed = True
                failures = 0
            except Exception:
                missing += 1
                failures += 1
            if logger and calls % 10 == 0:
                logger.info(f"标题翻译：已处理 {calls} 篇（译文缓存可复用）")
    finally:
        if fetcher is None:
            client.close()
    if changed and commit:
        try:
            atomic_write_json(cache_path, cache)
        except OSError:
            if logger:
                logger.info("标题翻译缓存未能写入；本次译文仍会显示")
    note = "中文标题为机器翻译，请以英文原题为准"
    if missing:
        note += f"；{missing} 篇未获得译文，保留原题（下次生成时重试）"
    return translated, note
