"""Scheduler wrappers: generated commands must be correct on each OS."""

from helpers import temp_dir

from litradar import schedule
from litradar.config import Config


def _config(tmp, **overrides):
    from litradar.config import DEFAULTS, deep_merge

    data = deep_merge(DEFAULTS, {"schedule": {"time": "07:30"}})
    data["schedule"].update(overrides)
    return Config(data, tmp)


def test_shell_wrapper_contains_the_run_command():
    with temp_dir() as tmp:
        original = schedule.platform_key
        schedule.platform_key = lambda: "linux"
        config = _config(tmp)
        try:
            path = schedule.write_wrapper(tmp, config, python_exe="/usr/bin/python3")
        finally:
            schedule.platform_key = original
        content = path.read_text(encoding="utf-8")
        assert path.name == "run-daily.sh"
        assert "-m litradar run" in content
        assert "--catch-up" in content
        assert str(tmp) in content
        assert "/usr/bin/python3" in content
        assert content.startswith("#!/bin/sh")


def test_windows_wrapper():
    with temp_dir() as tmp:
        original = schedule.platform_key
        schedule.platform_key = lambda: "win32"
        try:
            config = _config(tmp)
            path = schedule.write_wrapper(tmp, config, python_exe=r"C:\Python\python.exe")
            content = path.read_text(encoding="utf-8")
            assert path.name == "run-daily.cmd"
            assert "chcp 65001" in content
            assert "cd /d" in content
            assert "%ERRORLEVEL%" in content
        finally:
            schedule.platform_key = original


def test_mac_plist_contains_time():
    with temp_dir() as tmp:
        config = _config(tmp)
        wrapper = schedule.wrapper_path(tmp)
        plist = schedule.mac_plist(wrapper, config)
        assert "com.litradar.daily" in plist
        assert "<key>Hour</key><integer>7</integer>" in plist
        assert "<key>Minute</key><integer>30</integer>" in plist


def test_cron_line():
    with temp_dir() as tmp:
        config = _config(tmp)
        line = schedule.cron_line(schedule.wrapper_path(tmp), config)
        assert line.startswith("30 7 * * *")
        assert "run-daily.sh" in line


def test_print_commands_writes_wrapper():
    with temp_dir() as tmp:
        config = _config(tmp)
        lines = schedule.print_commands(tmp, config)
        assert lines
        assert schedule.wrapper_path(tmp).exists()


def test_uninstall_without_apply_is_dry_run():
    with temp_dir() as tmp:
        config = _config(tmp)
        lines = schedule.uninstall(tmp, config, apply=False)
        assert lines
        assert schedule.wrapper_path(tmp).exists() is False


def test_schedule_time_fallback_on_bad_value():
    with temp_dir() as tmp:
        config = Config({"schedule": {"time": "not-a-time"}}, tmp)
        assert schedule._time(config) == (9, 0)


def test_wrapper_sources_secret_env_file():
    with temp_dir() as tmp:
        original = schedule.platform_key
        schedule.platform_key = lambda: "linux"
        try:
            path = schedule.write_wrapper(tmp, _config(tmp))
        finally:
            schedule.platform_key = original
        content = path.read_text(encoding="utf-8")
        assert ".config/litradar/env" in content
