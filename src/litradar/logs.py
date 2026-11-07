"""Human-readable logging — always stderr, always redacted.

stdout is reserved for the ``--jsonl`` protocol; anything a human reads goes here so
that piping the engine into a front-end never mixes the two streams.
"""

from __future__ import annotations

import io
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .util import redact

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


class Logger:
    def __init__(
        self,
        level: str = "info",
        stream: TextIO | None = None,
        file_path: Path | None = None,
        timestamp: bool = True,
    ) -> None:
        self.level = LEVELS.get(level, 20)
        self.stream = stream if stream is not None else sys.stderr
        self.timestamp = timestamp
        self.secrets: list[str] = []
        self._files: list[TextIO] = []
        if file_path is not None:
            self.add_file(file_path)

    def add_file(self, file_path: Path | str) -> None:
        """Attach another log file (every run keeps ``runs/<id>/run.log`` too).

        A log file we cannot open (read-only vault, macOS TCC, full disk) must never
        take the command down: stderr keeps working, and the reason is printed once.
        """
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a", encoding="utf-8")
        except OSError as exc:
            try:
                print(f"WARN  无法写日志文件 {path}：{exc}（仅输出到 stderr）", file=self.stream)
            except (OSError, ValueError):
                pass
            return
        self._files.append(handle)

    def _write(self, level: str, message: str) -> None:
        if LEVELS[level] < self.level:
            return
        stamp = f"{datetime.now():%H:%M:%S} " if self.timestamp else ""
        message = redact(message)
        for secret in self.secrets:
            message = message.replace(secret, "***")
        line = f"{stamp}{level.upper():5s} {message}"
        try:
            print(line, file=self.stream, flush=True)
        except (OSError, ValueError):  # stream closed by a departed front-end
            pass
        for handle in self._files:
            try:
                handle.write(line + "\n")
                handle.flush()
            except OSError:
                pass

    def add_secret(self, value: str | None) -> None:
        """Redact a literal secret (e.g. a non-sk API key) from every log line."""
        if value and len(value) >= 8 and value not in self.secrets:
            self.secrets.append(value)

    def debug(self, message: str) -> None:
        self._write("debug", message)

    def info(self, message: str) -> None:
        self._write("info", message)

    def warn(self, message: str) -> None:
        self._write("warn", message)

    def error(self, message: str) -> None:
        self._write("error", message)

    def journal(self, journal: str, status: str, detail: str, ms: int | None = None) -> None:
        suffix = f" ({ms}ms)" if ms is not None else ""
        self._write("info", f"[{journal}] {status} {detail}{suffix}".replace("  ", " ").strip())

    def close(self) -> None:
        for handle in self._files:
            try:
                handle.close()
            except OSError:
                pass
        self._files.clear()


class Stopwatch:
    def __init__(self) -> None:
        self._start = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


class NullLogger(Logger):
    """Logger that writes nowhere — used by tests and by ``screen --strict``."""

    def __init__(self) -> None:
        super().__init__(level="error", stream=io.StringIO())

    def _write(self, level: str, message: str) -> None:  # pragma: no cover - trivial
        return None


def rotate_logs(directory, keep_days: int = 7) -> list:
    """Delete ``cli-*.log`` older than ``keep_days`` so a daily job cannot fill a disk."""
    import time
    from pathlib import Path

    directory = Path(directory)
    if not directory.exists():
        return []
    cutoff = time.time() - keep_days * 86400
    removed = []
    for path in directory.glob("cli-*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed
