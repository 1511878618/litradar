"""Screening: prompt shape, keyword fallback, tolerant result extraction."""

import json

from helpers import sample_article

from litradar.screening import (
    SYSTEM_PROMPT,
    build_messages,
    build_payload,
    extract_results,
    keyword_hits,
    keyword_screen,
)


def test_keyword_screen_threshold():
    article = sample_article(abstract_raw="We study positive selection in plants.")
    results = keyword_screen([article], ["positive selection"], min_relevance=0.6)
    assert results[0]["relevant"] is True
    assert results[0]["relevance"] >= 0.6

    results = keyword_screen([article], ["positive selection"], min_relevance=0.99)
    assert results[0]["relevant"] is False


def test_keyword_screen_no_hits():
    article = sample_article(
        title="Mushroom pigments",
        abstract_raw="We study the biosynthesis of pigments in basidiomycetes.",
    )
    results = keyword_screen([article], ["positive selection"])
    assert results[0]["relevant"] is False
    assert results[0]["relevance"] == 0.0
    assert results[0]["why"] is None


def test_keyword_hits_title_weighting():
    article = sample_article(title="Positive selection in apes", abstract_raw="Nothing here.")
    results = keyword_screen([article], ["positive selection"])
    assert results[0]["relevant"] is True


def test_keyword_hits_finds_phrase():
    article = sample_article(abstract_raw="A genome-wide association study of height.")
    assert keyword_hits(article, ["genome-wide association"]) == ["genome-wide association"]


def test_build_payload_shape():
    article = sample_article(abstract_raw="x" * 5000)
    payload = build_payload("方向", ["J"], [article], max_abstract_chars=100)
    assert len(payload["papers"][0]["abstract"]) <= 100
    assert payload["papers"][0]["id"] == article.id
    assert payload["direction"] == "方向"


def test_build_messages_includes_system_rules():
    article = sample_article()
    messages = build_messages("方向", ["J"], [article])
    assert messages[0]["role"] == "system"
    assert "results 的长度必须等于 papers 的长度" in messages[0]["content"]
    assert article.id in messages[1]["content"]
    assert "总体趋势" in SYSTEM_PROMPT


def test_extract_results_variants():
    entry = {"id": "sha1:x"}
    assert extract_results({"results": [entry]}) == [entry]
    assert extract_results({"items": [entry]}) == [entry]
    assert extract_results([entry]) == [entry]
    assert extract_results({"nope": []}) == []
    assert extract_results("string") == []
    assert extract_results({"results": [entry, "junk"]}) == [entry]


def test_keyword_screen_serialises_to_json():
    results = keyword_screen([sample_article()], ["positive selection"])
    assert json.loads(json.dumps(results))[0]["id"]
