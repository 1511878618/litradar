# Litradar

> One auditable daily digest of new journal articles and preprints. Ranking only
> decides what gets highlighted — never what gets included.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-384%20passed-brightgreen)](tests/run_tests.py)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.2.0-orange)](CHANGELOG.md)

[中文说明](README.md) · [Sample report](examples/sample-report.md) · [Journal catalog](docs/journals-catalog.md)

---

## What's new in 2.2.0

Paired with Obsidian plugin 0.4.0 and Zotero Bridge 0.2.0: targeted retries that preserve batches, bounded backfill and late-arrival checks, paginated Crossref queries, cached Chinese titles, a cross-batch library, Zotero PDF status feedback, reversible ranking feedback, and evidence-based preprint/publication links. Schema 2 remains compatible.

See the [reading and recovery guide](docs/reading-workflow.md) and [Zotero setup](docs/zotero-setup.md). Website blocking, metadata delays, missing translations and unavailable full text remain visible rather than being treated as successful coverage.

## Why

Most "new papers" tools lie in three ways: a failed fetch becomes "no new articles",
a model silently hides what it thinks is irrelevant, and summaries/claims are invented.

Litradar treats those as design constraints:

| Guarantee | How |
|---|---|
| **Ranking never filters** | the body always lists every candidate in the window; the model only orders highlights and explains why |
| **A failed fetch is not an empty day** | six states per journal (`ok / metadata-only / off-cycle / blocked / timeout / error`); failures and non-publishing days are shown in the coverage table |
| **Every model claim is falsifiable** | ids must exist, "verbatim" abstracts need ≥ 0.90 similarity, conclusions need a whitelisted source URL — otherwise the field is downgraded and logged |

The name comes from radar: scanning, coverage, and the difference between
**no echo** and **no target**.

## Features

- **One daily file**, with **journals** and **preprints** (bioRxiv / medRxiv) as separate sections — Obsidian daily notes only need one path;
- **Research profile from Zotero**: pick any number of collections and let the LLM synthesize a profile (falls back to keyword extraction without a key); stored under `.litradar/profiles/`;
- **Optional two-file mode**: `output.split_preprints: true` writes `2026-11-01.md` and a linked `2026-11-01-preprint.md`;
- **21 journals built in** (Nature / Science / **Cell** and their titles, Nature Communications, npj Digital Medicine, The Lancet Digital Health, JAMIA, JBI, PLOS Genetics, MBE, preprints…);
- **Impact-factor ordering** and per-journal heading levels, editable per YAML;
- **Automatic topic tags** for every article, a **Mermaid topic chart** and an **SVG word cloud**;
- **LLM keyword extraction + deterministic keyword relevance**: the model only extracts 3–6 terms; matching against your keyword list is computed by the engine (`screening.keyword_weight`);
- **Chinese deep-read for highlights**: translated title, field, a 2–4 sentence digest and keywords (LLM, validated per item, can be turned off);
- **Per-journal cadence**: weekly journals show "🛌 not a publishing day" outside their weekday instead of pretending "no new articles";
- **Friday weekly recap**: merges this week's (Mon–today) daily reports into `2026-W45.md` with "this week's trends" — each trend must cite at least two of the week's papers or it is dropped;
- Optional figures (first figure / `og:image` / cover);
- **Seven validation rules** for model output — degrade, never invent;
- **`--jsonl` on every command**: one JSON event per line on stdout, human logs on stderr;
- **Idempotent + file lock**: re-running a day never overwrites a good report; concurrent runs exit 6;
- **Cross-platform scheduling**: Task Scheduler / launchd / cron, dry-run by default; the Friday recap rides the same daily job;
- **Optional**: LLM ranking, local bge-m3 embeddings, Zotero push, Obsidian plugin.

## Zotero integration setup (optional)

Follow the [Zotero setup guide (Chinese)](docs/zotero-setup.md). Enable the Zotero 10 local API, install **Reading List** for reading-state integration, and upgrade the companion to **Litradar Zotero Bridge 0.2.0** (same add-on ID as PDF Bridge). The bridge is required for reading-state writes and native PDF download requests; saving items and opening existing PDFs use Zotero's native API. Imports default to `Litradar 文献雷达 / YYYY-MM-DD` collections (report date, or the configured local date when no report is selected). Existing items retain prior collection memberships; a manually selected destination overrides daily archiving.

