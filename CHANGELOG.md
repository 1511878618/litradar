# Changelog

## 2.0.0 — 2026-11-02

首个公开版本，从 v1 的单文件实现重构为可安装的引擎 + 可选插件。

**引擎**

- `fetch` / `render` / `run` 三段式，`--date` 可补跑任意历史日；
- 全命令 `--jsonl` 流式事件（stdout 只有 JSON），人类日志走 stderr；
- 每刊一个 YAML，新增期刊不需要改代码；`journals add|list|test`；
- bioRxiv / medRxiv 预印本适配器（官方 JSON API 按日期区间抓取，正文标注"未经同行评审"）；
- 默认期刊扩到 17 本：Nature/Science 系列、The Lancet Digital Health、JAMIA、JBI、npj Digital Medicine；
- 期刊按影响因子排序，预印本单独成节；标题层级可配置（`heading_level`）；
- Nature 系新闻用 DOI 前缀 `10.1038/d` 过滤，Science 用 feed 的 `dc:type` 过滤；
- Crossref 支持 `date_filter: created`（Elsevier 只给年月时按收录日抓取）；
- 置顶文章可选配图：第一张图 / og:image / 期刊封面，下载到 `assets/`，失败自动跳过；
- 一份日报内「期刊」与「预印本」分节，索引每天一行（Obsidian 日记只需管一个文件）；
- 每篇文章按标题打受控标签；日报新增「今日主题分布」（Mermaid 饼图 + 计数表）；
- 标题词云（自绘 SVG，存 `assets/`，无额外依赖）；
- 置顶文章可带「一句话解读」（LLM `summary_zh`，标注为机器概述）；
- 五态覆盖 `ok / metadata-only / blocked / timeout / error`，失败必须给出人话原因；
- 三级去重（DOI → 持久标识 → 标题+刊+作者全名），`seen.json` 仅在渲染成功后提交；
- `screen.json` 七条校验规则，全部降级而非报错，结论必须带白名单来源；
- 日报固定骨架：全量正文 + 置顶 + 覆盖表 + 五段审计；
- 文件锁 + 退出码 6；`--catch-up --max-days 3` 补跑漏掉的日期；
- `doctor` 逐刊可达性与写入自检；`status --json`；
- 跨平台定时：Windows `schtasks` / macOS launchd / Linux cron，默认只打印命令；
- 可选 LLM（任意 OpenAI 兼容端点）、本地 embedding 排序、Zotero 本地 API 入库；
- 200+ 自包含测试，无 pytest、无网络依赖。

**前端**

- Obsidian 插件（`obsidian-litradar/`）：只读面板、JSONL 进度、版本握手、退出码矩阵。

**契约**

- 所有 JSON 带 `schema: 2`；读到更高版本拒绝并提示升级。
