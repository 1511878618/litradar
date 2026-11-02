"""Article identity must be stable — a drifting id re-reports the same paper."""

from datetime import date

from litradar.model import Article, JournalScan, looks_non_research, normalize_doi


def test_doi_identity_is_stable():
    first = Article(title="T", journal="J", url="u", doi="10.1/ABC")
    second = Article(title="T (corrected)", journal="J", url="u2", doi="10.1/abc")
    assert first.id == second.id


def test_ids_differ_across_journals_without_doi():
    a = Article(title="Same title", journal="Journal A", url="u1")
    b = Article(title="Same title", journal="Journal B", url="u2")
    assert a.id != b.id


def test_full_author_name_disambiguates():
    ann = Article(title="T", journal="J", url="u", authors=["Ann Lee"])
    bob = Article(title="T", journal="J", url="u", authors=["Bob Lee"])
    assert ann.id != bob.id


def test_no_author_is_stable():
    first = Article(title="T", journal="J", url="u")
    second = Article(title="T", journal="J", url="u")
    assert first.id == second.id


def test_normalize_doi_variants():
    assert normalize_doi("https://doi.org/10.1038/ABC.") == "10.1038/abc"
    assert normalize_doi("doi:10.1038/ABC") == "10.1038/abc"
    assert normalize_doi("  10.1038/abc  ") == "10.1038/abc"
    assert normalize_doi(None) is None


def test_article_roundtrip():
    article = Article(
        title="T",
        journal="J",
        url="https://example.org/x",
        doi="10.1/x",
        online_date=date(2026, 10, 30),
        date_source="publisher",
        abstract_raw="Some abstract text that is long enough.",
        authors=["Ann Lee"],
        extra={"k": 1},
    )
    restored = Article.from_dict(article.to_dict())
    assert restored.id == article.id
    assert restored.online_date == date(2026, 10, 30)
    assert restored.extra == {"k": 1}


def test_invalid_enum_values_are_sanitised():
    article = Article(title="T", journal="J", url="u", date_source="guess", content_type="blog")
    assert article.date_source == "missing"
    assert article.content_type == "other"


def test_has_abstract():
    assert Article(title="T", journal="J", url="u", abstract_raw="x").has_abstract
    assert not Article(title="T", journal="J", url="u").has_abstract


def test_journal_scan_status_normalised():
    scan = JournalScan(journal="J", status="weird")
    assert scan.status == "error"


def test_journal_scan_complete_semantics():
    assert JournalScan(journal="J", status="ok").complete
    assert not JournalScan(journal="J", status="metadata-only").complete
    assert not JournalScan(journal="J", status="blocked").complete


def test_looks_non_research():
    assert looks_non_research("Editorial: something")
    assert looks_non_research("Correction to: a paper")
    assert looks_non_research("Publisher's Note")
    assert not looks_non_research("Positive selection in plants")


def test_author_correction_is_not_research():
    assert looks_non_research("Author Correction: Rh3+ stabilized in KRh@MFI")
    assert looks_non_research("Retraction Note: something")
