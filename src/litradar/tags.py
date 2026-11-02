"""Deterministic topic tags, computed from titles (abstract as a fallback).

Tags do three jobs: label every article (not only the ones an LLM saw), feed the
daily topic chart, and give the reader a vocabulary that stays stable across days.
They are deliberately rule-based: a model can add free-form tags, but a chart needs
categories that mean the same thing every run.
"""

from __future__ import annotations

import re
from typing import Any

from .model import Article

# tag -> patterns (case-insensitive). Keep tags short; they become chart labels.
DEFAULT_RULES: dict[str, list[str]] = {
    "基因组学": [r"genom\w*", r"\bgwas\b", r"haplotype", r"variant", r"allele", r"sequencing"],
    "单细胞": [r"single[- ]cell", r"scrna", r"single[- ]nucleus"],
    "机器学习与AI": [
        r"machine learning", r"deep learning", r"neural network", r"language model",
        r"\bllms?\b", r"artificial intelligence", r"\bai\b",
    ],
    "医学影像": [r"imaging", r"\bmri\b", r"\bct\b", r"radiolog\w*", r"pathology"],
    "临床与流行病": [
        r"clinical trial", r"cohort", r"randomi[sz]ed", r"epidemiolog\w*", r"patients?",
    ],
    "基因编辑": [r"crispr", r"base editing", r"prime editing", r"gene editing"],
    "进化与群体遗传": [
        r"evolution\w*", r"selection", r"phyloge\w*", r"population genetics", r"adaptation",
    ],
    "蛋白质与结构": [r"protein", r"cryo-?em", r"alphafold", r"structural"],
    "药物与治疗": [r"drug", r"therap\w*", r"inhibitor", r"treatment"],
    "神经科学": [r"neuro\w*", r"\bbrain\b", r"cortex", r"neuron"],
    "植物与农业": [r"\bplants?\b", r"\bcrops?\b", r"\brice\b", r"\bmaize\b", r"arabidopsis"],
    "微生物与感染": [r"microb\w*", r"bacteri\w*", r"\bvirus\b", r"viral", r"pathogen"],
    "多组学": [r"proteom\w*", r"metabolom\w*", r"epigenom\w*", r"multi-?omic"],
    "空间生物学": [r"spatial transcriptom\w*", r"spatial omics"],
    "免疫": [r"immun\w*", r"interferon", r"\bt cells?\b", r"antibod\w*"],
    "代谢": [r"metabol\w*", r"lipid", r"glucose", r"biofuel", r"photosynth\w*"],
    "心血管": [r"cardio\w*", r"\bheart\b", r"vascular", r"hypertrophy"],
    "发育": [r"development\w*", r"embryo\w*", r"organoid"],
    "癌症": [r"cancer", r"tumou?r", r"oncolog\w*", r"carcinoma"],
    "公共卫生": [r"public health", r"global health", r"health polic\w*", r"epidemic"],
    "健康信息学": [
        r"health informatic\w*", r"electronic health record", r"\behrs?\b",
        r"telemedicine", r"wearable", r"digital health",
    ],
    "生物信息学": [r"bioinformatic\w*", r"computational", r"algorithm", r"pipeline"],
}

MAX_TAGS = 3


def _compile(rules: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {
        tag: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for tag, patterns in rules.items()
    }


def merged_rules(extra: dict[str, Any] | None = None) -> dict[str, list[str]]:
    rules = {tag: list(patterns) for tag, patterns in DEFAULT_RULES.items()}
    for tag, patterns in (extra if isinstance(extra, dict) else {}).items():
        if isinstance(patterns, str):
            patterns = [patterns]
        if isinstance(patterns, list):
            rules.setdefault(str(tag), []).extend(str(pattern) for pattern in patterns)
    return rules


def compiled_rules(extra: dict[str, Any] | None = None) -> dict[str, list[re.Pattern]]:
    return _compile(merged_rules(extra))


def tag_article(article: Article, rules: dict[str, list[re.Pattern]] | None = None) -> list[str]:
    """Tags from the title first; the abstract only fills gaps. Max 3, stable order."""
    compiled = rules or _compile(merged_rules())
    title = article.title or ""
    abstract = article.abstract_raw or ""
    scored: list[tuple[int, int, str]] = []
    for tag, patterns in compiled.items():
        title_hits = sum(1 for pattern in patterns if pattern.search(title))
        abstract_hits = 0 if title_hits else sum(1 for pattern in patterns if pattern.search(abstract))
        if title_hits or abstract_hits:
            scored.append((title_hits, abstract_hits, tag))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [tag for _, _, tag in scored[:MAX_TAGS]]


def topic_counts(items: list[Article]) -> tuple[dict[str, int], int]:
    """(tag -> articles, untagged article count) for one report."""
    counts: dict[str, int] = {}
    untagged = 0
    for article in items:
        tags = (article.extra or {}).get("tags_auto") or []
        if not tags:
            untagged += 1
            continue
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    return counts, untagged
