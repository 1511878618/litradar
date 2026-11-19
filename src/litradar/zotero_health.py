"""Read-only capability checks; an unavailable integration does not disable others."""
import re
from datetime import datetime, timezone

from .zotero import ZoteroUnavailable


def diagnose(api):
    rows = []

    def row(id, name, state, message, action="", version="", detail=""):
        rows.append(dict(id=id, name=name, state=state, message=message,
                         action=action, version=version, detail=detail))

    try:
        api.request("/api/", timeout=4)
    except ZoteroUnavailable as exc:
        row("zotero", "Zotero", "error", "Zotero 本地接口不可用", "打开 Zotero，并在设置 → 高级中允许其他应用通信", detail=str(exc))
        for id, name in (("bridge", "Litradar 桥接"), ("reading", "Reading List"), ("llm", "llm-for-zotero")):
            row(id, name, "unknown", "等待连接 Zotero 后检测")
        return {"checkedAt": datetime.now(timezone.utc).isoformat(), "checks": rows}

    row("zotero", "Zotero", "ok", "本地接口连接正常")
    try:
        health, _ = api.request("/api/users/0/litradar/health", timeout=4)
    except ZoteroUnavailable:
        health = None
    if isinstance(health, dict) and health.get("bridgeVersion"):
        rows[0]["version"] = health.get("zoteroVersion", "")
        row("bridge", "Litradar 桥接", "ok", "桥接已就绪", version=health["bridgeVersion"])
        for id, name in (("reading", "Reading List"), ("llm", "llm-for-zotero")):
            value = health.get(id, {})
            if value.get("ready"):
                row(id, name, "ok", "已就绪", version=value.get("version", ""))
            else:
                message = "未安装" if not value.get("installed") else "未启用" if not value.get("enabled") else "已启用，但初始化未完成"
                action = f"在 Zotero 的工具 → 插件中安装或启用 {name}" if not value.get("enabled") else "重启 Zotero 后重新检测"
                if "尚不受" in value.get("error", ""):
                    message, action = "当前版本尚未适配", "更新 Litradar 桥接至支持此版本的版本"
                row(id, name, "error", message, action, value.get("version", ""), value.get("error", ""))
    else:
        # Older bridges: inspect their existing endpoints without writing to Zotero.
        found = False
        version = ""
        for id, name, path in (("reading", "Reading List", "reading?metadata=1"), ("llm", "llm-for-zotero", "llm-context")):
            try:
                value, _ = api.request("/api/users/0/litradar/" + path, timeout=4)
                if not isinstance(value, dict) or value.get("provider") not in ("reading-list", "llm-for-zotero"):
                    raise ZoteroUnavailable("桥接返回格式异常")
                found = True
                version = value.get("bridgeVersion", "") or version
                row(id, name, "ok", "已就绪", version=value.get("version", ""))
            except ZoteroUnavailable as exc:
                detail = str(exc)
                found = found or "HTTP 503" in detail
                incompatible = "尚不受" in detail
                match = re.search(r"llm-for-zotero (\d+\.\d+\.\d+)", detail)
                row(id, name, "error", "当前版本尚未适配" if incompatible else "尚未就绪",
                    "更新 Litradar 桥接以适配当前版本" if incompatible else "确认插件已启用，重启 Zotero 后重新检测；升级桥接可查看具体原因",
                    match[1] if match else "", detail)
        row("bridge", "Litradar 桥接", "ok" if found else "unknown",
            "桥接可访问；建议更新以获得完整诊断" if found else "未确认桥接可用", "安装或更新 Litradar Zotero Bridge 0.3.1", version)
        rows.sort(key=lambda value: ["zotero", "bridge", "reading", "llm"].index(value["id"]))
    return {"checkedAt": datetime.now(timezone.utc).isoformat(), "checks": rows}
