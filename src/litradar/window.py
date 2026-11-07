"""Date windows: every report is defined by a window, never by "when it ran"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .util import ConfigError, parse_date, today

KINDS = ("today", "yesterday", "last_week", "last_n_days", "explicit", "weekly", "week_to_date", "day")


@dataclass(frozen=True)
class Window:
    kind: str
    start: date
    end: date
    tz: str = "Asia/Shanghai"
    label: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ConfigError(f"窗口结束日 {self.end} 早于开始日 {self.start}")
        if not self.label:
            object.__setattr__(self, "label", self._default_label())

    def _default_label(self) -> str:
        if self.kind in ("weekly", "week_to_date"):
            return f"{self.week_label}（{self.start} ~ {self.end}）"
        if self.single_day:
            return self.start.isoformat()
        return f"{self.start} ~ {self.end}"

    @property
    def single_day(self) -> bool:
        return self.start == self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def week_label(self) -> str:
        iso = self.start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    @property
    def run_id(self) -> str:
        if self.kind in ("weekly", "week_to_date"):
            return self.week_label
        if self.single_day:
            return self.start.isoformat()
        return f"{self.start}_{self.end}"

    def contains(self, value: date | None) -> bool:
        return value is not None and self.start <= value <= self.end

    def covers_weekday(self, weekday: int) -> bool:
        """Does any day of the window fall on ``weekday`` (Monday=0)?"""
        if not 0 <= weekday <= 6:
            return False
        span = (self.end - self.start).days + 1
        return any((self.start + timedelta(days=offset)).weekday() == weekday for offset in range(span))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "tz": self.tz,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Window:
        start = parse_date(data.get("start"))
        end = parse_date(data.get("end"))
        if not start or not end:
            raise ConfigError("store 文件里的 window 缺少合法的 start/end")
        return cls(
            kind=data.get("kind") or "day",
            start=start,
            end=end,
            tz=data.get("tz") or "Asia/Shanghai",
            label=data.get("label") or "",
        )


def build_window(
    kind: str = "yesterday",
    *,
    tz: str = "Asia/Shanghai",
    ref: date | str | None = None,
    date_str: str | None = None,
    days: int = 7,
    start: str | date | None = None,
    end: str | date | None = None,
) -> Window:
    """Resolve CLI flags into an explicit, auditable window."""
    if date_str:
        day = parse_date(date_str)
        if not day:
            raise ConfigError(f"无法解析日期 {date_str!r}，请用 YYYY-MM-DD")
        return Window(kind="day", start=day, end=day, tz=tz)

    ref_day = parse_date(ref) if ref else today(tz)
    if ref_day is None:
        raise ConfigError(f"无法解析参照日期 {ref!r}")

    if kind == "today":
        return Window(kind=kind, start=ref_day, end=ref_day, tz=tz)
    if kind == "yesterday":
        day = ref_day - timedelta(days=1)
        return Window(kind=kind, start=day, end=day, tz=tz)
    if kind in ("last_week", "last_n_days"):
        if days < 1:
            raise ConfigError("--days 必须 >= 1")
        return Window(
            kind=kind, start=ref_day - timedelta(days=days), end=ref_day - timedelta(days=1), tz=tz
        )
    if kind == "weekly":
        cursor = ref_day - timedelta(days=7)
        start_day = cursor - timedelta(days=cursor.weekday())
        return Window(kind=kind, start=start_day, end=start_day + timedelta(days=6), tz=tz)
    if kind == "week_to_date":
        # "This week so far": Monday through the reference day (Friday by default).
        start_day = ref_day - timedelta(days=ref_day.weekday())
        return Window(kind=kind, start=start_day, end=ref_day, tz=tz)
    if kind == "explicit":
        start_day, end_day = parse_date(start), parse_date(end)
        if not start_day or not end_day:
            raise ConfigError("explicit 窗口需要 --start 与 --end（YYYY-MM-DD）")
        return Window(kind=kind, start=start_day, end=end_day, tz=tz)

    raise ConfigError(f"未知窗口类型 {kind!r}，可选：{', '.join(KINDS)}")
