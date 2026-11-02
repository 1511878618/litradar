# 文档

| 文件 | 内容 |
|---|---|
| [design/](design/) | 原始设计规格（架构、CLI、数据契约、报告骨架、插件、集成、里程碑） |
| [journals.md](journals.md) | 新增期刊 SOP：选适配器、调选择器、处理 403 |
| [journals-catalog.md](journals-catalog.md) | 默认 17 本刊、影响因子、标题层级、排序规则 |
| [../README.md](../README.md) | 使用说明（中文） |
| [../README.en.md](../README.en.md) | Usage (English) |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献指南 |

## 设计规格阅读顺序

1. `design/01-architecture.md` — 三层职责与唯一边界判据
2. `design/02-engine.md` — 命令、退出码、JSONL 事件、幂等
3. `design/03-contracts.md` — 所有落盘 JSON 的形状与校验规则
4. `design/05-report.md` — 报告骨架与硬性文案
5. `design/06-plugin.md` — 插件规格与错误矩阵
6. `design/10-implementation-map.md` — 规格 → 代码 → 测试的对照表（含有意偏离）

`design/08-plan.md`（里程碑）、`design/09-environment.md`（v1 机器的环境坑）
保留为历史记录：其中关于 Windows 路径与 `D:\` 工作区的部分只对当初那台机器成立，
现在的代码是跨平台的。

## 契约变更

数据形状以 `design/03-contracts.md` 为准。改字段语义时：

1. 改代码；
2. 提升 `litradar.SCHEMA`；
3. 更新契约文档与本目录的说明；
4. 同步 `obsidian-litradar/src/types.ts`。
