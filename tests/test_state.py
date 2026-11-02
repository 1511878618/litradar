"""State files must be atomic and lock-protected."""

import json

from helpers import temp_dir

from litradar.state import (
    LockBusy,
    VaultLock,
    atomic_write_json,
    atomic_write_text,
    load_scans,
    load_seen,
    merge_scan_history,
    read_json,
    save_scans,
    save_seen,
)


def test_atomic_write_and_backup():
    with temp_dir() as tmp:
        path = tmp / "sub" / "file.txt"
        atomic_write_text(path, "first", backup=True)
        assert path.read_text(encoding="utf-8") == "first"
        atomic_write_text(path, "second", backup=True)
        assert path.read_text(encoding="utf-8") == "second"
        assert (tmp / "sub" / "file.txt.bak").read_text(encoding="utf-8") == "first"


def test_no_backup_by_default():
    with temp_dir() as tmp:
        path = tmp / "report.md"
        atomic_write_text(path, "one")
        atomic_write_text(path, "two")
        assert not (tmp / "report.md.bak").exists()


def test_no_temp_files_left_behind():
    with temp_dir() as tmp:
        path = tmp / "x.json"
        atomic_write_json(path, {"a": 1})
        leftovers = [entry.name for entry in tmp.iterdir() if entry.name.startswith(".")]
        assert leftovers == []


def test_read_json_default():
    with temp_dir() as tmp:
        assert read_json(tmp / "missing.json", default={"x": 1}) == {"x": 1}
        (tmp / "bad.json").write_text("{not json", encoding="utf-8")
        try:
            read_json(tmp / "bad.json")
        except Exception as exc:
            assert "JSON" in str(exc)
        else:
            raise AssertionError("expected error for malformed JSON")


def test_lock_is_exclusive():
    with temp_dir() as tmp:
        first = VaultLock(tmp / ".lock").acquire()
        try:
            try:
                VaultLock(tmp / ".lock").acquire()
            except LockBusy:
                pass
            else:
                raise AssertionError("second lock should fail")
        finally:
            first.release()
        again = VaultLock(tmp / ".lock").acquire()
        again.release()


def test_lock_records_pid():
    with temp_dir() as tmp:
        lock = VaultLock(tmp / "lock").acquire()
        try:
            assert (tmp / "lock").read_text(encoding="ascii").isdigit()
        finally:
            lock.release()


def test_seen_roundtrip():
    with temp_dir() as tmp:
        path = tmp / "seen.json"
        save_seen(path, {"sha1:x": {"title": "T"}})
        assert load_seen(path)["sha1:x"]["title"] == "T"


def test_scans_roundtrip():
    with temp_dir() as tmp:
        path = tmp / "scans.json"
        save_scans(path, {"J": {"status": "ok"}})
        assert load_scans(path)["J"]["status"] == "ok"


def test_merge_scan_history_success_resets_failures():
    entry = merge_scan_history({"consecutive_failures": 2}, {"status": "ok", "scanned_at": "2026-10-30T09:00:00+08:00"})
    assert entry["consecutive_failures"] == 0
    assert entry["last_ok_at"] == "2026-10-30T09:00:00+08:00"


def test_merge_scan_history_failure_increments_and_keeps_last_ok():
    previous = {"consecutive_failures": 1, "last_ok_at": "2026-10-27T09:00:00+08:00"}
    entry = merge_scan_history(
        previous,
        {"status": "blocked", "scanned_at": "2026-10-30T09:00:00+08:00", "reason": "HTTP 403"},
    )
    assert entry["consecutive_failures"] == 2
    assert entry["last_ok_at"] == "2026-10-27T09:00:00+08:00"
    assert entry["reason"] == "HTTP 403"


def test_json_payload_is_readable():
    with temp_dir() as tmp:
        path = tmp / "a.json"
        atomic_write_json(path, {"中文": "值"})
        assert "中文" in path.read_text(encoding="utf-8")
        assert json.loads(path.read_text(encoding="utf-8"))["中文"] == "值"
