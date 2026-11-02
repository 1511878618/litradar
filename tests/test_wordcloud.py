"""Word cloud: deterministic SVG, no plotting dependency."""

from helpers import sample_article, temp_dir

from litradar.wordcloud import build_svg, title_terms, write_wordcloud


def test_title_terms_filters_stopwords_and_orders_by_frequency():
    items = [
        sample_article(doi="10.1/a", title="Genome editing of wheat"),
        sample_article(doi="10.1/b", title="Genome assembly of wheat"),
        sample_article(doi="10.1/c", title="A study of methods"),
    ]
    terms = title_terms(items)
    assert terms["genome"] == 2
    assert terms["wheat"] == 2
    assert "study" not in terms
    assert "methods" not in terms
    assert list(terms)[0] in ("genome", "wheat")


def test_svg_is_well_formed_and_escaped():
    svg = build_svg({"genome": 5, "a<b": 2})
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "a&lt;b" in svg


def test_write_wordcloud_returns_relative_path():
    with temp_dir() as tmp:
        titles = [
            "Genome assembly of wheat",
            "Single-cell atlas of the brain",
            "CRISPR editing in rice",
            "Deep learning for medical imaging",
            "Protein structure prediction",
            "Microbial diversity in soil",
            "Chromatin architecture in neurons",
            "Spatial transcriptomics of tumours",
        ]
        items = [sample_article(doi=f"10.1/{index}", title=title)
                 for index, title in enumerate(titles)]
        path = write_wordcloud(tmp, "2026-10-30", "journal", items)
        assert path and path.startswith("assets/")
        assert (tmp / path).exists()
        assert "<svg" in (tmp / path).read_text(encoding="utf-8")


def test_wordcloud_skips_thin_input():
    with temp_dir() as tmp:
        assert write_wordcloud(tmp, "2026-10-30", "journal", [sample_article()]) is None
