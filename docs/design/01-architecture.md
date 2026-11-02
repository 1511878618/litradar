# 01 · 架构

## 1. 三层与一条边界

```
┌──────────── 前端（可替换）────────────┐
│  Windows 计划任务   Obsidian 插件   CLI │
└──────────────────┬────────────────────┘
                   │ 只读写 vault 内文件 + spawn 子进程
┌──────────────────┴────────────────────┐
│ 数据（vault 内，自洽、可同步、可审计）  │
│ .litradar/config.yaml  journals/*.yaml  │
│ store/ runs/ state/ vectors/ logs/      │
│ 02-Reading/文献雷达/*.md                │
└──────────────────┬────────────────────┘
                   │
┌──────────────────┴────────────────────┐
│ 引擎（Python，确定性，无 GUI）          │
│ adapters → 日期窗 → 去重 → [LLM 校验]   │
│          → 渲染 → 状态提交              │
└───────────────────────────────────────┘
```

**边界判据（唯一一条）**：

> 把插件删掉，日报还在不在？在 → 架构正确；不在 → 有逻辑漏进前端了。

## 2. 为什么不是"服务"

刻意做成**一组幂等命令 + 文件状态**：没有常驻进程、没有端口、没有账号。

| 场景 | 命令行工具 | 常驻服务 |
|---|---|---|
| 崩溃恢复 | 重跑同一条命令 | 守护进程 + 重启策略 |
| 补跑历史某天 | `--date 2026-10-20` | 加管理接口 |
| 插件与计划任务同时触发 | 文件锁 + 幂等 | 排队/去重 |
| 用户排查 | 看 `runs/<id>/run.log` | 日志服务 |

**不要为了"像服务"而加进程。**

## 3. 职责边界（越界即错）

| 层 | 该做 | 绝不该做 |
|---|---|---|
| 引擎 | 抓取、解析、日期判定、去重、调 LLM、校验、渲染、写状态 | 弹 UI、读 Obsidian 设置、依赖插件 |
| 数据 | 存配置、原始抓取、筛选结果、日报、去重状态 | 存业务逻辑（`journals/*.py` 除外） |
| 前端 | 展示、触发、配置编辑、错误提示 | 判断相关性、算日期窗、改 `seen.json`、拼报告 |

典型越界：插件里判断相关性（换前端就行为不一致）；插件里算"昨天"（边界必然打架）；插件直接写日报（关掉就没日报）；引擎读 Obsidian 的 `data.json`（绑死）。

## 4. 一次日报的数据流

```
09:00  schtasks
  └─ litradar fetch --all --jsonl
       ├─ journals/*.yaml 逐刊 → adapter.fetch() → list[Article]
       ├─ 日期窗过滤（yesterday，含两端，Asia/Shanghai）
       ├─ 去重（seen.json，DOI 优先）
       └─ 写 store/<date>.json + 更新 journal_scans.json（三态）
  └─ litradar render --date <date> --llm --jsonl
       ├─ 读 store/<date>.json
       ├─ （可选）LLM 写 runs/<date>/screen.json
       ├─ validate.py 校验/降级/拒收
       ├─ render.py 生成 markdown（全量 + 置顶 + 审计）
       ├─ 写 02-Reading/文献雷达/<yyyy>/<MM>/<date>.md + 索引
       └─ 成功后才把本轮文章写进 seen.json

任意时刻  Obsidian 插件读 store/ 与 reports/ → 面板
```

## 5. 并发与幂等

- 每个 vault 一把文件锁（`.litradar/.lock`；Windows `msvcrt.locking`，POSIX `fcntl`）；
- 拿不到锁 → 立即退出码 `6`，前端提示"已有任务在跑"；
- `fetch` 可重复：缓存 + 去重保证结果一致；
- `render` 可重复：同一 run_id 覆盖写日报，索引行更新而非追加。

## 6. 为什么"模型算是非必需"

| 步骤 | 失败后果 | 可否跳过 |
|---|---|---|
| fetch | 没有数据 | 否 |
| render（无 LLM） | 退化版日报（全量 + 关键词置顶） | 可降级 |
| LLM screening | 置顶质量下降 | **可** |
| embedding | 置顶精度下降 | **可** |

设计原则：**fetch 必过、render 必过，LLM 与 embedding 都可失败**。任何可选步骤失败都不允许让日报为空。
