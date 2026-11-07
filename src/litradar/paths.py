"""Vault layout: every path the engine touches, resolved once."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import ConfigError
from .window import Window

DEFAULT_REPORT_DIR = "02-Reading/文献雷达/{yyyy}/{MM}"
DEFAULT_FILENAME = "{yyyy}-{MM}-{dd}"
DEFAULT_INDEX = "02-Reading/文献雷达/文献雷达索引.md"


def expand_template(template: str, window: Window) -> str:
    tokens: dict[str, Any] = {
        "yyyy": f"{window.end:%Y}",
        "MM": f"{window.end:%m}",
        "dd": f"{window.end:%d}",
        "date": window.end.isoformat(),
        "run_id": window.run_id,
        "week": window.week_label,
    }
    try:
        return template.format(**tokens)
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(
            f"路径模板 {template!r} 含未知占位符；可用：yyyy / MM / dd / date / run_id"
        ) from exc


class Paths:
    def __init__(self, vault: Path | str, config: Any = None) -> None:
        self.vault = Path(vault).expanduser().resolve()
        self.cfg = config
        self.root = self.vault / ".litradar"

    # ---- core directories -------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.yaml"

    @property
    def journals_dir(self) -> Path:
        return self.root / "journals"

    @property
    def store_dir(self) -> Path:
        return self.root / "store"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def vectors_dir(self) -> Path:
        return self.root / "vectors"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def manual_dir(self) -> Path:
        return self.root / "manual"

    @property
    def lock_file(self) -> Path:
        return self.root / ".lock"

    @property
    def seen_file(self) -> Path:
        return self.state_dir / "seen.json"

    @property
    def scans_file(self) -> Path:
        return self.state_dir / "journal_scans.json"

    # ---- per-run ----------------------------------------------------------
    def store_file(self, run_id: str) -> Path:
        return self.store_dir / f"{run_id}.json"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def screen_file(self, run_id: str, kind: str = "journal") -> Path:
        name = "screen.json" if kind == "journal" else f"screen-{kind}.json"
        return self.run_dir(run_id) / name

    def scores_file(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "scores.json"

    def report_copy(self, run_id: str, kind: str = "journal") -> Path:
        name = "report.md" if kind == "journal" else f"report-{kind}.md"
        return self.run_dir(run_id) / name

    def run_log(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.log"

    # ---- report -----------------------------------------------------------
    def report_file(self, window: Window, kind: str = "journal") -> Path:
        template = DEFAULT_REPORT_DIR
        filename = DEFAULT_FILENAME
        suffix = "-preprint"
        if self.cfg is not None:
            template = self.cfg.get("output.report_dir", template) or template
            filename = self.cfg.get("output.filename", filename) or filename
            suffix = str(self.cfg.get("output.preprint_suffix", suffix) or suffix)
        if window.kind in ("weekly", "week_to_date"):
            filename = str(self.cfg.get("output.weekly_filename", "{week}") if self.cfg else "{week}") or "{week}"
        directory = expand_template(template, window)
        name = expand_template(filename, window)
        if kind == "preprint":
            name += suffix
        if not name.lower().endswith(".md"):
            name += ".md"
        return self.vault / directory / name

    def index_file(self) -> Path:
        template = DEFAULT_INDEX
        if self.cfg is not None:
            template = self.cfg.get("output.index_file", template) or template
        return self.vault / template

    # ---- creation ---------------------------------------------------------
    def ensure(self) -> None:
        for directory in (
            self.root,
            self.journals_dir,
            self.store_dir,
            self.runs_dir,
            self.state_dir,
            self.logs_dir,
            self.cache_dir,
            self.manual_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
