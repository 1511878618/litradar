# 02 · 引擎 CLI 规格

包名与命令名：`litradar`。入口：`console_scripts` → `litradar = litradar.cli:main`。Python `>=3.10`。

## 1. 命令表

| 命令 | 作用 | 主要参数 |
|---|---|---|
| `init` | 在 vault 建 `.litradar/` 骨架 | `--vault PATH`（必填） |
| `fetch` | 抓取 + 日期窗 + 去重 → `store/<date>.json` | `--date / --window / --journal / --ref / --no-cache / --jsonl` |
| `render` | 读 store → [LLM] → 校验 → 日报 + 索引 | `--date / --llm / --no-llm / --jsonl / --kind` |
| `run` | `fetch` + `render` | 两者并集；配置日追加周报 |
| `weekly` | 合并本周日报 → 周报（含热点） | 不抓取，只读 `store/<date>.json` |
| `embed` | 算相关度 → `scores.json` | `--date / --profile / --top / --jsonl` |
| `screen` | 校验外部写入的 `screen.json`（默认不落盘） | `--file / --apply / --strict` |
| `status` | 上次抓取、三态覆盖 | `--json` |
| `doctor` | 依赖 / 路径 / 每刊可达性 | `--json` |
| `journals` | `list` / `add NAME` / `test NAME` | 子命令 |
| `schedule` | `print` / `install` / `uninstall` | `--time HH:MM` |
| `deploy` | 引擎副本 + 配置 + 技能装进 vault | `--vault PATH` |
| `upgrade` | 更新 vault 内引擎副本（不动配置/状态） | — |
| `zotero` | `collections` / `push --date` | `--api URL` |

通用：`--vault` / `--config` 可放子命令前后（`argparse` 的 `parents=[common]` + `default=SUPPRESS`，v1 已有范例）。

## 2. 退出码（前端按此映射）

| 码 | 含义 | 前端行为 |
|---|---|---|
| 0 | 成功 | 绿色，刷新面板 |
| 1 | 未预期异常 | 红色，展示 stderr 末 20 行 |
| 2 | 配置错误 | 黄色，提示打开配置 |
| 3 | screen.json 校验失败 | 黄色，展示「校验记录」 |
| 4 | 全部期刊抓取失败 | **红色**，禁止显示"无新文章" |
| 5 | doctor 发现问题 | 黄色，展示清单 |
| 6 | 已有任务在跑（锁） | 灰色，提示稍后 |
| 7 | 超时 / 被取消 | 灰色，可重试 |

## 3. `--jsonl` 流式协议（插件唯一的进度来源）

规则：带 `--jsonl` 时 **stdout 每行一个 JSON**（`ensure_ascii=False`，立即 flush），人类日志全走 stderr。

```json
{"event":"run_start","run_id":"2026-10-29","engine":"2.0.0","schema":2,"started_at":"2026-10-30T09:00:01+08:00"}
{"event":"journal_start","journal":"Nature Genetics","adapter":"rss"}
{"event":"journal_done","journal":"Nature Genetics","status":"ok","http":200,"items":8,"ms":812,"reason":null}
{"event":"journal_start","journal":"Molecular Biology and Evolution","adapter":"crossref"}
{"event":"journal_done","journal":"Molecular Biology and Evolution","status":"blocked","http":403,"items":0,"ms":640,"reason":"Cloudflare / bot protection"}
{"event":"progress","stage":"screen","done":3,"total":40}
{"event":"report_written","path":"02-Reading/文献雷达/2026/10/2026-10-29.md","items":9,"highlights":2,"coverage":"partial"}
{"event":"run_end","ok":true,"exit":0}
```

约束：

- `status` ∈ `ok | metadata-only | blocked | timeout | error`；
- `reason` 是人话（`HTTP 403 (Cloudflare)`、`页面可读但 selectors.item 未匹配`），前端直接展示；
- `items` 是该刊本轮条目数（去重前）；
- `run_end.exit` 必须与进程退出码一致；
- 事件按上表顺序保证，插件可依赖。

## 4. 参数语义

| 参数 | 语义 | 默认 |
|---|---|---|
| `--date` | 「报告哪一天的文献」，决定 run_id 与文件名 | 昨天（按 `--tz`） |
| `--window` | `yesterday / last_week / last_n_days / explicit` | `yesterday` |
| `--ref` | 把"今天"固定为某天，仅补跑与测试 | 空 |
| `--journal` | 只跑指定刊（可重复） | 全部 |
| `--no-cache` | 忽略 HTTP 缓存 | 关 |
| `--no-llm` | 跳过 LLM，走关键词兜底 | 关 |

`--date` 与 `--window` 互斥；`--date D` ≡ 窗口 `[D, D]`。

## 5. 目录布局（vault 内）

```
<vault>/.litradar/
├── .lock
├── config.yaml
├── journals/*.yaml
├── journals/*.py            # 仅 custom 适配器
├── store/2026-10-29.json    # 抓取结果（含 abstract_raw）
├── runs/2026-10-29/
│   ├── screen.json          # LLM 输出（可能不存在）
│   ├── scores.json          # embedding 输出（可能不存在）
│   ├── report.md            # 与 vault 内日报同内容
│   └── run.log
├── state/seen.json
├── state/journal_scans.json
├── vectors/*.npy
└── logs/cli-2026-10-30.log
```

日报与索引：

```
02-Reading/文献雷达/<yyyy>/<MM>/<yyyy>-<MM>-<dd>.md
02-Reading/文献雷达/文献雷达索引.md
```

## 6. 性能与超时

| 阶段 | 预算 | 超时行为 |
|---|---|---|
| 单刊抓取 | 60s | 该刊标 `timeout`，继续下一刊 |
| 全轮 fetch | 10 min | 中止，退出码 7，已完成状态保留 |
| LLM screening | 3 min（40 篇内） | 降级关键词 + 报告注明 |
| embedding | 60s（模型已加载） | 保留上次 scores，标"数据过期" |
| 整条 `run` | 15 min | 同上 |

## 7. 幂等要求（回归测试必测）

- 同一天 `run` 两次：第二次 `items` 为 0（全被 seen 拦截），日报内容不变，索引只有一行；
- `render --no-llm` 在 `screen.json` 存在时也不得改动它；
- `fetch` 中断后重跑：`journal_scans.json` 不丢已成功的刊；
- `seen.json` 只在 render 成功后变化。

## 8. 日志

- `run.log`：人读，`HH:MM:SS [journal] 状态 说明 (ms)`；
- `logs/cli-*.log`：stderr 全集，滚动 7 天；
- **禁止**把 API key、cookie 写进任何日志；`sk-[A-Za-z0-9]{10,}` 一律替换为 `sk-***`。
