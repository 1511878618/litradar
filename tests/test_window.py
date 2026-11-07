"""Windows must be explicit; "today" must never leak into a report."""

from datetime import date, timedelta

from litradar.util import ConfigError
from litradar.window import Window, build_window

REF = date(2026, 10, 31)


def test_yesterday():
    window = build_window("yesterday", ref=REF, tz="Asia/Shanghai")
    assert window.start == date(2026, 10, 30)
    assert window.end == date(2026, 10, 30)
    assert window.single_day
    assert window.run_id == "2026-10-30"


def test_explicit_date_is_a_single_day():
    window = build_window(date_str="2026-10-20", tz="Asia/Shanghai")
    assert (window.start, window.end) == (date(2026, 10, 20), date(2026, 10, 20))
    assert window.kind == "day"


def test_last_week_is_seven_days_ending_yesterday():
    window = build_window("last_week", ref=REF, days=7, tz="Asia/Shanghai")
    assert window.start == REF - timedelta(days=7)
    assert window.end == REF - timedelta(days=1)
    assert window.days == 7


def test_last_n_days():
    window = build_window("last_n_days", ref=REF, days=3, tz="UTC")
    assert window.days == 3
    assert window.end == REF - timedelta(days=1)


def test_explicit_range():
    window = build_window("explicit", start="2026-10-01", end="2026-10-07", tz="UTC")
    assert window.days == 7
    assert window.run_id == "2026-10-01_2026-10-07"


def test_explicit_requires_both_ends():
    try:
        build_window("explicit", start="2026-10-01", tz="UTC")
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")


def test_explicit_rejects_reversed_range():
    try:
        Window(kind="explicit", start=date(2026, 10, 7), end=date(2026, 10, 1))
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")


def test_weekly_uses_last_complete_iso_week():
    window = build_window("weekly", ref=REF, tz="UTC")
    assert window.kind == "weekly"
    assert window.start.weekday() == 0
    assert window.days == 7
    assert window.run_id == window.week_label
    assert window.run_id.startswith("2026-W")


def test_contains_inclusive_edges():
    window = Window(kind="explicit", start=date(2026, 10, 1), end=date(2026, 10, 3))
    assert window.contains(date(2026, 10, 1))
    assert window.contains(date(2026, 10, 3))
    assert not window.contains(date(2026, 9, 30))
    assert not window.contains(None)


def test_roundtrip():
    window = Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30), tz="Asia/Shanghai")
    restored = Window.from_dict(window.to_dict())
    assert restored == window
    assert restored.label == "2026-10-30"


def test_unknown_kind():
    try:
        build_window("fortnight", ref=REF)
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")


def test_covers_weekday_spans_the_window():
    from datetime import date

    from litradar.window import Window

    # 2026-11-02 is a Monday; the week 11-02..11-08 covers every weekday once.
    window = Window(kind="explicit", start=date(2026, 11, 2), end=date(2026, 11, 8))
    for weekday in range(7):
        assert window.covers_weekday(weekday)
    sunday_only = Window(kind="day", start=date(2026, 11, 8), end=date(2026, 11, 8))
    assert sunday_only.covers_weekday(6)
    assert not sunday_only.covers_weekday(2)
    assert not sunday_only.covers_weekday(9)
