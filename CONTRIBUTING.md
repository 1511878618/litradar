# 贡献指南

感谢你愿意改这个项目。改之前请先接受它存在的三个约束——它们不是风格偏好，是产品定义：

1. **筛选只排序，不删除。** 任何让模型决定"哪篇不进正文"的改动都不会被合并。
2. **抓取失败 ≠ 没有新文章。** 新增状态必须有明确的人话原因，且出现在覆盖表里。
3. **可选步骤可以失败。** LLM、embedding、Zotero、插件任何一环挂掉，`run` 仍然要写出日报。

## 开发环境

开发装进 **conda 的 base 环境**，不要在仓库里建 `.venv`：

```bash
conda activate base
pip install -e ".[dev]"
```

引擎只依赖 `PyYAML` 与 `beautifulsoup4`；`tzdata` 仅 Windows 需要。
新增硬依赖前请先开 issue 讨论 —— 这个工具的安装体验是特性之一。

## 测试

```bash
python tests/run_tests.py            # 全部
python tests/run_tests.py test_render # 单个文件
ruff check src tests
```

- 测试**不访问网络**：用 `tests/helpers.FakeFetcher` 或本地 `http.server`；
- 每个 bug 修复都要带一个会失败的用例；
- 测试运行器是自己写的（`tests/run_tests.py`），不依赖 pytest，请保持这样。

## 加一个适配器

1. 新建 `src/litradar/adapters/<name>.py`，实现 `fetch(journal, fetcher, window) -> (list[Article], JournalScan)`；
2. 在 `adapters/base.py:get_adapter` 注册；
3. 在 `adapters/__init__.py` 导出；
4. 在 `config.py` 的 `ADAPTER_NAMES` 与 `validate_journal` 里补字段校验；
5. 写 fixture（放 `tests/helpers.py`）与解析测试，覆盖：正常、无日期、被 403、结构损坏；
6. 更新 `docs/journals.md`。

适配器里**不要**自己实现重试、限速、缓存，统一用传入的 `fetcher`。

## 改数据结构

`store/*.json`、`screen.json`、`state/*.json` 都是对外契约：

1. 改 `src/litradar/model.py` / `validate.py` / `render.py`；
2. 若字段语义变化，`SCHEMA` 加一，并更新 `docs/design/03-contracts.md`；
3. 引擎读到更高版本必须拒绝（退出码 2）；
4. 同步 `obsidian-litradar/src/types.ts`；
5. 在 `tests/` 里加向后兼容用例。

## 报告格式

`docs/design/05-report.md` 里的 front-matter 键与四个二级标题是前端的锚点。
新增段落可以，改动已有锚点需要同时改插件并提升 schema。

## 代码风格

- `ruff check src tests` 必须干净（配置在 `pyproject.toml`）；
- 注释解释"为什么"，不复述"做了什么"；
- 面向用户的字符串用中文，日志/异常信息也用中文，代码标识符用英文；
- 不要把 API key、cookie、完整 HTML 写进日志。

## 提交前检查

- [ ] `python tests/run_tests.py` 全绿
- [ ] `ruff check src tests` 干净
- [ ] 新增/修改的行为有测试
- [ ] 面向用户的变更更新了 README / docs
- [ ] 没有把「没有新文章」写进任何失败路径
- [ ] 插件改动跑过 `pnpm run build`

## 提交信息

```
area: short imperative summary

why it matters, and what would have broken without it
```

area 用 `engine` / `adapters` / `render` / `cli` / `plugin` / `docs` / `tests`。
