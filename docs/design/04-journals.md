# 04 · 期刊配置与适配器

目标：**新增一本刊 ≤ 10 分钟，不改引擎代码。**

## 1. 适配器分级

| 级别 | adapter | 适用 | 可靠性 | 本机实测 |
|---|---|---|---|---|
| A | `rss` | 有 RSS/Atom/RDF | 高 | ✅ Nature 系、PLOS 系 |
| B | `crossref` | 任何有 ISSN 的刊（**仅发现**） | 中 | ✅ API 可用 |
| C | `html` | 栏目页 + CSS 选择器 | 中高 | ✅ 页面可载（BMC） |
| D | `browser` | JS 重/反爬（可选，M5） | 低但唯一 | ⛔ OUP、PNAS 403 |
| E | `manual` | 手工粘贴 | 兜底 | ✅ |
| X | `custom` | 以上都不行的怪刊 | 取决于实现 | ✅ 逃生舱 |

降级：配置里写 `fallback: [rss, crossref]` 逐级尝试；降级必须写进 `reason` 与报告覆盖表。

## 2. `config.yaml`（全量）

```yaml
version: 1
vault: D:\my_brain\XTF_kgb
direction: "进化基因组学；正选择与基因家族扩张；植物自交不亲和性；GWAS 与复杂疾病变异"
language: zh-CN

output:
  report_dir: "02-Reading/文献雷达/{yyyy}/{MM}"
  filename: "{yyyy}-{MM}-{dd}"
  index_file: "02-Reading/文献雷达/文献雷达索引.md"
  index_mode: append            # append | dataview | none
  betternote_dir: ""            # 填 "02-Reading/4-betternotes" 则自动建 @ 笔记
  betternote_prefix: "@"

schedule:
  kind: daily
  time: "09:00"
  tz: Asia/Shanghai
  window: yesterday             # 早 9 点跑"昨天"整天：不重叠不遗漏

report:
  highlights: 8                 # 置顶上限；正文永远全量
  abstract_chars: 1500

screening:
  mode: llm                     # llm | keyword
  min_relevance: 0.6
  max_items: 60                 # 单次喂模型上限，超出按日期倒序截断并标注
  include_types: [research-article, review]
  keywords:                     # keyword 兜底必须英文（匹配英文摘要）
    - positive selection
    - genome-wide association
    - self-incompatibility

llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"
  api_key_env: "LITRADAR_API_KEY"   # 只存环境变量名
  timeout_s: 120
  max_retries: 2

embedding:
  enabled: false
  backend: local                # local | api
  model: "BAAI/bge-m3"
  profile: "zotero:collection/进化基因组学"
  cache_dir: ".litradar/vectors"
  top_k: 8

zotero:
  api: "http://127.0.0.1:23119"
  enabled: false

http:
  timeout_s: 25
  retries: 2
  rate_limit_s: 2
  user_agent: "litradar/2.0 (personal research use)"
  cache_ttl_hours: 12
```

## 3. 每刊一个文件：`journals/<slug>.yaml`

**A · RSS（最常见）**

```yaml
name: Nature Genetics
adapter: rss
feed: https://www.nature.com/ng.rss
surface: Latest Research
issn: "1061-4036"
```

**B · HTML 栏目页**

```yaml
name: The Plant Cell
adapter: html
url: https://academic.oup.com/plcell/advance-articles
base_url: https://academic.oup.com
surface: Advance Articles
selectors:
  item: "li.al-article-item"
  title: "h4 a"                    # 支持 ::attr(href) 与 ::text
  link: "h4 a::attr(href)"
  doi: "span.al-doi"
  date: "span.al-date"
  abstract: "div.al-abstract"      # 可选
content_type: research-article
fallback: [crossref]
issn: "1040-4651"
```

**C · Crossref 仅发现**

```yaml
name: Molecular Biology and Evolution
adapter: crossref
issn: "0737-4038"
surface: Advance Access
mailto: you@example.com
```

**D · 自定义代码（逃生舱）**

```yaml
name: Cell
adapter: custom
module: journals/cell_custom.py
surface: Articles in Press
url: https://www.cell.com/cell/newarticles
```

## 4. custom 适配器接口

```python
# journals/cell_custom.py
from litradar.model import Article
from litradar.adapters.base import failed, ok

def fetch(journal: dict, fetcher, window):
    """返回 (list[Article], JournalScan)。允许抛异常，引擎会兜住并标 error。"""
    resp = fetcher.get(journal["url"])          # 自带重试/限速/缓存/UA
    if not resp.ok:
        return [], failed(journal, "blocked", resp.error or f"HTTP {resp.status}",
                          url=journal["url"], http=resp.status or None)

    items = []
    for node in parse_layout(resp.text):
        items.append(Article(
            title=node.title,
            journal=journal["name"],            # 必须用配置里的规范名
            url=node.url,
            surface=journal.get("surface"),
            doi=node.doi,
            online_date=node.date,              # 拿不到就 None，不要猜
            date_source="publisher" if node.date else "missing",
        ))
    return items, ok(journal, items, http=resp.status)
```

契约：

1. 不自己实现重试/限速/缓存，用 `fetcher`；
2. 日期拿不到就 `None`，绝不从列表顺序或"今天"推断；
3. `journal` 必须用 `journal["name"]`；
4. 类型判断不了留 `research-article`；
5. 返回 `(list, JournalScan)`，状态用 `ok/failed` 助手生成。

## 5. 实测结论（2026-10）

| 站点 | 结果 | 备注 |
|---|---|---|
| `www.nature.com/<j>.rss` | ✅ 200 | RDF；`<prism:doi>`、`<prism:publicationDate>`；`<description>` 混着 `Journal, Published online: …; doi:…` 样板 + `<p>`，**必须清洗** |
| `journals.plos.org/plosgenetics/feed/atom` | ✅ 200 | Atom，`<summary>` 是完整摘要 |
| `api.crossref.org/works` | ✅ 200 | `filter=issn:…,from-online-pub-date:…`；摘要常带 `<jats:p>` 与 `Abstract` 前缀 |
| `academic.oup.com/*` | ⛔ 403 | Cloudflare |
| `www.pnas.org/toc/...` | ⛔ 403 | 同上 |
| `genomebiology.biomedcentral.com/articles` | ✅ 200 | 340KB HTML，C 级可行 |
| `genome.cshlp.org` | ⚠ 302 循环 | 需 cookie 链，暂不做 |

## 6. 新增一刊 SOP

1. 浏览器打开该刊 ASAP / Early View / Articles in Press，确认**普通读者可见**；
2. 页脚找 RSS → `adapter: rss`（首选）；
3. 无 RSS → 看源码找条目容器与字段选择器 → `adapter: html`；
4. 都不行 → `adapter: crossref`（接受"仅元数据"标签）；
5. 写 yaml，跑 `litradar journals test "<刊名>"`：输出须含 `status`、条数、前 3 条的 `title / online_date / doi`。
   **日期全空或 DOI 全空 = 选择器错了**，回去改；
6. `doctor` 复检，提交。

## 7. 内容类型过滤

只收 `research-article` 与 `review`，排除 `editorial / correction / erratum / retraction / news / obituary / book-review / correspondence / comment / in-this-issue`。

RSS 无类型字段 → 标题前缀启发式：

```python
_NON_RESEARCH = re.compile(
    r"^(editorial|correction|erratum|retraction|news|obituary|book review|"
    r"correspondence|comment|in this issue|table of contents|focus|"
    r"publisher.s note|expression of concern)\b", re.I)
```

启发式必有漏，所以**被排除条目要进 `excluded_type` 审计段**，可追。