Litradar uses Reading List's real state and custom status choices. Updates in Litradar are saved through Reading List; updates in Zotero appear after the next Litradar refresh. Legacy tags and local records can be explicitly migrated while preserving existing Reading List statuses.

## Quick start

Python ≥ 3.10.

Install into your **conda base environment** (the repo no longer ships a venv):

```bash
conda activate base
pip install -e /path/to/litradar   # or `pip install litradar` once published

mkdir -p ~/my-vault && cd ~/my-vault
litradar init --vault .
$EDITOR .litradar/config.yaml        # set `direction`
litradar doctor --vault .
litradar run --vault . --no-llm      # works without any model
```

Add a model (DeepSeek flash example, full snippet in
[examples/llm-deepseek-flash.yaml](examples/llm-deepseek-flash.yaml)):

```yaml
llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-flash"
  api_key: "sk-..."                  # or api_key_file / api_key_env
screening:
  mode: llm
  max_items: 120
deepread:                            # Chinese annotations for the top-N highlights
  enabled: true
  max_items: 8
weekly:                              # one recap every Friday
  enabled: true
  weekday: fri
```

```bash
litradar run --vault . --llm
```

If the key is missing, the model times out, or the output is junk, the digest is
still written — highlights fall back to keywords.

## Output

```
02-Reading/文献雷达/2026/11/
├── 2026-11-01.md          # the only daily file (journals + preprints)
└── assets/
    ├── wordcloud-2026-11-01-daily.svg
    └── <figure>.png
```

Sections: highlights (with one-line interpretation) → topic chart/word cloud →
`## 期刊` → `## 预印本` → coverage tables → audit sections (out of window,
unverified date, excluded types, already reported, validation log).

See [examples/sample-report.md](examples/sample-report.md).

## Journals

`litradar init` writes 17 working configs. Adding one is a YAML file:

```yaml
name: Nature Genetics
adapter: rss
feed: https://www.nature.com/ng.rss
surface: Latest Research
issn: "1061-4036"
impact_factor: 30.8
heading_level: 3
```

Adapters: `rss` / `html` / `crossref` / `biorxiv` / `manual` / `custom`, with
`fallback` chains. Full SOP: [docs/journals.md](docs/journals.md).

## Obsidian plugin (optional)

`obsidian-litradar/` is a thin read-only front-end: it reads `.litradar` JSON and the
digest, spawns the engine, and parses `--jsonl`. Delete it and the scheduled digest
still appears. Desktop only.

## Scheduling

```bash
litradar schedule install --vault ~/my-vault          # print commands
litradar schedule install --vault ~/my-vault --apply  # register
```

Daily at 09:00 for *yesterday*, with a three-day catch-up pass. Secrets for
unattended runs go in `~/.config/litradar/env` (sourced by the wrapper).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected error |
| 2 | configuration error |
| 3 | strict `screen.json` validation failed |
| 4 | **all journals failed** — never render "no new articles" |
| 5 | doctor problems / optional extra missing |
| 6 | another run holds the lock |
| 7 | timeout / cancelled |

## Design

```
front-end (replaceable)  scheduler / Obsidian plugin / CLI
        │  reads and writes vault files, spawns the engine
data      .litradar/{config,journals,store,runs,state} + daily digest
        │
engine (Python, deterministic, headless)  adapters → window → dedupe → [LLM] → validate → render
```

The only test that matters: **delete the plugin — does the digest still appear?**

## Development

```bash
pip install -e ".[dev]"
python tests/run_tests.py     # 288 self-contained tests, no pytest, no network
ruff check src tests
cd obsidian-litradar && pnpm install && pnpm run build
```

## Limitations

- Login-heavy / bot-protected sites (OUP, PNAS, Elsevier) fall back to Crossref metadata only;
- Science feeds expose ~60-character teasers, so ranking leans on titles there;
- Preprint full-text pages sit behind Cloudflare, so **preprint figures are often unavailable**;
- Impact factors are editable ordering hints, not authoritative data;
- Topic tags are rule-based and can miss niche work (untagged counts are reported, not hidden);
- arXiv is not supported yet (bioRxiv / medRxiv are).

MIT licensed.
