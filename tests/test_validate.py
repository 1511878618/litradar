"""The seven verification rules — the core promise that output is not made up."""

from helpers import sample_article

from litradar.util import similar
from litradar.validate import default_hosts, host_allowed, validate_results


def _item(**overrides):
    return sample_article(**overrides)


def test_rule1_unknown_id_is_rejected():
    item = _item()
    report = validate_results(
        [{"id": "sha1:ffffffffffffffff", "relevant": True}], [item], {"example.org"}
    )
    assert "sha1:ffffffffffffffff" in dict(report.rejected)
    assert report.results == {}


def test_rule2_doi_mismatch_is_ignored():
    item = _item()
    report = validate_results(
        [{"id": item.id, "doi": "10.9999/other", "relevant": False}], [item], {"example.org"}
    )
    result = report.results[item.id]
    assert result.doi == item.doi
    assert any("DOI" in note for note in report.notes)


def test_rule3_verbatim_mismatch_downgrades_to_summary():
    item = _item()
    report = validate_results(
        [
            {
                "id": item.id,
                "abstract_en": "A completely different sentence about mushrooms.",
                "abstract_mode": "verbatim",
            }
        ],
        [item],
        {"example.org"},
    )
    result = report.results[item.id]
    assert result.abstract_mode == "faithful-summary"
    assert any("已降级为概述" in note for note in report.notes)


def test_rule3_verbatim_match_is_kept():
    item = _item()
    report = validate_results(
        [{"id": item.id, "abstract_en": item.abstract_raw, "abstract_mode": "verbatim"}],
        [item],
        {"example.org"},
    )
    assert report.results[item.id].abstract_mode == "verbatim"


def test_rule4_verbatim_without_source_abstract():
    item = _item(abstract_raw=None)
    report = validate_results(
        [{"id": item.id, "abstract_en": "something", "abstract_mode": "verbatim"}],
        [item],
        {"example.org"},
    )
    result = report.results[item.id]
    assert result.abstract_mode == "none"
    assert result.abstract_en is None


def test_rule5_conclusion_requires_source():
    item = _item()
    report = validate_results(
        [{"id": item.id, "conclusion": "This proves X.", "conclusion_source": None}],
        [item],
        {"example.org"},
    )
    assert report.results[item.id].conclusion is None
    assert any("结论" in note for note in report.notes)


def test_rule5_conclusion_source_must_be_whitelisted():
    item = _item()
    report = validate_results(
        [{"id": item.id, "conclusion": "This proves X.", "conclusion_source": "https://spam.example.net/x"}],
        [item],
        {"example.org"},
    )
    assert report.results[item.id].conclusion is None


def test_rule5_valid_conclusion_is_kept():
    item = _item()
    report = validate_results(
        [
            {
                "id": item.id,
                "conclusion": "Selection acts on standing variation.",
                "conclusion_source": "https://example.org/paper",
            }
        ],
        [item],
        {"example.org"},
    )
    assert report.results[item.id].conclusion is not None


def test_rule6_missing_url_is_rejected():
    item = _item(url="")
    report = validate_results([{"id": item.id, "relevant": True}], [item], {"example.org"})
    assert report.rejected
    assert report.results == {}


def test_rule7_relevance_is_clamped():
    item = _item()
    report = validate_results([{"id": item.id, "relevance": 1.7}], [item], {"example.org"})
    assert report.results[item.id].relevance == 1.0
    report = validate_results([{"id": item.id, "relevance": -2}], [item], {"example.org"})
    assert report.results[item.id].relevance == 0.0


def test_relevance_missing_is_not_guessed():
    item = _item()
    report = validate_results([{"id": item.id, "why": "x"}], [item], {"example.org"})
    assert report.results[item.id].relevance is None


def test_missing_results_are_recorded():
    items = [_item(doi="10.1/a"), _item(doi="10.1/b", title="Second")]
    report = validate_results([{"id": items[0].id, "relevant": True}], items, {"example.org"})
    assert items[1].id in report.missing


def test_duplicate_ids_keep_the_first():
    item = _item()
    report = validate_results(
        [{"id": item.id, "relevant": True, "relevance": 0.9}, {"id": item.id, "relevance": 0.1}],
        [item],
        {"example.org"},
    )
    assert report.results[item.id].relevance == 0.9


def test_abstract_mode_unknown_becomes_none():
    item = _item()
    report = validate_results(
        [{"id": item.id, "abstract_mode": "paraphrase", "abstract_en": "x"}], [item], {"example.org"}
    )
    assert report.results[item.id].abstract_mode == "none"


def test_tags_are_capped():
    item = _item()
    report = validate_results(
        [{"id": item.id, "topic_tags": [f"t{i}" for i in range(9)]}], [item], {"example.org"}
    )
    assert len(report.results[item.id].topic_tags) == 5


def test_host_allowed_subdomains():
    assert host_allowed("https://www.example.org/x", {"example.org"})
    assert not host_allowed("https://example.org.evil.com/x", {"example.org"})
    assert not host_allowed("file:///etc/passwd", {"example.org"})


def test_default_hosts_includes_doi_and_candidate_hosts():
    hosts = default_hosts([_item(url="https://www.nature.com/articles/x")])
    assert "doi.org" in hosts
    assert "www.nature.com" in hosts


def test_similarity_threshold_is_ninety_percent():
    item = _item(abstract_raw="alpha beta gamma delta epsilon zeta eta theta")
    nearly = "alpha beta gamma delta epsilon zeta eta theta."
    assert similar(nearly, item.abstract_raw) >= 0.90


def test_validation_report_serialises():
    item = _item()
    report = validate_results([{"id": item.id, "relevant": True}], [item], {"example.org"})
    payload = report.to_dict()
    assert item.id in payload["results"]
    assert payload["results"][item.id]["relevant"] is True
