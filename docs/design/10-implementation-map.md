# 10 · 规格到代码的映射

给接手代码的人：这一页只回答"这条规格写在哪、被谁实现、哪组测试盯着它"。

| 规格 | 代码 | 测试 |
|---|---|---|
| [01 架构](01-architecture.md) 三层与边界 | `pipeline.py`（fetch/render）、`cli.py`（入口）、`obsidian-litradar/`（前端） | `test_cli.py`、`test_pipeline.py` |
| [02 CLI](02-engine.md) 命令、退出码、JSONL | `cli.py`、`events.py`、`logs.py` | `test_cli.py`、`test_misc.py`（Emitter） |
| [02 §5 目录布局](02-engine.md) | `paths.py`、`state.py` | `test_pipeline.py::test_run_log_and_layout_are_written` |
| [02 §7 幂等](02-engine.md) | `pipeline.py`（store/seen 提交顺序）、`render.py`（索引替换） | `test_pipeline.py`、`test_render.py` |
| [03 数据契约](03-contracts.md) | `model.py`、`state.py`、`validate.py` | `test_model.py`、`test_state.py`、`test_validate.py` |
| [03 §5 七条校验](03-contracts.md) | `validate.py` | `test_validate.py`（逐条） |
| [04 期刊配置](04-journals.md) | `config.py`、`adapters/*` | `test_config.py`、`test_adapters.py` |
| bioRxiv / medRxiv（规格 D4 之外的新增） | `adapters/biorxiv.py` | `test_adapters.py`（分页/分类/上限/无结果） |
| 置顶配图（规格之外的新增） | `images.py`、`render.py`、`pipeline.py` | `test_images.py`、`test_render.py` |
| 影响因子排序 / 标题层级（规格之外的新增） | `render.py`、`config.py` | `test_render.py`、`test_config.py` |
| 期刊 / 预印本分节（规格之外） | `render.py`、`pipeline.py` | `test_pipeline.py`、`test_cli.py` |
| 标签、主题图、词云（规格之外） | `tags.py`、`wordcloud.py`、`render.py` | `test_tags.py`、`test_wordcloud.py` |
| [05 报告骨架](05-report.md) | `render.py` | `test_render.py`（front-matter、全量、审计段） |
| [06 插件](06-plugin.md) | `obsidian-litradar/src/*` | CI `plugin` job（`tsc --noEmit` + esbuild） |
| [07 定时](07-integrations.md) | `schedule.py` | `test_schedule.py` |
| [07 LLM](07-integrations.md) | `llm.py`、`screening.py` | `test_llm.py`、`test_screening.py` |
| [07 embedding](07-integrations.md) | `embed.py`、`render.py::final_score` | `test_misc.py`、`test_render.py` |
| [07 Zotero](07-integrations.md) | `zotero.py` | `test_misc.py::test_zotero_item_mapping` |
| [09 环境](09-environment.md) | 已跨平台化：`fetcher.py`（urllib）、`schedule.py`（三平台）、`state.py`（flock/msvcrt） | `test_fetcher.py`、`test_schedule.py`、`test_state.py` |

## 有意偏离规格的地方

| 规格原文 | 实现 | 原因 |
|---|---|---|
| 依赖 httpx + bs4 + lxml + pyyaml | 只剩 `PyYAML` + `beautifulsoup4`（HTTP 用标准库 urllib） | `pipx install` 的安装面越小越可靠；lxml 在部分平台需要编译 |
| `deploy` 把引擎副本装进 vault | `deploy` 只写配置骨架与 agent 说明书；引擎用 `pipx` 装 | 副本会与 pipx 版本漂移，缓存/依赖双份 |
| `upgrade` 更新 vault 内副本 | `upgrade` 打印 `pipx upgrade` 指引 | 同上 |
| `schedule install` 不自动创建任务 | 默认只打印；加 `--apply` 才注册 | 与规格一致，同时给"一条命令装好"的路径 |
| embedding 缓存 `vectors/*.npy` | `vectors/<model>/<sha1>.npy` | 换模型自动失效，不需要手动清 |
| browser 适配器（M5） | 未实现，`get_adapter("browser")` 给出明确报错 | 规格写明"不到万不得已不要上" |
| D4「不做预印本」 | 新增 `biorxiv` 适配器，接入 bioRxiv / medRxiv | 用户明确要求；arXiv 仍不接，预印本逐条标注未经同行评审 |
| 插件 `pythonPath` 默认 `E:\conda\python.exe` | 默认空，设置页给出 Windows / POSIX 两种示例 | 开源用户不在那台 Windows 机器上；空值会被"请填写"拦住 |

## 扩展点

- **新适配器**：`adapters/` + `get_adapter` 注册 + `ADAPTER_NAMES`；
- **新报告段落**：`render.py`，注意 front-matter 与四个锚点标题不能改；
- **新状态**：`state.py` 的原子写与锁是唯一入口，不要在别处直接 `open(..., "w")`；
- **新前端**：只读 `store/`、`runs/`、`state/journal_scans.json`，通过 `--jsonl` 触发。
