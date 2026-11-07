# Litradar · 文献雷达

> 每天一份**可审计**的文献日报：期刊与预印本全量收录，相关性只决定置顶，不决定取舍。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-384%20passed-brightgreen)](tests/run_tests.py)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.2.0-orange)](CHANGELOG.md)

[English](README.en.md) · [示例日报](examples/sample-report.md) · [期刊目录](docs/journals-catalog.md) · [新增一刊](docs/journals.md)

---

## 它解决什么问题

大多数「论文推送」工具有三种撒谎方式：

1. **悄悄漏文**：抓取失败，页面显示「今天没有新文章」；
2. **替你决定不看什么**：模型说「不相关」，文章就不出现；
3. **假装读过**：摘要被改写、结论被编造、日期是猜的。

Litradar 把这三件事当成设计约束，而不是文案：

| 保证 | 实现方式 |
|---|---|
| **筛选只排序，不删除** | 正文永远包含窗口内的全部候选；模型只决定置顶顺序与理由 |
| **抓取失败 ≠ 没有新文章** | 每刊六态 `ok / metadata-only / off-cycle / blocked / timeout / error`；失败与非出版日都进覆盖表并写明原因 |
| **每条模型断言都可证伪** | `id` 必须命中候选、verbatim 摘要相似度 ≥ 0.90、结论必须带白名单来源，违反即降级并写进「校验记录」 |

名字来自 radar：**扫描、覆盖，以及「没有回波」和「没有目标」是两件事。**

## 功能

- **一份日报，两类内容**：`2026-11-01.md` 内含「期刊」与「预印本（bioRxiv / medRxiv）」两节，Obsidian 日记只需管一个文件；
- **可选拆成两个文件**：`output.split_preprints: true` 时期刊写 `2026-11-01.md`、预印本写 `2026-11-01-preprint.md`，互相链接、各自独立的覆盖表；
- **21 本内置期刊**：Nature / Science / **Cell** 及其子刊、Nature Communications、npj Digital Medicine、The Lancet Digital Health、JAMIA、JBI、PLOS Genetics、MBE、bioRxiv、medRxiv…（[完整目录](docs/journals-catalog.md)）；
- **按影响因子排序、按刊设定标题层级**（可在每刊 YAML 里改）；
- **每篇文章自动打标签**（22 个受控主题），日报带 **Mermaid 主题分布图** + **SVG 词云**；
- **LLM 提关键词 + 关键词相关性**：模型只从标题/摘要里抽 3–6 个原词，匹配你的关键词表由引擎确定性计算（`screening.keyword_weight` 可调）；
- **置顶文章中文精读**：中文标题、研究领域、2–4 句解读、中文关键词（LLM，逐条校验，可关闭）；
- **感知期刊节奏**：`cadence: weekly` + `weekday` 的刊物在非出版日标「🛌 非出版日」，不冒充「无新文章」；
- **周五自动出周报**：`weekly` 把这一周（周一~今天）的日报合并成 `2026-W45.md`，带「本周热点」（每条必须引用至少 2 篇本周论文，否则丢弃）；
- 可选配图（第一张图 / `og:image` / 期刊封面）；
- **七条模型输出校验**：不信任模型的自述，宁肯降级也不编；
- **用量与成本可见**：每次运行把调用次数、输入/输出 tokens、估算费用写进 `runs/<date>/usage.json`，日报页脚也打印一行；
- **全命令 `--jsonl`**：stdout 每行一个 JSON 事件，人类日志走 stderr；
- **幂等 + 文件锁**：同一天重跑不会覆盖已有日报，并发出退出码 6 而不是并发写坏状态；
- **跨平台定时**：Windows 计划任务 / macOS launchd / Linux cron，默认只打印命令；
- **可选增强**：LLM 置顶、本地 bge-m3 语义排序、Zotero 一键入库；
- **科研画像**：`zotero profile --collections "EHR,心血管疾病" --llm` 从 Zotero 元数据归纳方向/关键词/研究问题，存到 `.litradar/profiles/`；Obsidian 设置页可勾选收藏夹一键生成；
- **可选 Obsidian 插件**：面板 + 触发按钮 + 常用设置（只需填 Python 解释器路径，方向/关键词/模型/周报日都在 Obsidian 里改），删掉它日报照常生成。

## 快速开始

需要 Python ≥ 3.10。

装进 **conda 的 base 环境**（后续你自己装，仓库不维护虚拟环境）：

```bash
conda activate base                # 或你自己的环境
pip install -e /path/to/litradar   # 源码安装；发布后可直接 pip install litradar
litradar --version --json          # {"engine": "...", "schema": 2}
```

然后建 vault、跑第一次：

