"""Local recommendation feedback and evidence-based paper version links."""

import re

from .model import normalize_doi
from .state import atomic_write_json, read_json
from .util import iso


def tokens(title):
    return set(re.findall(r"[a-z]{4,}", title.lower())) - {
        "with",
        "from",
        "study",
        "analysis",
        "using",
        "based",
        "human",
        "their",
        "that",
        "this",
    }


def feedback(paths, article, value):
    if value not in ("relevant", "irrelevant", "less", "clear"):
        raise ValueError("无效反馈")
    path = paths.state_dir / "feedback.json"
    rows = read_json(path, default={})
    if value == "clear":
        rows.pop(article.id, None)
    else:
        rows[article.id] = {"value": value, "title": article.title, "updated_at": iso()}
    atomic_write_json(path, rows)
    return rows


def apply_feedback(paths, articles):
    rows = read_json(paths.state_dir / "feedback.json", default={})
    for article in articles:
        article.extra.pop("feedback_adjustment", None)
        article.extra.pop("feedback_reason", None)
        direct = rows.get(article.id, {}).get("value")
        if direct:
            article.extra["feedback_adjustment"] = 0.2 if direct == "relevant" else -0.3
            article.extra["feedback_reason"] = (
                "你标记为相关" if direct == "relevant" else "你标记为不相关／减少推荐"
            )
        else:
            words = tokens(article.title)
            for row in rows.values():
                other = tokens(row.get("title", ""))
                if (
                    row["value"] == "less"
                    and len(words & other) >= 3
                    and len(words & other) / max(1, len(words | other)) >= 0.25
                ):
                    article.extra["feedback_adjustment"] = -0.15
                    article.extra["feedback_reason"] = "与你要求减少推荐的主题相似"
                    break


def version_links(paths, articles):
    known = {}
    for path in sorted(paths.store_dir.glob("*.json")):
        for row in read_json(path, default={}).get("items", []):
            doi = str(row.get("doi") or "").lower().removeprefix("https://doi.org/")
            if doi:
                known[doi] = row
    for article in articles:
        if article.doi:
            known[normalize_doi(article.doi)] = article.to_dict()
    links = {}
    for doi, row in known.items():
        extra = row.get("extra") or {}
        relations = extra.get("relations") or {}
        for relation, entries in relations.items():
            if relation not in ("is-preprint-of", "has-preprint", "is-version-of", "has-version"):
                continue
            for entry in entries if isinstance(entries, list) else []:
                target = str(entry.get("id") or "").lower().removeprefix("https://doi.org/")
                if target and target != doi:
                    links.setdefault(doi, []).append(
                        {"doi": target, "relation": relation, "evidence": "Crossref relation"}
                    )
                    links.setdefault(target, []).append(
                        {"doi": doi, "relation": "related-version", "evidence": "Crossref relation"}
                    )
        published = str(extra.get("published_doi") or "").lower().removeprefix("https://doi.org/")
        if published and published not in ("na", "none") and published != doi:
            links.setdefault(doi, []).append(
                {"doi": published, "relation": "is-preprint-of", "evidence": "preprint publisher"}
            )
            links.setdefault(published, []).append(
                {"doi": doi, "relation": "has-preprint", "evidence": "preprint publisher"}
            )
    for article in articles:
        article.extra.pop("versions", None)
        article.extra.pop("superseded_by", None)
        if normalize_doi(article.doi) in links:
            article.extra["versions"] = links[normalize_doi(article.doi)]
            if article.extra.get("preprint"):
                published = [
                    v["doi"]
                    for v in article.extra["versions"]
                    if v["doi"] in known and not (known[v["doi"]].get("extra") or {}).get("preprint")
                ]
                if published:
                    article.extra["superseded_by"] = published[0]
    atomic_write_json(paths.state_dir / "versions.json", links)
    return links


def rebuild_presentations(ctx):
    """Migrate existing stores using local caches only; never overwrite a report."""
    from .pipeline import load_store
    from .render import ReportInput, final_score, select_highlights
    from .translation import translate_titles
    from .validate import Result

    count = 0
    for path in sorted(ctx.paths.store_dir.glob("*.json")):
        store = load_store(ctx.paths, path.stem)
        articles = store.items + store.already_seen
        deep = read_json(ctx.paths.run_dir(store.run_id) / "deepread.json", default={})
        existing = {row["id"]: row["title_zh"] for row in deep.get("papers", []) if row.get("title_zh")}
        titles, _ = translate_titles(ctx.config, ctx.paths, articles, existing=existing, commit=False)
        screen = read_json(ctx.paths.screen_file(store.run_id), default={})
        results = {row["id"]: Result.from_dict(row) for row in screen.get("results", []) if row.get("id")}
        apply_feedback(ctx.paths, articles)
        version_links(ctx.paths, articles)
        data = ReportInput(
            run_id=store.run_id,
            window=store.window,
            scans=store.scans,
            items=store.items,
            results=results,
            config=ctx.config,
        )
        selected = {a.id for a in select_highlights(data)}
        atomic_write_json(
            ctx.paths.run_dir(store.run_id) / "presentation.json",
            {
                "titles": titles,
                "summaries": {key: row.summary_zh for key, row in results.items() if row.summary_zh},
                "why": {key: row.why for key, row in results.items() if row.why},
                "extras": {
                    a.id: {
                        **a.extra,
                        "highlight": a.id in selected,
                        "rank_score": final_score(a, results.get(a.id), None, ctx.config),
                    }
                    for a in articles
                },
            },
        )
        count += len(articles)
    return count
