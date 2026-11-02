"""示例：custom 适配器。

把本文件复制到 <vault>/.litradar/journals/ 后由 `module: journals/cell_custom.py` 加载。
契约（见 docs/journals.md）：

* 不自己实现重试/限速/缓存，统一用 `fetcher`；
* 日期拿不到就 `None`，绝不从列表顺序或"今天"推断；
* `journal` 字段必须用配置里的规范名；
* 返回 `(list[Article], JournalScan)`，状态用 `ok` / `failed` 生成。
"""

from __future__ import annotations

import urllib.parse

from litradar.adapters.base import failed, ok
from litradar.model import Article, looks_non_research
from litradar.util import clean_abstract, parse_date

# 站点改版时只需要改这里。
ITEM = "li.article-item"
TITLE = "h3.article-title a"
LINK = "h3.article-title a::attr(href)"
DATE = "span.article-date"
ABSTRACT = "div.article-abstract"


def _pick(node, spec: str) -> str:
    css, _, modifier = spec.partition("::")
    element = node.select_one(css.strip())
    if element is None:
        return ""
    if modifier.startswith("attr("):
        return (element.get(modifier[5:-1]) or "").strip()
    return element.get_text(" ", strip=True)


def fetch(journal, fetcher, window):
    response = fetcher.get(journal["url"])
    if not response.ok:
        # 403/429 与"页面坏了"要分开报，前端才能给出不同提示。
        status = "blocked" if response.status in (401, 403, 429) else "error"
        return [], failed(journal, status, response.reason, http=response.status, url=journal["url"])

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "html.parser")
    nodes = soup.select(ITEM)
    if not nodes:
        return [], failed(
            journal, "error", "页面可读但 selectors.Item 未匹配（站点可能改版）",
            http=response.status, url=journal["url"],
        )

    items = []
    for node in nodes:
        title = _pick(node, TITLE)
        link = _pick(node, LINK)
        if not title or not link:
            continue
        online_date = parse_date(_pick(node, DATE))
        items.append(
            Article(
                title=title,
                journal=journal["name"],
                url=urllib.parse.urljoin(journal["url"], link),
                surface=journal.get("surface"),
                online_date=online_date,
                date_source="publisher" if online_date else "missing",
                abstract_raw=clean_abstract(_pick(node, ABSTRACT), max_chars=2000),
                content_type="other" if looks_non_research(title) else "research-article",
            )
        )
    return items, ok(journal, items, http=response.status, url=journal["url"])
