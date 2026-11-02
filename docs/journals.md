# 新增一本期刊

目标：**≤ 10 分钟，只改 YAML，不改引擎代码。**

## 0. 先确认能不能做

浏览器打开该刊的 ASAP / Early View / Advance Access / Articles in Press 页面，
**用无痕窗口**确认普通读者能看到列表和日期。需要登录才能看到 = 抓不到。

## 1. 按可靠性选适配器

| 顺序 | adapter | 判断方法 | 可靠性 |
|---|---|---|---|
| 1 | `rss` | 页脚找 RSS 图标，或试 `https://<站>/<刊>.rss` | ★★★ |
| 2 | `html` | 页面源码里条目容器有稳定的 class | ★★ |
| 3 | `crossref` | 有 ISSN 就能用，但只有元数据 | ★★（摘要/日期可能缺） |
| 4 | `biorxiv` | bioRxiv / medRxiv（官方 JSON API） | ★★★（量大，且是预印本） |
| 5 | `manual` | 人工粘贴 | ★ |
| 6 | `custom` | 写 20 行 Python | 取决于你 |

## 2. RSS（首选）

```yaml
name: Nature Genetics
adapter: rss
feed: https://www.nature.com/ng.rss
surface: Latest Research
issn: "1061-4036"
```

```bash
litradar journals test "Nature Genetics"
```

必须看到 `title / online_date / doi` 三样。常见情况：

- **日期全空**：feed 里没有 `pubDate`/`dc:date`/`prism:publicationDate`，
  且描述里也没有 `Published online:` 字样 → 换 `html` 或 `crossref`；
- **DOI 全空**：DOI 只在链接里，且不是 `doi.org/` 形式 → 仍可接受，靠标题+作者去重；
- **摘要是样板文**：`content:encoded` 里混了「期刊名, Published online: …; doi:…」，
  引擎会自动剥离；如果还有残留，用 `abstract_mode` 兜底（渲染时以 store 里的
  `abstract_raw` 为准）。

## 3. HTML 栏目页

```yaml
name: The Plant Cell
adapter: html
url: https://academic.oup.com/plcell/advance-articles
base_url: https://academic.oup.com
surface: Advance Articles
selectors:
  item: "li.al-article-item"       # 每条文章的容器
  title: "h4 a"                    # 默认取文本
  link: "h4 a::attr(href)"         # ::attr(name) 取属性，也可 ::text
  doi: "span.al-doi"
  date: "span.al-date"
  abstract: "div.al-abstract"      # 可选
content_type: research-article
fallback: [crossref]
issn: "1040-4651"
```

调试步骤：

1. 浏览器 → 右键条目 → 检查 → 找到一个能覆盖**所有**条目的容器选择器；
2. 确认容器里能唯一定位标题、链接、日期；
3. `litradar journals test "The Plant Cell" --json` 看 `scan.status` 与前三条的原始字段；
4. 报 `页面可读但 selectors.item 未匹配` = 选择器错了（不是被封）；
5. 拿不到日期就写 `fallback: [crossref]`，让引擎用元数据补，并在报告里标「仅元数据」。

## 4. Crossref 兜底

```yaml
name: Molecular Biology and Evolution
adapter: crossref
issn: "0737-4038"
surface: Advance Access
mailto: you@example.com     # 走 Crossref 的 polite pool，强烈建议填
```

- 状态会标成 `metadata-only`，覆盖表显示 ⚠ 仅元数据；
- 日期来自 Crossref 的 `published-online`，报告里会加「（元数据日期，未在官网核实）」；
- 摘要通常为空，正文里会写 `No abstract supplied by the journal`（不补写、不翻译）。

## 5. 预印本服务器：bioRxiv / medRxiv

两者共用 `biorxiv` 适配器，走官方 JSON API（按日期区间，不用 RSS）：

```yaml
name: bioRxiv
adapter: biorxiv
server: biorxiv          # biorxiv | medrxiv
surface: Preprints
# 可选：只收这些分类（大小写、连字符都无所谓）
categories:
  - genomics
  - genetics
  - evolutionary biology
# 可选：单刊上限，触顶会在覆盖表注明
max_items: 300
```

要点：

- **日期来自预印本服务器自己的 `date` 字段**（`date_source: publisher`），接口
  支持 `{from}/{to}` 区间，翻页每次 30 条，适配器会翻到 `total` 为止；
- **正文会标注「预印本·未经同行评审」**，置顶卡片也有独立一行 —— 不要把预印本
  当成已发表论文引用；
- 量很大：bioRxiv 常见 80–150 条/天、medRxiv 30–50 条/天。可选三种收窄方式：
  `categories`（最推荐）、`max_items`（触顶会写进报告，不静默）、
  调大 `screening.max_items` 让更多条目参与置顶；
