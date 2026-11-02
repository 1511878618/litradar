"""Filesystem state: atomic writes, one vault lock, and the two state files.

Rules that matter and are easy to get wrong:

* never ``mkstemp``/``mkdtemp`` (they are unusable in some sandboxes) — write a
  sibling ``.tmp`` file and ``os.replace`` it;
* ``seen.json`` may only be written *after* a report has been rendered;
* concurrent runs must fail fast with exit code 6 instead of corrupting state.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .util import EngineError

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class LockBusy(EngineError):
    """Another litradar process owns the vault lock (exit code 6)."""


# msvcrt locks are per process, so two VaultLock instances in one process would
# both succeed on Windows.  A process-local registry makes re-entry behave the same
# everywhere (and turns accidental nested locks into a clear error).
_HELD_LOCKS: set[str] = set()
_HELD_GUARD = threading.Lock()


def _lock_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def read_json(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EngineError(f"{path} 不是合法 JSON：{exc}") from exc


def atomic_write_text(path: Path | str, text: str, *, backup: bool = False) -> None:
    """Write atomically; optionally keep one ``.bak`` generation (state files)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if backup and path.exists():
        try:  # keep one generation so a corrupted write is recoverable
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


def atomic_write_json(path: Path | str, payload: Any, *, backup: bool = False) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", backup=backup)


class VaultLock(AbstractContextManager):
    """Advisory, non-blocking, cross-platform lock file."""

    def __init__(self, path: Path | str, wait_seconds: float = 0.0) -> None:
        self.path = Path(path)
        self.wait_seconds = wait_seconds
        self._handle: Any = None

    def acquire(self) -> VaultLock:
        key = _lock_key(self.path)
        with _HELD_GUARD:
            if key in _HELD_LOCKS:
                raise LockBusy("已有任务在运行（同一进程已持有 .litradar/.lock）")
            _HELD_LOCKS.add(key)
        try:
            return self._acquire_os(key)
        except BaseException:
            with _HELD_GUARD:
                _HELD_LOCKS.discard(key)
            raise

    def _acquire_os(self, key: str) -> VaultLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        deadline = time.monotonic() + max(self.wait_seconds, 0)
        while True:
            try:
                if fcntl is not None:
                    flags = fcntl.LOCK_EX | (0 if self.wait_seconds else fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), flags)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if self.wait_seconds and time.monotonic() < deadline:
                    time.sleep(0.2)
                    continue
                handle.close()
                raise LockBusy(".litradar/.lock 被占用（另一个 litradar 进程正在运行）") from exc
        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
        except OSError:
            pass
        return self

    def release(self) -> None:
        key = _lock_key(self.path)
        with _HELD_GUARD:
            _HELD_LOCKS.discard(key)
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self) -> VaultLock:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


SEEN_DO_NOT_EDIT = "DO NOT EDIT — written by litradar after a successful render."


def load_seen(path: Path | str) -> dict[str, dict]:
    data = read_json(path, default={})
    if not isinstance(data, dict):
        raise EngineError(f"{path} 结构错误：期望 object，得到 {type(data).__name__}")
    return data


def save_seen(path: Path | str, seen: dict[str, dict]) -> None:
    atomic_write_json(path, seen, backup=True)


def load_scans(path: Path | str) -> dict[str, dict]:
    data = read_json(path, default={})
    return data if isinstance(data, dict) else {}


def save_scans(path: Path | str, scans: dict[str, dict]) -> None:
    atomic_write_json(path, scans, backup=True)


def merge_scan_history(previous: dict | None, scan: dict) -> dict:
    """Fold today's scan into the persistent per-journal history."""
    previous = previous or {}
    failures = 0 if scan.get("status") in ("ok", "metadata-only") else int(
        previous.get("consecutive_failures") or 0
    ) + 1
    entry = {
        "last_ok_at": scan.get("scanned_at") if scan.get("status") in ("ok", "metadata-only")
        else previous.get("last_ok_at"),
        "last_attempt_at": scan.get("scanned_at"),
        "status": scan.get("status"),
        "http": scan.get("http"),
        "items_seen": scan.get("items_seen", 0),
        "reason": scan.get("reason"),
        "surface": scan.get("surface"),
        "url": scan.get("url"),
        "visibility": scan.get("visibility", "publisher"),
        "consecutive_failures": failures,
    }
    return entry
