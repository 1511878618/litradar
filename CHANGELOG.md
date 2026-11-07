# Changelog

## 2.2.0 — 2026-11-08

配套 Obsidian 插件 0.4.0、Zotero Bridge 0.2.0；数据 schema 保持 2，新增字段向后兼容。

- 新增单刊重试、失败批次恢复、近几日自动补齐与迟到回查；合并原批次并保留已有文献和首次发现时间。
- Crossref 支持分页与截断提示，Science / Science Advances RSS 被拦时降级为发表日期元数据查询。
- 日报新增色块排版、独立词云、缓存中文标题翻译；期刊与预印本分别统计。
- 新增跨批次文献库，支持中文检索及日期、期刊、主题、文献类型和 Reading List 状态筛选。
- 面板与日报共用 presentation.json；可离线重建历史展示数据，不改写原日报。
- Zotero Reading List 双向状态联动、每日收藏夹归档、入库及 PDF 状态检查，保留原收藏夹和阅读记录。
- 新增可撤销的推荐反馈及有 DOI 关系证据的预印本／正式版关联；正式版已收录时避免重复置顶。
- TLS 握手异常降级、自动抓取设置、历史浏览与面板状态保持改进。
- Python 回归测试 384 项；CI 增加前端回归检查及 Zotero XPI 构建检查。


## 2.1.0 — 2026-11-02

日报仍是每日一份，但更像「一份能读的刊物」：置顶的几篇有中文精读，周更刊物不再假装每天都更新。

**中文精读（`deepread`）**

- 新增 `runs/<date>/deepread.json`：给置顶文章写中文标题、研究领域、80–200 字解读、3–5 个中文关键词；
- 独立模型调用，只覆盖 `deepread.max_items`（默认 8）篇；`--no-llm` 或模型失败时整段退回英文版式，日报照常生成；
- 逐条校验：`id` 必须在候选内、标题/解读非空、超长截断、解读中的数字必须出现在标题或摘要里（否则记 warning）；
- 被拒条目与 warning 全部写进「校验记录」，不静默丢弃；
- `runs/<date>/deepread.json` 与 `screen.json` 一样可复用，重渲染不再花钱。

**期刊节奏（`cadence`）**

- 每刊 YAML 支持 `cadence: daily | weekly` + `weekday: mon..sun`（或 `0..6` / `周三`）；
- 新增覆盖状态 `off-cycle`（🛌 非出版日）：窗口不包含更新星期且 0 篇时明确标注，不算失败、不降级覆盖；
- 窗口包含更新星期却 0 篇时仍是 `ok`，但 `reason` 写明「周三更新，本轮 0 篇」，提示核对采集；
- Science（周四）、Science Advances（周五）默认标为周更；`journals list` 新增「更新」列，`journals add --cadence/--weekday`。

**周报（weekly）**

- 新增 `litradar weekly`：把本周（周一~今天）每天的日报合并成 `2026-W45.md`，不重新抓取；
- `--kind weekly` 支持任意起止（`--start/--end`），可把「上周一 ~ 今天」合成一份周报；
- 配置了 `weekly.weekday`（默认周五）时，同一条计划任务在日报之后自动补一份周报，不需要第二条定时任务；
- 周报新增「本周热点」：模型归纳 1–5 条，每条必须引用 ≥2 篇本周论文，引用候选外 id 直接丢弃；
- 周报页头列出本周缺失抓取的日期（那天没开机就不假装覆盖）；
- 产物 `runs/<week>/hotspots.json`；front-matter 新增 `kind` / `hotspots`。

**期刊**

- 内置期刊 17 → 21：新增 Cell、Nature Sustainability、Nature Climate Change、Nature Computational Science；
- Nature / Science / Cell 标为周四出刊，Science Advances 周五；Cell 主用 RSS，TLS 被拦时自动降级 Crossref（仅元数据）；
- 其余 Nature 子刊是月刊、在线优先连续出版，保持 `daily`。

**科研画像（Zotero → LLM）**

- `litradar zotero profile` 支持多收藏夹：`--collection A --collection B` 或 `--collections "A,B"`；
- 新增 `--llm`：把元数据（标题/期刊/年份/标签/摘要节选）交给模型归纳出
  `direction / summary / keywords / research_questions / methods`，校验失败或无 key 时自动退回关键词画像；
- 产物落在 `.litradar/profiles/<名称>.yaml` 与 `.md`（保留 Unicode 名称），`--write` 同时更新生效的 `profile.yaml`；
- Obsidian 设置页新增「科研画像」：收藏夹筛选 + 多选勾选 + 画像名称 + 一键生成（`zotero profile --collections … --llm --write`）。

**日报拆分（可选）**

- `output.split_preprints: true`：期刊写 `<date>.md`、预印本写 `<date>-preprint.md`，两文件互相链接、各自带覆盖表；
- 索引写两行；默认仍是单文件（保持 Obsidian 日记只需管一个路径的习惯）；
- 说明：预印本目前是 **bioRxiv / medRxiv**，项目尚未接入 arXiv。

**关键词相关性**

- 筛选提示词让模型从标题/摘要里抽 3–6 个**原词关键词**（英语优先），存进 `screen.json` 的 `keywords`；
- 相关性改为确定性混合：`(1-w)·模型相关度 + w·关键词命中`，`screening.keyword_weight` 默认 0.5；
  命中数按「3 个封顶」归一，大小写/短语顺序/中英文子串都算命中；
- 置顶卡片显示抽取出的关键词；关键词缺失时退回原来的模型相关度。

**设置在 Obsidian 里完成**

- 新增 `litradar config get|set|unset|list`：写入 `.litradar/config.local.yaml` 覆盖层，**不动 config.yaml 的注释**；
- 新增 `litradar zotero profile`：从 Zotero 收藏夹生成 `direction` + 关键词，写 `.litradar/profile.yaml`；
  本地 API 关闭时直接读 `zotero.sqlite` 的只读副本（Zotero 开着也能读）；
- 插件设置页新增「研究设置」：研究方向、关键词、关键词权重、模型端点/模型名、精读篇数、配图开关、周报日；
  插件只需要用户填 Python 解释器路径，其余通过 `config set` 写入；
- 加载优先级：`DEFAULTS → config.yaml → config.local.yaml → profile.yaml`。

**安装**

- 文档改为 **conda base** 安装（`conda activate base && pip install -e .`），不再推荐仓库内 `.venv`；
- 开发/CI 命令同步更新。

**用量**

- 新增 `runs/<date>/usage.json`：调用次数、输入/输出 tokens、按 `llm.pricing` 估算的费用；
- 日报页脚打印「模型用量：N 次调用 · 输入 … · 输出 … · 约 ¥…」；`--json` 输出带 `usage`；
- 接口不返回 usage 时按字符估算并标记 `estimated`，重渲染复用历史用量不重复计费。

**其他**

- 日报 front-matter 新增 `deepread:` / `hotspots:` 字段；标题段新增「精读依据」「热点依据」；
- `litradar init` 新生成的 config 带 `weekly` / `deepread` 段；
- 342 个自包含测试（新增 54 个）；README/契约文档/期刊指南同步更新。

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