```bash
mkdir -p ~/my-vault && cd ~/my-vault
litradar init --vault .            # 生成 .litradar/ 与 21 本示例刊
$EDITOR .litradar/config.yaml      # 填 direction（研究方向），其余可先不动

litradar doctor --vault .          # 依赖 / 路径 / 每刊可达性
litradar run --vault . --no-llm    # 先不接模型跑通
```

接模型（以 DeepSeek flash 为例，[完整片段](examples/llm-deepseek-flash.yaml)）：

```yaml
llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-flash"
  api_key: "sk-..."                # 或 api_key_file / api_key_env，三选一
screening:
  mode: llm
  max_items: 120
deepread:                         # 置顶几篇的中文精读
  enabled: true
  max_items: 8
weekly:                           # 每周五把这一周合成一份周报
  enabled: true
  weekday: fri
```

```bash
litradar run --vault . --llm
litradar status --vault .          # 上次抓取、覆盖状态
```

没有 API key、模型超时、模型返回垃圾 JSON——日报都会照常生成，只是置顶退化为关键词。

## Zotero 联动安装（可选）

完整步骤见 [Zotero 联动安装教程](docs/zotero-setup.md)：启用 Zotero 10 本地通信，在 Zotero 中安装 Reading List（阅读状态联动时必需）和 Zotero Bridge 0.2.0（状态联动或“下载 PDF”时必需），再配置 Litradar 的文献保存文件夹。

**Reading List 双向状态联动需要 Zotero Bridge 0.2.0。** 在 Litradar 改状态会写入 Reading List，在 Zotero 修改后由 Litradar 刷新同步；支持 Reading List 的全部自定义状态。旧 Litradar 标签和本地清单提供显式迁移入口，保留已有 Reading List 状态。

## 输出长什么样

```
02-Reading/文献雷达/2026/11/
├── 2026-11-01.md          # 每天唯一一份（期刊 + 预印本）
├── assets/
    ├── wordcloud-2026-11-01-daily.svg
    └── <figure>.png
.litradar/runs/<date>/
├── screen.json / deepread.json / hotspots.json
└── usage.json              # 本次调用的 tokens 与估算费用
```

日报结构固定（默认一份；`output.split_preprints: true` 时拆成期刊/预印本两份）：

- front-matter：`kind`（daily/weekly）/ `new_items` / `highlights` / `hotspots` / `coverage` / `screening` / `deepread`；
- 置顶段「与你最相关」（跨期刊 + 预印本统一排序；有模型时是中文精读：中文标题 / 研究领域 / 解读 / 关键词，并保留原文标题与英文摘要供核对）；
- 今日主题分布（Mermaid 饼图 + 计数表 + 词云）；
- `## 期刊（N 篇）` 与 `## 预印本（N 篇）`，各自按刊分组；
- `## 覆盖情况` 分期刊 / 预印本两张表，含影响因子与失败原因；
- 审计段：越窗 / 待核验 / 非研究类型 / 已报告过 / 校验记录。

完整示例：[examples/sample-report.md](examples/sample-report.md)（8 篇的小样本，结构完整）。

## 期刊

`litradar init` 会写入 17 本可直接用的期刊配置。加一刊 = 加一个 YAML：

```yaml
name: Nature Genetics
adapter: rss
feed: https://www.nature.com/ng.rss
surface: Latest Research
issn: "1061-4036"
impact_factor: 30.8
heading_level: 3
cadence: weekly      # 可选：daily（默认）| weekly
weekday: wed         # weekly 必填：周几更新，也可写 2 / 周三
```

适配器：`rss` / `html` / `crossref` / `biorxiv` / `manual` / `custom`，支持 `fallback` 降级链。
新增一刊的完整 SOP（选源、调选择器、处理 403）：[docs/journals.md](docs/journals.md)。

> 预印本单独成节、不参与期刊的影响因子排序；Nature 系的新闻/评论按 DOI 前缀过滤，Science 按 feed 的 `dc:type` 过滤，被排除的仍会出现在「非研究类型」审计段。

## 定时

Obsidian 内自动抓取默认已开启。可在 **设置 → Litradar → 自动抓取与定时** 中切换开关、选择每日时间并点击“保存时间”；雷达面板也提供“自动抓取设置”入口。默认 09:00（Asia/Shanghai），需保持 Obsidian 运行；超过计划时间才打开时会检查补抓。“系统后台定时未安装”不代表插件内自动抓取关闭。

以下命令管理独立的系统后台任务，适用于需要在关闭 Obsidian 后运行的情况：

```bash
litradar schedule install --vault ~/my-vault          # 只打印命令
litradar schedule install --vault ~/my-vault --apply  # 真正注册
```

默认每天 09:00 跑「昨天」整天，并带 `--catch-up --max-days 3`：关机错过的日期，第二天补跑。
如果当天是 `weekly.weekday`（默认周五），同一条计划任务会在日报之后自动再写一份周报，不需要第二条定时任务。

