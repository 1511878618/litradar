"""Helpers: dates, HTML cleaning, redaction."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from litradar.util import (
    ConfigError,
    clean_abstract,
    collapse,
    iso,
    norm_title,
    parse_date,
    redact,
    similar,
    slugify,
    strip_html,
    truncate,
    tzinfo,
)


def test_parse_date_iso():
    assert parse_date("2026-10-30") == date(2026, 10, 30)
    assert parse_date("2026/1/4") == date(2026, 1, 4)
    assert parse_date("20261030") == date(2026, 10, 30)


def test_parse_date_with_time():
    assert parse_date("2026-10-30T14:00:00Z") == date(2026, 10, 30)
    assert parse_date("2026-10-30T14:00:00+08:00") == date(2026, 10, 30)


def test_parse_date_rfc822():
    assert parse_date("Fri, 30 Oct 2026 00:00:00 GMT") == date(2026, 10, 30)


def test_parse_date_human_text():
    assert parse_date("Published online: 30 October 2026; doi:10.1/x") == date(2026, 10, 30)
    assert parse_date("22 Oct 2026") == date(2026, 10, 22)


def test_parse_date_never_guesses():
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("n/a") is None
    assert parse_date("2026-13-40") is None


def test_parse_date_accepts_date_objects():
    assert parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert parse_date(datetime(2026, 1, 2, 3, 4)) == date(2026, 1, 2)


def test_iso_carries_offset():
    rendered = iso()
    assert "T" in rendered
    assert rendered.endswith("+00:00") or "+" in rendered[10:] or "-" in rendered[10:]


def test_strip_html_entities_and_tags():
    assert strip_html("<p>a &amp; b</p><p>c</p>") == "a & b c"


def test_clean_abstract_removes_publisher_boilerplate():
    raw = (
        "<p>Nature Genetics, Published online: 30 October 2026; "
        '<a href="https://doi.org/10.1038/x">doi:10.1038/x</a></p>'
        "We assembled a haplotype reference panel from 490,319 genomes."
    )
    cleaned = clean_abstract(raw)
    assert cleaned.startswith("We assembled")
    assert "Published online" not in cleaned


def test_clean_abstract_removes_jats_and_title():
    raw = "<jats:title>Abstract</jats:title><jats:p>We analyse selection in great apes.</jats:p>"
    assert clean_abstract(raw) == "We analyse selection in great apes."


def test_clean_abstract_removes_byline_paragraph():
    raw = "<p>by Siyi Chen, Bob Lee</p>\nWe derive a conversion for liability-scale analyses."
    assert clean_abstract(raw).startswith("We derive")


def test_clean_abstract_too_short_is_none():
    assert clean_abstract("Abstract") is None
    assert clean_abstract("Too short.") is None


def test_clean_abstract_truncates_on_word_boundary():
    cleaned = clean_abstract("word " * 500, max_chars=100)
    assert len(cleaned) <= 100
    assert cleaned.endswith("word")


def test_truncate_keeps_short_text():
    assert truncate("abc", 10) == "abc"
    assert truncate("a b c d", 5) == "a b…"


def test_redact_api_keys():
    assert "sk-" not in redact("key=sk-abcdef0123456789 rest").replace("sk-***", "")
    assert "sk-***" in redact("Authorization: sk-abcdef0123456789")


def test_similar_scores():
    assert similar("abc def", "abc def") == 1.0
    assert similar("abc def", "") == 0.0
    assert 0.0 <= similar("abc def", "abc xyz") < 1.0


def test_norm_title_ignores_case_and_punctuation():
    assert norm_title("A Study: of Selection!") == norm_title("a study of selection")


def test_slugify():
    assert slugify("Nature Genetics") == "nature-genetics"
    assert slugify("") == "journal"


def test_collapse():
    assert collapse("  a \n b  ") == "a b"


def test_tzinfo_unknown_raises():
    try:
        tzinfo("Mars/Olympus")
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")


def test_tzinfo_shanghai():
    assert tzinfo("Asia/Shanghai") == ZoneInfo("Asia/Shanghai")


def test_clean_title_keeps_short_titles():
    from litradar.util import clean_title

    assert clean_title("CRISPR screens") == "CRISPR screens"
    assert clean_title("<i>Short</i> &amp; sweet") == "Short & sweet"
    assert clean_title("  ") == ""
