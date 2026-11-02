"""Configuration: one ``config.yaml`` plus one ``journals/<slug>.yaml`` per journal.

Adding a journal must never require touching engine code, so everything
adapter-specific lives in those small files.  ``config.yaml`` keeps sane defaults
for every optional feature (LLM, embedding, Zotero, scheduling).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from .paths import Paths
from .util import ConfigError, collapse, slugify

ADAPTER_NAMES = ("rss", "crossref", "html", "biorxiv", "browser", "manual", "custom")

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "vault": "",
    "direction": "",
    "language": "zh-CN",
    "output": {
        "report_dir": "02-Reading/文献雷达/{yyyy}/{MM}",
        "filename": "{yyyy}-{MM}-{dd}",
        "index_file": "02-Reading/文献雷达/文献雷达索引.md",
        "index_mode": "append",
        "betternote_dir": "",
        "betternote_prefix": "@",
    },
    "schedule": {
        "kind": "daily",
        "time": "09:00",
        "tz": "Asia/Shanghai",
        "window": "yesterday",
    },
    "report": {
        "highlights": 8,
        "abstract_chars": 1500,
        "topic_chart": True,
        "wordcloud": True,
    },
    "images": {
        "enabled": False,
        "source": "auto",
        "scope": "highlights",
        "download": True,
        "max_per_run": 12,
        "max_bytes": 2000000,
    },
    "screening": {
        "mode": "llm",
        "min_relevance": 0.6,
        "max_items": 60,
        "include_types": ["research-article", "review"],
        "keywords": [
            "positive selection",
            "genome-wide association",
            "self-incompatibility",
            "gene family expansion",
            "population genomics",
        ],
    },
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "",
        "api_key_file": "",
        "api_key_env": "LITRADAR_API_KEY",
        "timeout_s": 120,
        "max_retries": 2,
        "max_abstract_chars": 1200,
        "json_mode": True,
    },
    "embedding": {
        "enabled": False,
        "backend": "local",
        "model": "BAAI/bge-m3",
        "profile": "text:",
        "cache_dir": ".litradar/vectors",
        "top_k": 8,
    },
    "zotero": {"api": "http://127.0.0.1:23119", "enabled": False},
    "tags": {"extra": {}},
    "http": {
        "timeout_s": 25,
        "retries": 2,
        "rate_limit_s": 2,
        "user_agent": "litradar/2.0 (personal research use)",
        "cache_ttl_hours": 12,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, data: dict[str, Any], vault: Path | str, path: Path | None = None) -> None:
        self.raw = data
        self.vault = Path(vault).expanduser().resolve()
        self.path = path

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
            if node is None:
                return default
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def direction(self) -> str:
        return collapse(str(self.get("direction", "")))

    @property
    def tz(self) -> str:
        return str(self.get("schedule.tz", "Asia/Shanghai") or "Asia/Shanghai")


def load_config(vault: Path | str, path: Path | None = None, require: bool = True) -> Config:
    vault_path = Path(vault).expanduser()
    config_path = Path(path) if path else Paths(vault_path).config_file
    if not config_path.exists():
        if require:
            raise ConfigError(
                f"找不到配置文件 {config_path}。先运行 `litradar init --vault {vault_path}`。"
            )
        return Config(copy.deepcopy(DEFAULTS), vault_path, None)
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} 不是合法 YAML：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取 {config_path}：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{config_path} 顶层必须是映射（mapping）")
    return Config(deep_merge(DEFAULTS, loaded), vault_path, config_path)


def _require(journal: dict, key: str, source: str) -> Any:
    value = journal.get(key)
    if value in (None, "", []):
        raise ConfigError(f"{source}: 缺少必填字段 {key!r}")
    return value


def validate_journal(journal: dict, source: str = "<inline>") -> dict:
    if not isinstance(journal, dict):
        raise ConfigError(f"{source}: 期刊定义必须是映射")
    name = collapse(str(journal.get("name") or ""))
    if not name:
        raise ConfigError(f"{source}: 缺少期刊名 name")
    adapter = str(journal.get("adapter") or "").strip().lower()
    if adapter not in ADAPTER_NAMES:
        raise ConfigError(
            f"{source}（{name}）: 未知 adapter {adapter!r}，可用：{', '.join(ADAPTER_NAMES)}"
        )
    if adapter == "rss" and not (journal.get("feed") or journal.get("url")):
        raise ConfigError(f"{source}（{name}）: rss 适配器需要 feed 或 url")
    if adapter == "html":
        _require(journal, "url", source)
        selectors = journal.get("selectors")
        if not isinstance(selectors, dict) or not selectors.get("item"):
            raise ConfigError(f"{source}（{name}）: html 适配器需要 selectors.item")
    if adapter == "crossref":
        _require(journal, "issn", source)
    if adapter == "biorxiv":
        server = str(journal.get("server") or "biorxiv").strip().lower()
        if server not in ("biorxiv", "medrxiv"):
            raise ConfigError(f"{source}（{name}）: server 只能是 biorxiv 或 medrxiv")
        journal = dict(journal)
        journal["server"] = server
    if adapter == "custom":
        _require(journal, "module", source)
    if adapter == "manual":
        if not journal.get("items") and not journal.get("file"):
            raise ConfigError(f"{source}（{name}）: manual 适配器需要 items 或 file")
    prefixes = journal.get("exclude_doi_prefixes")
    if prefixes not in (None, "") and not isinstance(prefixes, list):
        raise ConfigError(f"{source}（{name}）: exclude_doi_prefixes 必须是列表")
    if journal.get("date_filter") not in (None, "", "online", "created"):
        raise ConfigError(f"{source}（{name}）: date_filter 只能是 online 或 created")
    if journal.get("impact_factor") not in (None, ""):
        try:
            if float(journal["impact_factor"]) < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigError(f"{source}（{name}）: impact_factor 必须是非负数字") from None
    if journal.get("heading_level") not in (None, ""):
        try:
            level = int(journal["heading_level"])
        except (TypeError, ValueError):
            raise ConfigError(f"{source}（{name}）: heading_level 必须是 3-6 的整数") from None
        if not 3 <= level <= 6:
            raise ConfigError(f"{source}（{name}）: heading_level 必须是 3-6 的整数")
    journal = dict(journal)
    journal["name"] = name
    journal["adapter"] = adapter
    journal.setdefault("surface", None)
    journal["slug"] = journal.get("slug") or slugify(name)
    return journal


def load_journals(config: Config) -> list[dict]:
    """Inline entries from config.yaml first, then ``journals/*.yaml`` (files win)."""
    inline = config.raw.get("journals") or []
    if not isinstance(inline, list):
        raise ConfigError("config.yaml 的 journals 必须是列表")

    ordered: list[dict] = []
    index: dict[str, int] = {}

    def upsert(entry: dict, source: str) -> None:
        validated = validate_journal(entry, source)
        key = validated["name"].casefold()
        if key in index:
            ordered[index[key]] = validated
        else:
            index[key] = len(ordered)
            ordered.append(validated)

    for entry in inline:
        upsert(entry, "config.yaml:journals")

    journals_dir = Paths(config.vault).journals_dir
    if journals_dir.exists():
        for path in sorted(journals_dir.glob("*.yaml")) + sorted(journals_dir.glob("*.yml")):
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(f"{path}: 不是合法 YAML：{exc}") from exc
            upsert(loaded, str(path))

    if not ordered:
        raise ConfigError(
            "没有配置任何期刊。在 .litradar/journals/ 下加一个 yaml，或运行 litradar init。"
        )
    return sort_journals(ordered)


def sort_journals(journals: list[dict]) -> list[dict]:
    """Impact factor descending; preprint servers last, then by name."""

    def key(journal: dict) -> tuple:
        if journal.get("preprint"):
            return (1, 0.0, journal["name"])
        try:
            factor = float(journal.get("impact_factor") or 0)
        except (TypeError, ValueError):
            factor = 0.0
        return (0, -factor, journal["name"])

    return sorted(journals, key=key)


def find_journal(journals: list[dict], name: str) -> dict:
    needle = collapse(name).casefold()
    for journal in journals:
        if journal["name"].casefold() == needle or journal.get("slug") == needle:
            return journal
    raise ConfigError(f"没有名为 {name!r} 的期刊；用 `litradar journals list` 查看")


CONFIG_TEMPLATE = """\
# litradar configuration — https://github.com/wanglab/litradar
version: 1
vault: {vault}

# 研究方向写人话即可；模型用它判断相关性，关键词兜底也从这里取默认值。
direction: "{direction}"
language: {language}

output:
  report_dir: "02-Reading/文献雷达/{{yyyy}}/{{MM}}"
  filename: "{{yyyy}}-{{MM}}-{{dd}}"
  index_file: "02-Reading/文献雷达/文献雷达索引.md"
  index_mode: append            # append | dataview | none
  betternote_dir: ""            # 填 "02-Reading/4-betternotes" 则给置顶条目建 @ 笔记
  betternote_prefix: "@"

schedule:
  kind: daily
  time: "09:00"
  tz: Asia/Shanghai
  window: yesterday

report:
  highlights: 8                 # 置顶上限；正文永远全量
  abstract_chars: 1500
  topic_chart: true             # 今日主题分布（Mermaid 饼图 + 计数表）
  wordcloud: true               # 标题词云（SVG，存到 assets/）

tags:
  extra: {{}}                   # 自定义标签，例如 {{"空间转录组": ["spatial transcriptomics"]}}

images:
  enabled: true                 # 给置顶文章配图（失败自动跳过，不影响日报）
  source: auto                  # auto | first-figure | cover
  scope: highlights             # highlights（推荐）| all
  download: true                # 存到日报同级的 assets/，离线可看
  max_per_run: 12
  max_bytes: 2000000

screening:
  mode: llm                     # llm | keyword
  min_relevance: 0.6
  max_items: 60                 # 单次喂模型上限，超出按日期倒序截断并标注
  include_types: [research-article, review]
  keywords:                     # 关键词兜底必须用英文（匹配英文摘要）
    - positive selection
    - genome-wide association
    - self-incompatibility
    - gene family expansion
    - population genomics

llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"
  # 三选一（优先级从上到下）：
  #   api_key      直接写在配置里（最方便；vault 若同步会带走 key，注意权限）
  #   api_key_file 指向 vault 外的只读文件，如 "~/.config/litradar/deepseek.key"
  #   api_key_env  读环境变量（最安全，计划任务需自行注入）
  api_key: ""
  api_key_file: ""
  api_key_env: "LITRADAR_API_KEY"
  timeout_s: 120
  max_retries: 2
  max_abstract_chars: 1200
  json_mode: true               # 模型不支持 response_format 时设为 false

embedding:
  enabled: false                # 需要 pip install "litradar[embed]"
  backend: local                # local | api
  model: "BAAI/bge-m3"
  profile: "text:"              # zotero:collection/<名称> | tags:#标签 | text:<方向>
  cache_dir: ".litradar/vectors"
  top_k: 8

zotero:
  api: "http://127.0.0.1:23119"
  enabled: false

http:
  timeout_s: 25
  retries: 2
  rate_limit_s: 2
  user_agent: "litradar/2.0 (personal research use)"
  cache_ttl_hours: 12
"""

SAMPLE_JOURNALS: tuple[dict[str, Any], ...] = (
    {
        "name": "Nature",
        "adapter": "rss",
        "feed": "https://www.nature.com/nature.rss",
        "surface": "Latest Research",
        "issn": "0028-0836",
        "impact_factor": 48.5,
        "heading_level": 3,
    },
    {
        "name": "Science",
        "adapter": "rss",
        "feed": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science",
        "surface": "Current Issue",
        "issn": "0036-8075",
        "impact_factor": 44.7,
        "heading_level": 3,
    },
    {
        "name": "Nature Methods",
        "adapter": "rss",
        "feed": "https://www.nature.com/nmeth.rss",
        "surface": "Latest Research",
        "issn": "1548-7091",
        "impact_factor": 32.1,
        "heading_level": 3,
    },
    {
        "name": "Nature Genetics",
        "adapter": "rss",
        "feed": "https://www.nature.com/ng.rss",
        "surface": "Latest Research",
        "issn": "1061-4036",
        "impact_factor": 30.8,
        "heading_level": 3,
    },
    {
        "name": "Nature Biotechnology",
        "adapter": "rss",
        "feed": "https://www.nature.com/nbt.rss",
        "surface": "Latest Research",
        "issn": "1087-0156",
        "impact_factor": 29.0,
        "heading_level": 3,
    },
    {
        "name": "Nature Biomedical Engineering",
        "adapter": "rss",
        "feed": "https://www.nature.com/natbiomedeng.rss",
        "surface": "Latest Research",
        "issn": "2157-846X",
        "impact_factor": 25.0,
        "heading_level": 3,
    },
    {
        "name": "The Lancet Digital Health",
        "adapter": "crossref",
        "issn": "2589-7500",
        "surface": "Online First",
        "date_filter": "created",
        "impact_factor": 19.9,
        "heading_level": 3,
    },
    {
        "name": "Nature Machine Intelligence",
        "adapter": "rss",
        "feed": "https://www.nature.com/natmachintell.rss",
        "surface": "Latest Research",
        "issn": "2522-5839",
        "impact_factor": 18.9,
        "heading_level": 4,
    },
    {
        "name": "Nature Communications",
        "adapter": "rss",
        "feed": "https://www.nature.com/ncomms.rss",
        "surface": "Latest Research",
        "issn": "2041-1723",
        "impact_factor": 15.7,
        "heading_level": 4,
    },
    {
        "name": "npj Digital Medicine",
        "adapter": "rss",
        "feed": "https://www.nature.com/npjdigitalmed.rss",
        "surface": "Latest Research",
        "issn": "2398-6352",
        "impact_factor": 15.1,
        "heading_level": 4,
    },
    {
        "name": "Science Advances",
        "adapter": "rss",
        "feed": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciadv",
        "surface": "Current Issue",
        "issn": "2375-2548",
        "impact_factor": 12.5,
        "heading_level": 4,
    },
    {
        "name": "JAMIA",
        "adapter": "crossref",
        "issn": "1067-5027",
        "surface": "Advance Access",
        "impact_factor": 6.0,
        "heading_level": 5,
    },
    {
        "name": "Molecular Biology and Evolution",
        "adapter": "crossref",
        "issn": "0737-4038",
        "surface": "Advance Access",
        "impact_factor": 5.3,
        "heading_level": 5,
    },
    {
        "name": "Journal of Biomedical Informatics",
        "adapter": "crossref",
        "issn": "1532-0464",
        "surface": "Articles in Press",
        "date_filter": "created",
        "impact_factor": 4.5,
        "heading_level": 5,
    },
    {
        "name": "PLOS Genetics",
        "adapter": "rss",
        "feed": "https://journals.plos.org/plosgenetics/feed/atom",
        "surface": "New Articles",
        "issn": "1553-7404",
        "impact_factor": 4.0,
        "heading_level": 5,
    },
    {
        "name": "bioRxiv",
        "adapter": "biorxiv",
        "server": "biorxiv",
        "surface": "Preprints",
        "image_page": ".full",
        "preprint": True,
        "heading_level": 3,
    },
    {
        "name": "medRxiv",
        "adapter": "biorxiv",
        "server": "medrxiv",
        "surface": "Preprints",
        "image_page": ".full",
        "preprint": True,
        "heading_level": 3,
    },
)

# Nature's front-half content (news, careers, News & Views) shares the site but uses
# DOIs under 10.1038/d…; research articles are 10.1038/s….  Excluding the prefix keeps
# "Patrick Stewart dies" out of the research digest while still auditing it.
_NATURE_FAMILY = {
    "Nature",
    "Nature Methods",
    "Nature Genetics",
    "Nature Biotechnology",
    "Nature Biomedical Engineering",
    "Nature Machine Intelligence",
    "Nature Communications",
    "npj Digital Medicine",
}
SAMPLE_JOURNALS = tuple(
    {**journal, "exclude_doi_prefixes": ["10.1038/d"]}
    if journal["name"] in _NATURE_FAMILY
    else journal
    for journal in SAMPLE_JOURNALS
)


def write_skeleton(vault: Path | str, direction: str = "") -> Paths:
    vault_path = Path(vault).expanduser().resolve()
    paths = Paths(vault_path)
    paths.ensure()
    if not paths.config_file.exists():
        text = CONFIG_TEMPLATE.format(
            vault=json.dumps(str(vault_path)),
            direction=(direction or "分子进化；群体基因组学；基因家族扩张").replace('"', "'"),
            language="zh-CN",
        )
        paths.config_file.write_text(text, encoding="utf-8")
    existing = {path.stem for path in paths.journals_dir.glob("*.y*ml")}
    for sample in SAMPLE_JOURNALS:
        slug = slugify(sample["name"])
        if slug in existing:
            continue
        (paths.journals_dir / f"{slug}.yaml").write_text(
            yaml.safe_dump(sample, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    readme = paths.journals_dir / "README.md"
    if not readme.exists():
        readme.write_text(JOURNALS_README, encoding="utf-8")
    for directory, content in ((paths.root, ROOT_README), (paths.state_dir, STATE_README)):
        target = directory / "README.md"
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    return paths


ROOT_README = """\
# .litradar/ 是什么

litradar 的全部状态都在这一个目录里。它跟着 vault 走，可以同步、可以备份、可以审。

| 路径 | 谁写 | 说明 |
|---|---|---|
| `config.yaml` | 你 | 研究方向、输出路径、LLM、embedding、Zotero |
| `journals/*.yaml` | 你 | 一本刊一个文件；加刊不需要改代码 |
| `store/<date>.json` | 引擎 | 某天的原始抓取结果（含被排除的条目），审计用 |
| `runs/<date>/` | 引擎 | `screen.json`、`scores.json`、`report.md`、`run.log` |
| `state/` | 引擎 | 去重账本与每刊扫描历史，**不要手改** |
| `cache/` | 引擎 | HTTP 缓存，可随时删 |
| `logs/` | 引擎 | CLI 日志，自动保留 7 天 |
| `vectors/` | 引擎 | embedding 向量缓存，删掉即全量重算 |
| `.lock` | 引擎 | 并发锁；删除无效，重启进程即可 |

## 不要做的事

- 不要手改 `state/seen.json`：它是"哪些文章已经报告过"的唯一账本，
  改坏会导致永久漏报或重复报告；
- 不要把 API key 写进 `config.yaml`（只写环境变量名）；
- 不要手工编辑 `store/` 与 `runs/`（会被覆盖，且破坏审计链）。
"""

STATE_README = """\
# state/ —— DO NOT EDIT

这两个文件是引擎的内部状态，手工修改会破坏去重：

- `seen.json`：每篇已报告文章一条记录。`first_seen_at` 永不覆盖，
  `last_reported_at` 只在渲染成功后才更新。改它 = 永久漏报或重复报告。
- `journal_scans.json`：每刊最近一次扫描与 `consecutive_failures`，
  前端用它做"连续 3 天失败"提醒。

想重置：删掉整个 `.litradar/state/` 目录（下次运行会重新开始记账，
旧日报不会被删除，但那一天之前的文章可能被再次报告）。
"""

JOURNALS_README = """\
# 期刊配置

每个 `*.yaml` 是一本刊。加一刊 = 加一个文件 + 跑 `litradar journals test "<刊名>"`。

## 三种常见写法

```yaml
# A. RSS / Atom / RDF（首选）
name: Nature Genetics
adapter: rss
feed: https://www.nature.com/ng.rss
surface: Latest Research
issn: "1061-4036"
```

```yaml
# B. HTML 栏目页 + CSS 选择器（无 RSS 时）
name: The Plant Cell
adapter: html
url: https://academic.oup.com/plcell/advance-articles
base_url: https://academic.oup.com
surface: Advance Articles
selectors:
  item: "li.al-article-item"
  title: "h4 a"
  link: "h4 a::attr(href)"
  doi: "span.al-doi"
  date: "span.al-date"
  abstract: "div.al-abstract"
fallback: [crossref]
issn: "1040-4651"
```

```yaml
# C. Crossref 兜底（只有元数据，会标 metadata-only）
name: Molecular Biology and Evolution
adapter: crossref
issn: "0737-4038"
surface: Advance Access
mailto: you@example.com
```

```yaml
# D. 预印本服务器（bioRxiv / medRxiv）
name: bioRxiv
adapter: biorxiv
server: biorxiv          # biorxiv | medrxiv
surface: Preprints
image_page: ".full"      # 配图要正文页；摘要页只有站点 logo
# categories: [genomics, evolutionary biology]   # 可选：收窄分类
# max_items: 300                                  # 可选：单刊上限
```

## 校验

```
litradar journals list
litradar journals test "Nature Genetics"   # 必须看到 title / date / doi
litradar doctor
```

日期全空或 DOI 全空 = 选择器/字段错了，不要就这样上线。
"""
