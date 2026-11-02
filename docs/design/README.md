# litradar v2 · 实施规格包

> **归档说明**：这是 v2 的原始实施规格，已按此实现。
> 代码在仓库根目录（`src/litradar/`、`obsidian-litradar/`），用法见 [../README.md](../README.md)。
> 规格里的 Windows 路径、`E:\conda` 解释器属于当时那台机器的事实；实现已跨平台
> （Windows 计划任务 / launchd / cron 三选一，`pipx install` 代替引擎副本部署）。
> 数据契约、退出码、报告骨架仍然有效。

> 给实现者：这份目录可独立施工，不需要任何前情对话。
> 参考实现已存在且可运行：`D:\AI小工具\litradar\`（Python 引擎，37 个测试通过）。
> **不要从零重写**，在它基础上做增量（见 `08-plan.md` 的 M1）。

---

## 产品定义

> 一个可 `pipx install` 的 Python 引擎（抓取期刊官网 + 校验模型输出 + 渲染日报 + 可选语义排序），
> 加一个可选的 Obsidian 桌面插件（配置 / 触发 / 展示），
> 两者以 **vault 内的文件** 为唯一契约。

## 三条硬约束

1. **Obsidian 关闭时，日报必须照常生成。** 抓取、LLM、渲染全在 Python 侧；插件永不是必经路径。
2. **筛选只能排序，不能删除。** 窗口内所有新文章都进正文全量列表；相关性只决定置顶。
3. **抓取失败 ≠ 没有新文章。** 每刊三态 `ok / blocked / timeout`，覆盖不到就标 `partial coverage`。

## 现状（v1 可直接复用）

| 能力 | 状态 | 位置 |
|---|---|---|
| RSS / Atom / RDF 适配器 | ✅ 实测 Nature Genetics、PLOS Genetics | `adapters/rss.py` |
| HTML 栏目页（CSS 选择器） | ✅ 单测通过 | `adapters/html_surface.py` |
| Crossref（仅发现，标 metadata-only） | ✅ 实测 MBE | `adapters/crossref.py` |
| 手工录入 | ✅ | `adapters/manual.py` |
| 日期窗（yesterday / last_week / last_n_days / explicit） | ✅ 带单测 | `window.py` |
| 三级去重（DOI → 持久标识 → 标题+作者+刊） | ✅ | `dedupe.py` + `state.py` |
| 原子写、只在渲染成功后提交 seen | ✅ | `state.py` |
| screen.json 校验（防编造，7 条规则） | ✅ | `validate.py` |
| 报告渲染（全量 + 置顶 + 审计段） | ✅ | `render.py` |
| CLI（init/scan/screen/render/run/status/doctor/deploy/upgrade） | ✅ | `cli.py` |
| 模型工位说明书（DSH 技能） | ✅ | `skill.py` |

**v2 要补**：`--jsonl` 流式输出、`custom` 适配器、每刊一个 yaml、`fetch`/`embed` 子命令、文件锁、计划任务安装器、Zotero、Obsidian 插件。

## 阅读顺序

| 文件 | 内容 | 读者 |
|---|---|---|
| `01-architecture.md` | 三层职责、边界判据、数据流 | 所有人 |
| `02-engine.md` | CLI、退出码、JSONL 协议、幂等 | 后端 |
| `03-contracts.md` | 全部数据结构与校验规则 | 后端 + 前端 |
| `04-journals.md` | 配置格式、custom 适配器、新增一刊 SOP | 后端 |
| `05-report.md` | 报告模板、命名、固定文案 | 前端 |
| `06-plugin.md` | 插件规格、spawn 桥、错误呈现 | 前端 |
| `07-integrations.md` | 计划任务、LLM、Zotero、embedding | 后端 |
| `08-plan.md` | M0–M5 里程碑与验收 | 所有人 |
| `09-environment.md` | 本机事实、依赖、沙箱坑 | **开工前必读** |

## 已定决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | LLM 在 **Python 端**，不在插件 | Obsidian 关着也要有日报 |
| D2 | embedding 默认 **本地 bge-m3**，可换 API；失败降级关键词 | 隐私优先，但不做硬依赖 |
| D3 | Zotero 用 **本地 API（127.0.0.1:23119）**，回退 zotlit / BBT | 官方、可读写、能一键入库 |
| D4 | **不做 arXiv**（预印本归 `biorxiv-radar`） | 避免两条产品线打架 |

## 交付定义（DoD）

- `pipx install litradar` 后任意目录 `litradar --version` 可用；
- `schtasks` 每天 09:00 跑一次，**关掉 Obsidian 也出日报**；
- 插件经 BRAT 装上，能看到今日概况、失败红字、置顶列表；
- 三态覆盖、超时、降级、版本握手都有明确 UI 呈现；
- 新增一本刊 ≤ 10 分钟（配置化），不改引擎代码。