计划任务不继承 shell 环境；key 推荐放 `~/.config/litradar/env`（`chmod 600`），生成的 wrapper 会自动 source。

## 命令

| 命令 | 作用 |
|---|---|
| `init` | 建 `.litradar/` 骨架（配置 + 17 本示例刊 + 说明） |
| `fetch` | 抓取 → 日期窗 → 去重 → `store/<date>.json` |
| `render` | 读 store → [LLM] → 校验 → 日报 + 索引 |
| `run` | `fetch` + `render`（`--catch-up` 补跑漏掉的日期；配置日自动追加周报） |
| `weekly` | 合并本周日报 → 周报 + 本周热点（`--ref` 指定汇总到哪天） |
| `embed` | 语义相关度 → `runs/<date>/scores.json` |
| `screen` | 校验外部写入的 `screen.json`（`--strict` → 退出码 3） |
| `status` / `doctor` | 覆盖情况 / 依赖、路径、每刊可达性（`--json`） |
| `journals list\|add\|test` | 期刊配置与试跑 |
| `config get\|set\|list\|unset` | 读写设置（写到 `.litradar/config.local.yaml`，不动 `config.yaml` 的注释） |
| `zotero profile` | 从 Zotero 收藏夹（可多选）生成科研画像：`--llm` 用模型归纳，失败退回关键词；产物在 `.litradar/profiles/` |
| `schedule print\|install\|uninstall` | Windows / launchd / cron |
| `zotero collections\|push` | 本地 Zotero API，无云端账号 |
| `deploy` / `upgrade` | 写配置骨架与 agent 说明书 / 显示升级方式 |

### 退出码

| 码 | 含义 | 前端行为 |
|---|---|---|
| 0 | 成功 | 刷新 |
| 1 | 未预期异常 | 红色，展示 stderr |
| 2 | 配置错误 | 黄色，打开配置 |
| 3 | `screen.json` 校验失败 | 黄色，展示校验记录 |
| 4 | 全部期刊抓取失败 | **红色，禁止显示「无新文章」** |
| 5 | doctor 发现问题 / 可选依赖缺失 | 黄色 |
| 6 | 已有任务在跑（文件锁） | 灰色，稍后 |
| 7 | 超时 / 取消 | 灰色，可重试 |

## 设计边界

```
前端（可替换）  计划任务 / Obsidian 插件 / CLI
        │  只读写 vault 内文件 + spawn 子进程
数据      .litradar/{config,journals,store,runs,state} + 日报
        │
引擎（Python，确定性，无 GUI）  adapters → 日期窗 → 去重 → [LLM] → 校验 → 渲染 → 状态提交
```

唯一判据：**把插件删掉，日报还在不在？** 在 → 架构正确。

设计规格（数据契约、CLI 协议、报告骨架）在 [docs/design/](docs/design/)；规格与实现的对照（含有意偏离）在 [10-implementation-map.md](docs/design/10-implementation-map.md)。

## Obsidian 插件（可选）

`obsidian-litradar/` 是薄前端：只读 `.litradar` 下的 JSON 与日报，按钮通过 `child_process` 调引擎，解析 `--jsonl` 事件。

- 安装：BRAT，或把 `manifest.json` + `main.js` + `styles.css` 放进 `<vault>/.obsidian/plugins/litradar/`；
- 设置：**只需要填 Python 解释器绝对路径**（conda base 的 `python`）；方向、关键词、模型、精读篇数、周报日都在插件设置页里改，插件调 `litradar config set` 写入 `.litradar/config.local.yaml`；
- 注意：插件仅桌面端可用（`isDesktopOnly: true`）。

## 开发

在 conda base 里直接开发（不要另建 `.venv`）：

```bash
conda activate base
pip install -e ".[dev]"

python tests/run_tests.py     # 337 个用例，自包含，不需要 pytest、不联网
ruff check src tests
```

插件：

```bash
cd obsidian-litradar
pnpm install && pnpm run build
```

数据契约变更请先读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 已知限制

- 需要登录或强反爬的站点（OUP、PNAS、Elsevier）只能走 Crossref 元数据兜底，日报会标「仅元数据」；
- Science / Science Advances 的 feed 只给约 60 字符导读，模型主要靠标题判断；
- 预印本的全文页有 Cloudflare 防护，**配图常不可用**；期刊文章配图正常；
- 中文精读只覆盖置顶的几篇（`deepread.max_items`，默认 8）；正文全量列表仍是英文原文；
- 精读是机器翻译与概括：数字若未出现在标题/摘要里会被记入「校验记录」，但不能保证语义完全无误；
- 影响因子是**排序用的示例值**（JCR 量级），不是权威数据，按自己单位口径改 YAML；
- 主题标签是规则匹配，冷门方向可能漏标（未标出的会统计为「未打标签」，不会假装覆盖）；
- 时间窗严格：9 点之后才上线的「昨天」文章会落到窗外，可用 `--window last_n_days --days 2` 兜住；
- arXiv 未接入（预印本目前是 bioRxiv / medRxiv）；
- `cadence: weekly` 只处理「每周固定一天」的刊物，月刊/双周刊（Nature Methods、Nature Genetics 等）仍按日抓取：它们本来就是在线优先连续出版，0 篇不会被误报为失败；
- 周报由本周已生成的日报合并而来：某天没开机就没有那天的 store，周报开头会列出缺失日期。

