# 03 · 数据结构契约

所有落盘 JSON 必带 `schema` 版本号；引擎读到更高版本应拒绝（退出码 2）并提示升级。

## 1. Article（内存模型）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✅ | 出版社原题，已去 HTML 标签与实体 |
| `journal` | str | ✅ | 配置里的规范刊名，**不做同义词归一** |
| `url` | str | ✅ | 文章页（可见性证据） |
| `surface` | str? | | `ASAP Articles` / `Early View` / `Latest Research`，保留出版社原词 |
| `doi` | str? | | 归一化：小写、去 `https://doi.org/` 前缀、去尾部标点 |
| `online_date` | date? | | 官网显示的首次在线日期；缺失保持 `None`，**不得用别的日期顶替** |
| `date_source` | str | ✅ | `publisher` / `crossref` / `manual` / `missing` |
| `abstract_raw` | str? | | 英文摘要（已去 JATS/HTML、去 `Abstract` 前缀、截断 1500–2000 字符） |
| `abstract_source` | str? | | 摘要出处 URL |
| `authors` | list[str] | | 全名，保持出版社顺序 |
| `content_type` | str | ✅ | `research-article` / `review` / `other` |
| `first_seen_at` | str? | | ISO8601 带时区 |
| `extra` | dict | | 适配器私有字段（不塞业务语义） |

派生：

- `id = "sha1:" + sha1(fingerprint_key)[:16]`
- `fingerprint_key = "doi:<normalized>"`；无 DOI 时 `"title:<norm_title>|<norm_journal>|<norm_first_author_full>"`

> 无 DOI 时作者键必须用**全名**，只用姓会撞（"Ann Lee" 与 "Bob Lee" 都得 `lee`）。v1 踩过。

## 2. JournalScan（覆盖记录，六态）

| 字段 | 说明 |
|---|---|
| `journal` | 刊名 |
| `status` | `ok` / `metadata-only` / `off-cycle` / `blocked` / `timeout` / `error` |
| `surface` / `url` / `http` | 证据 |
| `items_seen` | 本轮条目数（去重前） |
| `reason` | 人话原因，直接进报告与前端 |
| `scanned_at` | ISO8601 |
| `visibility` | `publisher` / `metadata-only` |

**语义铁律**：只有 `items_seen: 0` + `status: ok` 才能说"没有新文章"，其余组合都不行。

`off-cycle` 只由 `journals/<slug>.yaml` 里的 `cadence: weekly` + `weekday` 产生：本轮窗口不包含该刊的更新星期，且确实 0 篇。它算「已确认」，不算失败，也不会让整体覆盖降级；报告里显示为「🛌 非出版日（周三更新）」。反过来，窗口**包含**更新星期却 0 篇时仍是 `ok`，但 `reason` 会写明「周三更新，本轮 0 篇」，提示用 `journals test` 核对采集。

## 3. `store/<date>.json`

```json
{
  "schema": 2,
  "run_id": "2026-10-29",
  "scan_at": "2026-10-30T09:00:12+08:00",
  "window": {"kind": "yesterday", "start": "2026-10-29", "end": "2026-10-29",
             "tz": "Asia/Shanghai", "label": "2026-10-29"},
  "journals": [
    {"journal": "Nature Genetics", "status": "ok", "surface": "Latest Research",
     "url": "https://www.nature.com/ng.rss", "http": 200, "items_seen": 8,
     "reason": null, "scanned_at": "2026-10-30T09:00:11+08:00",
     "visibility": "publisher"}
  ],
  "items": [],
  "date_unverified": [],
  "out_of_window": [],
  "excluded_type": []
}
```

- `items` 只放待渲染候选（`already_seen: false`）；已报告过的置 `true` 留作审计，不进正文；
- `date_unverified` / `out_of_window` / `excluded_type` 必须在报告里有对应审计段。

## 4. `runs/<date>/screen.json`（模型产物）

```json
{
  "run_id": "2026-10-29",
  "model": "deepseek-chat",
  "screening": "llm-v1",
  "results": [
    {"id": "sha1:9f2c...", "relevant": true, "relevance": 0.82,
     "why": "用群体基因组数据识别了正选择位点",
     "topic_tags": ["进化基因组学", "正选择"],
     "keywords": ["positive selection", "gene family expansion"],
     "abstract_en": "…", "abstract_mode": "verbatim",
     "conclusion": "…", "conclusion_source": "https://www.nature.com/articles/…"}
  ]
}
```

`abstract_mode`：`verbatim`（与 `abstract_raw` 相似度须 ≥ 0.90）/ `faithful-summary` / `none`。

`keywords`：模型从标题/摘要里抽的 3–6 个原词（英语优先），用于和 `screening.keywords` 做
确定性匹配；缺失时退回模型给出的 `relevance`。

## 4.1 `runs/<date>/deepread.json`（精读产物）

只覆盖置顶的几篇（`deepread.max_items`），由单独的模型调用生成；`screen.json` 存在时复用，不重复花钱。

```json
{
  "schema": 2,
  "model": "deepseek-flash",
  "screening": "deepread-v1",
  "papers": [
    {"id": "sha1:9f2c...", "title_zh": "事件级基础模型推动粒子对撞数据分析…",
     "field_zh": "高能物理与机器学习（粒子对撞数据分析）",
     "digest_zh": "背景 → 做了什么 → 结论…", "keywords_zh": ["基础模型", "自监督学习"],
     "warnings": []}
  ]
}
```

校验（违反时该篇退回英文版式，绝不编造）：

