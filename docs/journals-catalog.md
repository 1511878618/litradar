# 期刊目录（init 默认带的 17 本）

`litradar init` 会把下面这些期刊写进 `.litradar/journals/`。**影响因子只是排序与展示用的示例值**
（JCR 2024 量级，四舍五入），不是权威数据，也不影响抓取；请按自己学校/单位的口径改 YAML 里的
`impact_factor`。

| 期刊 | 适配器 | 数据源 | 影响因子（示例） | 标题等级 | 备注 |
|---|---|---|---|---|---|
| Nature | rss | `nature.com/nature.rss` | 48.5 | 3 | 用 DOI 前缀排除新闻/评论 |
| Science | rss | `science.org` ETOC RSS | 44.7 | 3 | `dc:type` 区分研究/新闻 |
| Nature Methods | rss | `nature.com/nmeth.rss` | 32.1 | 3 | |
| Nature Genetics | rss | `nature.com/ng.rss` | 30.8 | 3 | |
| Nature Biotechnology | rss | `nature.com/nbt.rss` | 29.0 | 3 | |
| Nature Biomedical Engineering | rss | `nature.com/natbiomedeng.rss` | 25.0 | 3 | |
| The Lancet Digital Health | crossref | ISSN `2589-7500` | 19.9 | 3 | Elsevier 只有年月，用 `created` 日期 |
| Nature Machine Intelligence | rss | `nature.com/natmachintell.rss` | 18.9 | 4 | |
| Nature Communications | rss | `nature.com/ncomms.rss` | 15.7 | 4 | |
| npj Digital Medicine | rss | `nature.com/npjdigitalmed.rss` | 15.1 | 4 | |
| Science Advances | rss | `science.org` ETOC RSS | 12.5 | 4 | |
| JAMIA | crossref | ISSN `1067-5027` | 6.0 | 5 | OUP 页面 403，走 Crossref |
| Molecular Biology and Evolution | crossref | ISSN `0737-4038` | 5.3 | 5 | 仅元数据 |
| Journal of Biomedical Informatics | crossref | ISSN `1532-0464` | 4.5 | 5 | Elsevier `created` 日期 |
| PLOS Genetics | rss | PLOS Atom | 4.0 | 5 | |
| bioRxiv | biorxiv | 官方 JSON API | — | 5 | 预印本，永远排最后 |
| medRxiv | biorxiv | 官方 JSON API | — | 5 | 预印本，永远排最后 |

## 排序规则

1. **预印本（`preprint: true`）在日报里单独成节**，不参与期刊的影响因子排序；
2. 期刊按 `impact_factor` 从高到低；
3. 同分按刊名字母序；
4. 正文里每本刊是一个小节，标题层级取该刊的 `heading_level`，条目再深一级
   （最高到 `######`）。

所以 Nature 的论文是 `### 期刊` + `#### 标题`，JAMIA 是 `##### 期刊` + `###### 标题`，
bioRxiv 永远在最后。

## 常用字段

```yaml
impact_factor: 19.9       # 排序 + 覆盖表展示；不写则按 0 处理
heading_level: 3          # 3-6；不写则按 IF 推导（≥20→3，≥12→4，其余 5）
preprint: true            # 预印本：永远排最后，覆盖表 IF 显示为 —
exclude_doi_prefixes: ["10.1038/d"]   # 命中前缀的条目归为“非研究类型”
date_filter: created      # 仅 crossref：Elsevier 只有年月时用收录日
image_page: ".full"       # 配图抓取页（预印本）
```

## 改哪本、换哪本

- 不要某本刊：删掉对应的 `journals/<slug>.yaml`；
- 换影响因子：改 `impact_factor` 即可，不用动代码；
- 调整层级：改 `heading_level`（3–6）；
- 想加一本不在列表里的：见 [journals.md](journals.md)，或复制
  [`examples/journals/_template.yaml`](../examples/journals/_template.yaml)。