## 许可

MIT，见 [LICENSE](LICENSE)。图片与摘要版权归原出版方，仅用于个人阅读。

## 插件阅读工作流

- **浏览批次**：默认跟随最新批次，也可选择已有历史批次；“打开本批次日报”打开所选批次。历史模式隐藏抓取操作，返回最新批次后恢复。
- **搜索筛选**：搜索本批次新条目的标题、摘要、作者、期刊和 DOI；空格分隔的词同时匹配。可组合期刊、期刊文章／预印本、模型判定相关筛选；筛选同时作用于推荐和全部条目。
- **我的阅读**：在文献上选择 Reading List 中的任意状态，即可保存到跨日期清单；选“未设置”清除状态。清单从 Zotero 读取，不依赖历史批次文件继续存在。
- 阅读状态现在由 Zotero Reading List 管理；原 `litradar/待读`、`litradar/已读` 标签可手动迁移；插件原来的 `data.json/readingLibrary` 仅用于迁移旧记录，目标是 Reading List。默认保存到自动创建的 `Litradar 文献雷达 / YYYY-MM-DD` 收藏夹；设置页支持选择其他完整子文件夹路径覆盖默认位置。保存文献、打开条目、打开 PDF 均通过 Zotero；触发 Zotero 原生全文查找需要安装 `zotero-litradar/` 下的 Zotero Bridge 0.2.0 扩展。插件不改写引擎的 `seen.json`。

前端回归检查（在 `obsidian-litradar/` 内运行）：

```sh
node tests/view-state.cjs
node tests/reading-workflow.cjs
```

### 日报排版与中文标题

日报使用 Obsidian 原生 Callout：蓝色表示期刊论文，绿色提示优先阅读，橙色表示未经同行评审的预印本。每篇依次展示中文标题、英文原题、日期与链接、摘要；保留全量收录与覆盖审计。插件自带仅作用于 `cssclasses: [litradar-report]` 的样式，支持浅色、深色和窄屏。

在 **设置 → Litradar → 日报中文标题** 打开翻译，或执行：

```bash
litradar config set translation.enabled true --vault /path/to/vault
```

引擎使用 `google-web` 公开网页翻译接口，无需密钥；这不是官方 Google Cloud Translation API，可能受网络或限流影响。只提交公开文献标题，不提交摘要、笔记、研究画像或 Zotero 数据。原英文标题始终保留，专业术语以原文为准。译文存放在 `.litradar/cache/title-translations.json`，同一标题可跨日报、周报复用；已有精读中文标题优先使用。不受 `--no-llm` 影响，未启用时完全不联网翻译。

默认每次最多请求 200 篇，翻译时间预算 180 秒；连续失败 3 次后停止请求，未翻译的条目保留英文，下次重生成会重试。配置项：

```yaml
translation:
  enabled: true
  provider: google-web
  max_per_run: 200
  budget_s: 180
```

翻译与版式在下次生成日报时生效；历史日报需点击文献雷达面板的“重生成”。不使用插件时，将 [独立日报样式](assets/litradar-report.css) 复制到 vault 的 `.obsidian/snippets/` 并在“外观 → CSS 代码片段”启用，也可以正常显示。样式只作用于文献雷达日报。

词云直接显示在主题统计下方，统计饼图和计数表仍可折叠；即使没有主题标签命中，也能显示基于标题生成的词云。期刊与预印本分文件时分别生成独立词云，计数只包含当前文件。

Science / Science Advances 优先读官网 RSS；被 HTTP 403 拦截时使用 Crossref 作为备用来源，显示“仅元数据”并保留原始错误原因。两本期刊设置 `date_filter: published`，兼容仅登记纸刊日期的记录；这是 Crossref 的发表日期，不代表已在官网核实，且元数据登记可能延迟。

### 0.4 阅读流程升级

新增单刊重试、失败批次恢复、自动补齐／迟到回查、Crossref 分页、跨批次中文文献库、Zotero 入库／PDF 状态反馈、可撤销的推荐反馈，以及有来源证据的预印本／正式版关联。入口、配置和边界见 [每日阅读与恢复指南](docs/reading-workflow.md)。
