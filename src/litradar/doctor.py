"""Engine self-checks — the first thing to run when a daily report looks wrong."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from . import SCHEMA, __version__
from .config import ConfigError, load_journals
from .pipeline import Context
from .window import build_window


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "error"  # error | warn | info

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "severity": self.severity,
        }


def _check_python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 10)
    return Check("python", ok, f"{version}（需要 >= 3.10）")


def _check_deps() -> Check:
    missing = []
    versions = []
    for module, label in (("yaml", "PyYAML"), ("bs4", "beautifulsoup4")):
        try:
            imported = __import__(module)
            versions.append(f"{label} {getattr(imported, '__version__', '?')}")
        except ImportError:
            missing.append(label)
    if missing:
        return Check("deps", False, "缺少依赖：" + "、".join(missing) + "；pip install -e .")
    return Check("deps", True, "、".join(versions))


def _check_vault(ctx: Context) -> Check:
    if not ctx.paths.root.exists():
        return Check("vault", False, f"{ctx.paths.vault} 尚未初始化；运行 litradar init")
    if not ctx.paths.config_file.exists():
        return Check("vault", False, f"缺少 {ctx.paths.config_file}")
    return Check("vault", True, str(ctx.paths.vault))


def _check_config(ctx: Context) -> Check:
    direction = ctx.config.direction
    return Check(
        "config",
        True,
        f"schema={SCHEMA} engine={__version__} tz={ctx.config.tz} "
        f"direction={'已填写' if direction else '未填写（筛选质量会下降）'}",
        severity="info" if direction else "warn",
    )


def _check_llm(ctx: Context) -> Check:
    """Where the API key lives matters: inline keys can be synced by accident."""
    inline = str(ctx.config.get("llm.api_key", "") or "").strip()
    file_ref = str(ctx.config.get("llm.api_key_file", "") or "").strip()
    env_name = str(ctx.config.get("llm.api_key_env", "") or "")

    if inline.startswith("$"):
        return Check("llm", True, f"API key 引用环境变量：{inline}", severity="info")
    if inline:
        try:
            mode = stat.S_IMODE(ctx.paths.config_file.stat().st_mode)
        except OSError:
            mode = 0o644
        if mode & 0o077:
            return Check(
                "llm",
                True,
                "API key 明文写在 config.yaml，且文件对其他用户可读（建议 chmod 600）",
                severity="warn",
            )
        return Check(
            "llm", True, "API key 明文写在 config.yaml（可改用 api_key_file 或环境变量）",
            severity="warn",
        )
    if file_ref:
        path = Path(os.path.expanduser(file_ref))
        if not path.exists():
            return Check("llm", False, f"api_key_file 不存在：{path}", severity="warn")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = 0
        if mode & 0o077:
            return Check("llm", True, f"key 文件权限过宽：{path}（建议 chmod 600）", severity="warn")
        return Check("llm", True, f"从文件读取 key：{path}", severity="info")
    if env_name and os.environ.get(env_name):
        return Check("llm", True, f"环境变量 {env_name} 已设置", severity="info")
    return Check("llm", False, "未配置 API key（将降级关键词置顶）", severity="warn")


def _check_journals(ctx: Context) -> tuple[Check, list[dict]]:
    try:
        journals = load_journals(ctx.config)
    except ConfigError as exc:
        return Check("journals", False, str(exc)), []
    if not journals:
        return Check("journals", False, "没有配置任何期刊"), []
    names = "、".join(journal["name"] for journal in journals)
    return Check("journals", True, f"{len(journals)} 本：{names}"), journals


def _check_journal_reach(ctx: Context, journal: dict) -> Check:
    from .adapters.base import get_adapter

    window = build_window("yesterday", tz=ctx.tz)
    try:
        adapter = get_adapter(journal, ctx.paths.vault)
        items, scan = adapter.fetch(journal, ctx.fetcher, window)
    except Exception as exc:  # noqa: BLE001 - doctor must never crash
        return Check(f"journal:{journal['name']}", False, f"探测失败：{exc}", severity="warn")
    detail = f"{scan.status} · {scan.items_seen} 项" + (f" · {scan.reason}" if scan.reason else "")
    if scan.healthy:
        return Check(f"journal:{journal['name']}", True, detail, severity="info")
    return Check(f"journal:{journal['name']}", False, detail, severity="warn")


def _check_write(ctx: Context) -> Check:
    probe = ctx.paths.root / ".doctor-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check("write", True, "vault 可写", severity="info")
    except OSError as exc:
        return Check("write", False, f"vault 不可写：{exc}")


def _check_schedule(ctx: Context) -> Check:
    from .schedule import query

    try:
        info = query(ctx.paths.vault)
    except Exception as exc:  # noqa: BLE001
        return Check("schedule", False, f"无法检查计划任务：{exc}", severity="warn")
    if info.get("installed"):
        return Check("schedule", True, "已注册", severity="info")
    return Check("schedule", False, "未注册（Obsidian 关闭时不会自动出日报）", severity="warn")


def run_doctor(ctx: Context, *, network: bool = True) -> list[Check]:
    checks = [_check_python(), _check_deps(), _check_vault(ctx)]
    if not checks[-1].ok:
        return checks
    checks.append(_check_config(ctx))
    checks.append(_check_llm(ctx))
    journal_check, journals = _check_journals(ctx)
    checks.append(journal_check)
    checks.append(_check_write(ctx))
    if network and journals:
        for journal in journals:
            checks.append(_check_journal_reach(ctx, journal))
    checks.append(_check_schedule(ctx))
    return checks


def has_errors(checks: list[Check]) -> bool:
    return any(not check.ok and check.severity == "error" for check in checks)


def summarize(checks: list[Check]) -> str:
    errors = [check for check in checks if not check.ok and check.severity == "error"]
    warnings = [check for check in checks if not check.ok and check.severity == "warn"]
    if errors:
        return f"{len(errors)} 个错误，{len(warnings)} 个警告"
    if warnings:
        return f"可用，{len(warnings)} 个警告"
    return "一切正常"


__all__ = ["Check", "has_errors", "run_doctor", "summarize"]