- 用了 `categories` 或触顶时，覆盖表下方会有一行「说明」，写清本轮口径；
- **修订版（v2/v3）也算「当天上线」**：接口的 `date` 是该版本的上线日，
  所以旧预印本的新版本会进入当天窗口，报告里写作「预印本发布日期：…（v2）」；
- 预印本与正式发表版是不同 DOI，引擎不会跨版本合并 —— 同一工作可能先以预印本、
  后以期刊论文各出现一次。

## 6. 自定义代码（逃生舱）

```yaml
name: Cell
adapter: custom
module: journals/cell_custom.py
surface: Articles in Press
url: https://www.cell.com/cell/newarticles
```

`module` 相对于 `<vault>/.litradar/`。接口：

```python
from litradar.model import Article
from litradar.adapters.base import failed, ok

def fetch(journal, fetcher, window):
    resp = fetcher.get(journal["url"])       # 重试/限速/缓存/UA 都由 fetcher 负责
    if not resp.ok:
        return [], failed(journal, "blocked", resp.reason, http=resp.status, url=journal["url"])
    items = []
    for node in parse_whatever(resp.text):
        items.append(Article(
            title=node.title,
            journal=journal["name"],         # 必须用配置里的规范名
            url=node.url,
            surface=journal.get("surface"),
            doi=node.doi,
            online_date=node.date,           # 拿不到就 None，绝不猜
            date_source="publisher" if node.date else "missing",
        ))
    return items, ok(journal, items, http=resp.status)
```

契约：

1. 不自己实现重试/限速/缓存，用 `fetcher`；
2. 日期拿不到就 `None`，不要用「今天」或列表顺序推断；
3. 返回 `(list[Article], JournalScan)`，状态用 `ok` / `failed` 生成；
4. 抛异常也可以 —— 引擎会兜住，把该刊标成 `error`，其他刊继续。

## 7. 被 403 了怎么办

按成本从低到高：

1. **Crossref 兜底**（标 metadata-only）—— 90% 的场景到这里就够了；
2. 换 UA / 加 `Accept-Language`（在期刊 YAML 里加 `headers:`）；
3. 复用浏览器 cookie —— 中等成本、易失效，不建议长期依赖；
4. Playwright 无头浏览器 —— 重、要装几百 MB，只在万不得已时做。

**不要**因为抓不到就把它从配置里删掉：留在配置里、状态标 `blocked`，
比"看起来一切正常但没有文章"诚实得多。

## 8. 排序与标题层级（可选）

```yaml
impact_factor: 15.7      # 只用于排序与覆盖表展示；不写按 0 处理
heading_level: 4         # 报告里这本刊的标题层级（3-6），条目标题再深一级
preprint: true           # 预印本：日报里单独成节，排在期刊之后
exclude_doi_prefixes: ["10.1038/d"]   # 命中 DOI 前缀 → 归为“非研究类型”
date_filter: created     # 仅 crossref：Elsevier 只有年月时用收录日
```

- 排序：预印本最后 → 影响因子降序 → 刊名；
- 影响因子是可编辑的示例值，不是权威数据；
- Nature 系用 `exclude_doi_prefixes` 把新闻/评论（`10.1038/d…`）从研究正文里剔掉，
  它们仍会出现在「非研究类型」审计段；
- 完整字段模板见 [`examples/journals/_template.yaml`](../examples/journals/_template.yaml)。

## 9. 配图字段（可选）

只影响置顶文章的配图，取图顺序：`image_selector` → 正文第一张图 → `og:image` →
`cover_url`。

```yaml
name: bioRxiv
adapter: biorxiv
server: biorxiv
image_page: ".full"          # 预印本要正文页才有图（摘要页只有站点 logo）
# image_selector: "figure img"
# cover_url: "https://example.org/journal-cover.png"
```

- 图片下载到日报同级 `assets/`，按 URL 哈希命名，重复渲染不会重复下载；
- 只接受 `image/*` 且 ≤ `images.max_bytes`（默认 2MB）的位图，logo/图标/SVG 会被过滤；
- **bioRxiv / medRxiv 的 `.full` 页面目前对自动请求返回 403/429**，所以预印本配图
  经常拿不到；期刊文章不受影响。拿不到就跳过，不会影响日报。

## 10. 验收清单

- [ ] `litradar journals test "<刊名>"` 有 title / date / doi
- [ ] `litradar doctor` 该刊不是 ❌
- [ ] `litradar fetch --journal "<刊名>"` 后 `store/*.json` 里 `items_seen > 0`
- [ ] 覆盖表出现 ✅ 已读 或 ⚠ 仅元数据（不是 ⛔）
- [ ] 条目数与该刊近期发文节奏相符（骤降通常意味着改版）
