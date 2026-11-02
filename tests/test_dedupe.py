"""Dedupe and window bucketing decide what enters the report."""

from datetime import date

from helpers import sample_article

from litradar.dedupe import dedupe, split_by_window
from litradar.window import Window

WINDOW = Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30))


def test_new_items_pass_through():
    items = [sample_article(doi="10.1/a")]
    result = dedupe(items, {})
    assert len(result.new) == 1
    assert result.already_seen == []
    assert result.duplicates == 0


def test_seen_items_are_separated():
    article = sample_article(doi="10.1/a")
    result = dedupe([article], {article.id: {"title": "x"}})
    assert result.new == []
    assert result.already_seen == [article]


def test_duplicate_within_run_is_counted_once():
    first = sample_article(doi="10.1/a", title="Same")
    second = sample_article(doi="10.1/a", title="Same")
    result = dedupe([first, second], {})
    assert len(result.new) == 1
    assert result.duplicates == 1


def test_window_buckets():
    inside = sample_article(doi="10.1/in", online_date=date(2026, 10, 30))
    outside = sample_article(doi="10.1/out", online_date=date(2026, 10, 25))
    undated = sample_article(doi="10.1/nd", online_date=None)
    editorial = sample_article(doi="10.1/ed", content_type="other")
    buckets = split_by_window([inside, outside, undated, editorial], WINDOW)
    assert buckets["in_window"] == [inside]
    assert buckets["out_of_window"] == [outside]
    assert buckets["date_unverified"] == [undated]
    assert buckets["excluded_type"] == [editorial]


def test_review_type_is_kept():
    review = sample_article(doi="10.1/r", content_type="review")
    buckets = split_by_window([review], WINDOW)
    assert buckets["in_window"] == [review]


def test_include_types_is_configurable():
    review = sample_article(doi="10.1/r", content_type="review")
    buckets = split_by_window([review], WINDOW, include_types=["research-article"])
    assert buckets["in_window"] == []
    assert buckets["excluded_type"] == [review]
