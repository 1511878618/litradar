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

## 2. JournalScan（覆盖记录，三态）

| 字段 | 说明 |
|---|---|
| `journal` | 刊名 |
| `status` | `ok` / `metadata-only` / `blocked` / `timeout` / `error` |
| `surface` / `url` / `http` | 证据 |
| `items_seen` | 本轮条目数（去重前） |
| `reason` | 人话原因，直接进报告与前端 |
| `scanned_at` | ISO8601 |
| `visibility` | `publisher` / `metadata-only` |

**语义铁律**：只有 `items_seen: 0` + `status: ok` 才能说"没有新文章"，其余组合都不行。

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
     "abstract_en": "…", "abstract_mode": "verbatim",
     "conclusion": "…", "conclusion_source": "https://www.nature.com/articles/…"}
  ]
}
```

`abstract_mode`：`verbatim`（与 `abstract_raw` 相似度须 ≥ 0.90）/ `faithful-summary` / `none`。

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