| # | 规则 | 违反时 |
|---|---|---|
| 1 | `id` 必须在置顶候选内 | 拒收该条，写入「校验记录」 |
| 2 | `title_zh` / `digest_zh` 非空 | 拒收该条 |
| 3 | 超长字段 | 截断 + warning |
| 4 | 解读里的数字必须出现在标题或摘要中 | 保留正文，但写 warning |
| 5 | 关键词去重、截断到 6 个 | 自动修 |

`warnings` 不会让条目消失：它们进「校验记录」，读者能看到模型越界的地方。

## 4.2 `runs/<week>/hotspots.json`（周报热点）

只在周报里生成。每条热点必须引用至少 2 篇**本周候选**里的论文，否则整条丢弃（这就是「允许写趋势」的代价）。

```json
{
  "schema": 2,
  "model": "deepseek-flash",
  "screening": "hotspots-v1",
  "hotspots": [
    {"title": "本周方向：正选择", "body": "……（60–200 字）",
     "evidence": ["sha1:9f2c...", "sha1:0d7b..."], "warnings": []}
  ]
}
```

| # | 规则 | 违反时 |
|---|---|---|
| 1 | `title` / `body` 非空 | 拒收该条 |
| 2 | `evidence` ≥ 2 且全部命中本周候选 | 拒收该条；不存在的 id 记 note |
| 3 | `body` 超长 / 偏短 | 截断 / 记 note |
| 4 | 超过 5 条 | 截断并记 note |

## 4.3 周报的 store

周报不重新抓取：`assemble_weekly` 把本周每天的 `store/<date>.json` 合并成 `store/<week>.json`，额外带两个字段：

- `assembled_from: "daily"`；
- `missing_days: ["2026-11-04", ...]`——没开机、没跑日报的日子，周报页头会列出来，不假装覆盖。

## 4.4 设置覆盖文件

`.litradar/config.local.yaml` 与 `.litradar/profile.yaml` 都是覆盖层，加载顺序
（后者覆盖前者）：

```
DEFAULTS → config.yaml → config.local.yaml → profile.yaml
```

- `config.local.yaml`：`litradar config set` 写入，给 Obsidian 设置页用，**不改 config.yaml 的注释**；
- `profile.yaml`：`litradar zotero profile --write` 写入，只放 `direction` 与 `keywords`。

## 4.5 `runs/<date>/usage.json`（模型用量）

```json
{"calls": 3, "prompt_tokens": 17398, "completion_tokens": 6801,
 "estimated": false, "model": "deepseek-flash", "cost_cny": 0.0892,
 "recorded_at": "2026-11-02T21:35:03+08:00"}
```

- token 数取自接口返回的 `usage`；接口没给时按字符估算并置 `estimated: true`；
- `cost_cny` 按 `llm.pricing.input_per_million / output_per_million` 估算，只用于参考，不是账单；
- 重渲染会复用已有 `usage.json` 显示，不会重新计费。

## 5. screen.json 校验规则（违反时降级，不报错）

| # | 规则 | 违反时 |
|---|---|---|
| 1 | `id` 必须在候选集内 | **拒收**该条，写入「校验记录」 |
| 2 | `doi` 若给出必须与候选一致 | 忽略该 DOI + 记 note |
| 3 | `verbatim` 时相似度 ≥ 0.90 | 降级为 `faithful-summary` |
| 4 | 候选无 `abstract_raw` 却声称 verbatim | 降级为 `none` |
| 5 | `conclusion` 非空必须有 `conclusion_source` 且域名在白名单 | 隐藏结论 + 记 note |
| 6 | 每条主列表项必须有可打开 `url` | 拒收 |
| 7 | `relevance` ∉ [0,1] | 夹取并记 note |

降级/拒收**不阻塞渲染**，全部写进报告末尾「校验记录」。

## 6. `state/seen.json`

```json
{
  "sha1:9f2c...": {
    "title": "…", "journal": "Nature Genetics", "doi": "10.1038/…",
    "url": "https://…", "first_seen_at": "2026-10-30T09:00:12+08:00",
    "last_reported_at": "2026-10-30T09:00:31+08:00", "last_run": "2026-10-29"
  }
}
```

- `first_seen_at` 永不覆盖；
- **只有 render 成功后**才写入；
- 前端禁止修改此文件。

## 7. `state/journal_scans.json`

```json
{
  "Nature Genetics": {
    "last_ok_at": "2026-10-30T09:00:11+08:00",
    "last_attempt_at": "2026-10-30T09:00:11+08:00",
    "status": "ok", "http": 200, "items_seen": 8, "reason": null,
    "surface": "Latest Research", "url": "https://www.nature.com/ng.rss",
    "consecutive_failures": 0
  }
}
```

`consecutive_failures` 供前端做"连续 3 天失败"提醒。

## 8. `runs/<date>/scores.json`（embedding 产物）

```json
{
  "schema": 2,
  "computed_at": "2026-10-30T09:02:00+08:00",
  "model": "BAAI/bge-m3",
  "profile": {"source": "zotero:collection/进化基因组学", "n_items": 142},
  "scores": {"10.1038/s41588-026-02786-6": 0.71, "10.1093/molbev/msag262": 0.38}
}
```

只影响置顶排序；缺失/过期时一切照常（降级）。

## 9. 版本握手

- 所有 JSON 带 `schema`（当前 `2`）；读到更高版本 → 退出码 2，提示升级引擎；
- `litradar --version --json` → `{"engine":"2.0.0","schema":2}`；
- 插件用 `minEngine` 比对，不满足则禁用按钮并提示 `pipx upgrade litradar`（不自动升级）。
