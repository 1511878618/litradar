# 05 · 报告格式

## 1. 骨架（顺序固定，前端据此解析）

```markdown
---
title: 文献雷达 2026-10-29
date: 2026-10-30
tags: [文献雷达/daily]
direction: "进化基因组学；正选择…"
journals: [Nature Genetics, PLOS Genetics, Molecular Biology and Evolution]
window: 2026-10-29
coverage: partial
screening: llm-v1（deepseek-chat）
new_items: 9
highlights: 2
---

# 文献雷达 2026-10-29

> 扫描时间：2026-10-30 09:00（Asia/Shanghai）
> 严格时间窗：yesterday → 2026-10-29（含两端）
> 本期刊：Nature Genetics、PLOS Genetics、Molecular Biology and Evolution
> 研究方向：…
> 收录范围：窗口内、官网可见、且此前未报告过的**全部**文章；相关性只决定置顶，不决定取舍
> 置顶依据：llm-v1（deepseek-chat）

## 与你最相关（2 篇）

### 1. 精确标题
- 期刊：Nature Genetics
- 出版商状态：Latest Research
- 官网首次在线日期：2026-10-28
- 首次观察时间：2026-10-30 09:00
- DOI: https://doi.org/10.1038/…
- 作者：A, B, C 等
- 相关性：0.82 —— 用群体基因组数据识别了正选择位点
- 主题：进化基因组学 · 正选择
- 英文摘要：（verbatim 时原文；概述时标注"（概述）"）
- 可见性来源：https://…
- 摘要来源：https://…

## 全部新上线（9 篇）

### Nature Genetics（3 篇）

#### 精确标题
2026-10-22 · Latest Research · [原文](https://…) · [DOI](https://doi.org/10.1038/…)

（英文摘要整段）

#### 下一篇…

### PLOS Genetics（3 篇）
…

## 覆盖情况

| 期刊 | 官网栏目 | 覆盖状态 | 本轮条目 | 上次成功扫描 |
|---|---|---|---|---|
| Nature Genetics | Latest Research | ✅ 已读 | 8 | 2026-10-29 09:00 |
| Molecular Biology and Evolution | Advance Access | ⚠ 仅元数据 | 3 | — |

> ⚠️ **partial coverage**：Molecular Biology and Evolution（仅 Crossref 元数据）。这些刊的结论不可表述为「本周无新文章」。

## 越窗（官网日期不在本轮窗口内，未计入）
- 标题 — 刊 · 官网日期 2026-10-30

## 待核验（官网未给出可判定日期）

## 已报告过（去重跳过 3 篇）
> 这些文章在更早的日报中出现过，不重复计入。

## 校验记录
- 拒收：sha1:…: 不在本轮候选集内（可能是编造或过期结果）
- 降级：sha1:…: 标为原文但与原摘要不一致（相似度 0.71），已降级为概述

*由 litradar v2.0.0 生成 · 2026-10-30T09:00:31+08:00*
```

## 2. 硬性约束

1. **正文必须包含本轮全部候选**，一条不漏（`relevant: false` 也进正文）。
2. **置顶段最多 `report.highlights` 条**（默认 8），按 `relevance` 降序、同日按 `online_date` 降序。
3. **每条必须有**：标题、期刊、`online_date`（或"官网未标注"）、原文链接。
4. 摘要缺失写死为：`英文摘要：No abstract supplied by the journal`（不翻译、不补写）。
5. `date_source=crossref` 的条目，日期后必须加 `（元数据日期，未在官网核实）`。
6. **不得出现**模型生成的"总体趋势""研究热点"总结段（最容易编）。
7. 中文用于标题/栏目/说明；论文标题、期刊名、摘要**保持英文原文**。

## 3. 命名与路径

| 项 | 规则 | 示例 |
|---|---|---|
| 目录 | `report_dir` 模板，`{yyyy}/{MM}` 取自**窗口结束日** | `02-Reading/文献雷达/2026/10/` |
| 文件名 | `{yyyy}-{MM}-{dd}`（窗口结束日） | `2026-10-29.md` |
| run_id | 同日 → 日期；区间 → `起_止`；周 → `2026-W43` | — |

> 文件名必须由**窗口**决定，不能由"运行时刻"决定；否则补跑历史或周一跑上周会写错文件。v1 踩过。

## 4. 索引

`文献雷达索引.md`，每次渲染**更新**本 run_id 那一行（不追加重复行）：

```markdown
# 文献雷达索引

- [[2026-10-28]] · 7 篇（置顶 3） · 覆盖 complete · 2026-10-29
- [[2026-10-29]] · 9 篇（置顶 2） · 覆盖 partial · 2026-10-30
```

`index_mode: dataview` 时改写成 dataview 代码块：

````markdown
```dataview
TABLE new_items AS 篇数, highlights AS 置顶, coverage AS 覆盖
FROM "02-Reading/文献雷达"
SORT file.name DESC
```
````

## 5. betternote（可选，默认关）

`output.betternote_dir` 非空时为**置顶条目**生成 `@<doi后缀>.md`：

```markdown
---
title: "精确标题"
journal: Nature Genetics
date: 2026-10-28
doi: 10.1038/…
url: https://…
tags: unread
rating:
comment:
---

# 精确标题

（英文摘要）
```

已存在的文件**不覆盖**（用户可能已写笔记）。

## 6. 前端可依赖的锚点

只依赖这些稳定标记，不要依赖排版细节：

- frontmatter：`new_items` / `highlights` / `coverage` / `screening`
- 二级标题：`## 与你最相关（N 篇）` / `## 全部新上线（N 篇）` / `## 覆盖情况` / `## 校验记录`
- 覆盖表状态词：`✅ 已读` / `⚠ 仅元数据` / `⛔ 被拦截` / `❌ 解析失败`

更稳的做法：面板读 `store/<date>.json` 与 `runs/<date>/*.json` 渲染，markdown 只作为可点击产物。
