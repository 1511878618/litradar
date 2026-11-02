"""Deterministic topic tags: stable vocabulary for the daily chart."""

from helpers import sample_article

from litradar.tags import compiled_rules, merged_rules, tag_article, topic_counts


def test_title_tags():
    article = sample_article(title="Deep learning for single-cell genomics",
                             abstract_raw="A neural network study.")
    tags = tag_article(article)
    assert "机器学习与AI" in tags
    assert "单细胞" in tags
    assert "基因组学" in tags


def test_abstract_fills_gaps_when_title_has_none():
    article = sample_article(title="A new method for biology",
                             abstract_raw="We analyse genome-wide variants in patients.")
    tags = tag_article(article)
    assert "基因组学" in tags


def test_implant_is_not_a_plant():
    article = sample_article(title="Cortical implant restores connectivity",
                             abstract_raw="A device implanted in the brain.")
    assert "植物与农业" not in tag_article(article)


def test_max_three_tags():
    article = sample_article(
        title="Single-cell genome analysis with deep learning in patients",
        abstract_raw="CRISPR screens and protein structure.",
    )
    assert len(tag_article(article)) <= 3


def test_custom_rules_are_merged():
    rules = merged_rules({"空间转录组": ["spatial transcriptomics"]})
    assert "空间转录组" in rules
    article = sample_article(title="Spatial transcriptomics of tumours",
                             abstract_raw="A spatial transcriptomics atlas.")
    compiled = compiled_rules({"空间转录组": ["spatial transcriptomics"]})
    assert "空间转录组" in tag_article(article, compiled)


def test_topic_counts_and_untagged():
    first = sample_article(doi="10.1/a", title="Genome-wide association study",
                           abstract_raw="")
    second = sample_article(doi="10.1/b", title="Something entirely unrelated",
                            abstract_raw="")
    first.extra["tags_auto"] = ["基因组学"]
    second.extra["tags_auto"] = []
    counts, untagged = topic_counts([first, second])
    assert counts["基因组学"] == 1
    assert untagged == 1
