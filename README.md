# Litradar · 文献雷达

> 每天一份**可审计**的文献日报：期刊与预印本全量收录，相关性只决定置顶，不决定取舍。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-288%20passed-brightgreen)](tests/run_tests.py)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0.0-orange)](CHANGELOG.md)

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
| **抓取失败 ≠ 没有新文章** | 每刊五态 `ok / metadata-only / blocked / timeout / error`；失败进覆盖表并明确标注 |
| **每条模型断言都可证伪** | `id` 必须命中候选、verbatim 摘要相似度 ≥ 0.90、结论必须带白名单来源，违反即降级并写进「校验记录」 |

名字来自 radar：**扫描、覆盖，以及「没有回波」和「没有目标」是两件事。**

## 功能

- **一份日报，两类内容**：`2026-11-01.md` 内含「期刊」与「预印本（bioRxiv / medRxiv）」两节，Obsidian 日记只需管一个文件；
- **17 本内置期刊**：Nature / Science 系列、Nature Communications、npj Digital Medicine、The Lancet Digital Health、JAMIA、JBI、PLOS Genetics、MBE、bioRxiv、medRxiv…（[完整目录](docs/journals-catalog.md)）；
- **按影响因子排序、按刊设定标题层级**（可在每刊 YAML 里改）；
- **每篇文章自动打标签**（22 个受控主题），日报带 **Mermaid 主题分布图** + **SVG 词云**；
- **置顶文章一句话解读**（LLM `summary_zh`），可选配图（第一张图 / `og:image` / 期刊封面）；
- **七条模型输出校验**：不信任模型的自述，宁肯降级也不编；
- **全命令 `--jsonl`**：stdout 每行一个 JSON 事件，人类日志走 stderr；
- **幂等 + 文件锁**：同一天重跑不会覆盖已有日报，并发出退出码 6 而不是并发写坏状态；
- **跨平台定时**：Windows 计划任务 / macOS launchd / Linux cron，默认只打印命令；
- **可选增强**：LLM 置顶、本地 bge-m3 语义排序、Zotero 一键入库；
- **可选 Obsidian 插件**：只读面板 + 触发按钮，删掉它日报照常生成。

## 快速开始

需要 Python ≥ 3.10。

```bash
# PyPI 发布前，用源码安装；发布后可直接 pipx install litradar
git clone https://github.com/1511878618/litradar
pipx install ./litradar

mkdir -p ~/my-vault && cd ~/my-vault
litradar init --vault .            # 生成 .litradar/ 与 17 本示例刊
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
```

```bash
litradar run --vault . --llm
litradar status --vault .          # 上次抓取、三态覆盖
```

没有 API key、模型超时、模型返回垃圾 JSON——日报都会照常生成，只是置顶退化为关键词。

## 输出长什么样

```
02-Reading/文献雷达/2026/11/
├── 2026-11-01.md          # 每天唯一一份（期刊 + 预印本）
└── assets/
    ├── wordcloud-2026-11-01-daily.svg
    └── <figure>.png
```

日报结构固定：

- front-matter：`new_items` / `highlights` / `coverage` / `screening` / `journal_items` / `preprint_items`；
- 置顶段（跨期刊 + 预印本统一排序，带一句话解读）；
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
```

适配器：`rss` / `html` / `crossref` / `biorxiv` / `manual` / `custom`，支持 `fallback` 降级链。
新增一刊的完整 SOP（选源、调选择器、处理 403）：[docs/journals.md](docs/journals.md)。

> 预印本单独成节、不参与期刊的影响因子排序；Nature 系的新闻/评论按 DOI 前缀过滤，Science 按 feed 的 `dc:type` 过滤，被排除的仍会出现在「非研究类型」审计段。

## 定时

```bash
litradar schedule install --vault ~/my-vault          # 只打印命令
litradar schedule install --vault ~/my-vault --apply  # 真正注册
```

默认每天 09:00 跑「昨天」整天，并带 `--catch-up --max-days 3`：关机错过的日期，第二天补跑。

计划任务不继承 shell 环境；key 推荐放 `~/.config/litradar/env`（`chmod 600`），生成的 wrapper 会自动 source。

## 命令

| 命令 | 作用 |
|---|---|
| `init` | 建 `.litradar/` 骨架（配置 + 17 本示例刊 + 说明） |
| `fetch` | 抓取 → 日期窗 → 去重 → `store/<date>.json` |
| `render` | 读 store → [LLM] → 校验 → 日报 + 索引 |
| `run` | `fetch` + `render`（`--catch-up` 补跑漏掉的日期） |
| `embed` | 语义相关度 → `runs/<date>/scores.json` |
| `screen` | 校验外部写入的 `screen.json`（`--strict` → 退出码 3） |
| `status` / `doctor` | 覆盖情况 / 依赖、路径、每刊可达性（`--json`） |
| `journals list\|add\|test` | 期刊配置与试跑 |
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
- 设置：Python 解释器绝对路径、vault 根目录、轮询间隔、最低引擎版本；
- 注意：插件仅桌面端可用（`isDesktopOnly: true`）。

## 开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python tests/run_tests.py     # 288 个用例，自包含，不需要 pytest、不联网
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
- 影响因子是**排序用的示例值**（JCR 量级），不是权威数据，按自己单位口径改 YAML；
- 主题标签是规则匹配，冷门方向可能漏标（未标出的会统计为「未打标签」，不会假装覆盖）；
- 时间窗严格：9 点之后才上线的「昨天」文章会落到窗外，可用 `--window last_n_days --days 2` 兜住；
- arXiv 未接入（预印本目前是 bioRxiv / medRxiv）。

## 许可

MIT，见 [LICENSE](LICENSE)。图片与摘要版权归原出版方，仅用于个人阅读。
# litradar
# litradar
# litradar
# litradar
# litradar
# litradar
# litradar
