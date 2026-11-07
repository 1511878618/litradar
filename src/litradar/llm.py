"""OpenAI-compatible chat client — one dependency-free POST.

Keys are read from the environment only (``llm.api_key_env`` names the variable), are
never written to disk, and are redacted before anything reaches a log.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .util import redact


class LlmError(Exception):
    """Any reason the model could not answer — always degradable, never fatal."""


_CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """Rough token count: one per CJK char, four per other char (no tokenizer dep)."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 4) + 1


def new_usage() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "estimated": False}


def merge_usage(target: dict, source: dict | None) -> dict:
    if not source:
        return target
    for key in ("calls", "prompt_tokens", "completion_tokens"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
    if source.get("calls"):
        target["estimated"] = bool(target.get("estimated") or source.get("estimated"))
    return target


def cost_cny(usage: dict, cfg: Any) -> float:
    """Estimated CNY cost from ``llm.pricing`` (example prices by default)."""
    try:
        input_price = float(cfg.get("llm.pricing.input_per_million", 0) or 0)
        output_price = float(cfg.get("llm.pricing.output_per_million", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(
        int(usage.get("prompt_tokens", 0)) / 1_000_000 * input_price
        + int(usage.get("completion_tokens", 0)) / 1_000_000 * output_price,
        4,
    )


def _endpoint(base_url: str) -> str:
    base = (base_url or "https://api.deepseek.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _expand(raw: str) -> str:
    """Expand ``${VAR}`` / ``~``; unresolved references are an error, not a key."""
    expanded = os.path.expanduser(os.path.expandvars(raw.strip()))
    if "$" in expanded:
        raise LlmError(f"llm.api_key 引用了未设置的环境变量：{raw[:12]}…")
    return expanded


def api_key(cfg) -> str | None:
    """Key resolution order: ``llm.api_key`` → ``llm.api_key_file`` → env var.

    Putting a key in ``config.yaml`` is supported because it is the least
    surprising place for a desktop tool, but it lives inside the vault and may be
    synced — ``doctor`` warns about it, and the file-permission check is there for
    a reason.  ``api_key_env`` (or a key file outside the vault) remains preferred.
    """
    inline = str(cfg.get("llm.api_key", "") or "").strip()
    if inline:
        return _expand(inline) or None

    file_ref = str(cfg.get("llm.api_key_file", "") or "").strip()
    if file_ref:
        path = Path(os.path.expanduser(file_ref))
        if not path.exists():
            raise LlmError(f"llm.api_key_file 指向的文件不存在：{path}")
        try:
            value = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError as exc:
            raise LlmError(f"无法读取 llm.api_key_file：{exc}") from exc
        return (value[0].strip() if value else "") or None

    env_name = str(cfg.get("llm.api_key_env", "LITRADAR_API_KEY") or "")
    if not env_name:
        return None
    return os.environ.get(env_name, "").strip() or None


def chat_json(
    cfg,
    messages: list[dict[str, str]],
    *,
    logger: Any = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    usage: dict | None = None,
) -> Any:
    """POST /chat/completions and return the parsed JSON object."""
    key = api_key(cfg)
    if not key:
        raise LlmError(
            "未配置模型 API key：在 config.yaml 设置 llm.api_key / llm.api_key_file，"
            f"或设置环境变量 {cfg.get('llm.api_key_env', 'LITRADAR_API_KEY')}；已跳过模型筛选"
        )
    url = _endpoint(str(cfg.get("llm.base_url", "")))
    model = str(cfg.get("llm.model", "deepseek-chat"))
    timeout = float(timeout if timeout is not None else cfg.get("llm.timeout_s", 120))
    retries = int(max_retries if max_retries is not None else cfg.get("llm.max_retries", 2))

    request_body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": messages,
    }
    # Some models (reasoners, some "flash" variants) reject response_format.
    # Falling back to prompt-only JSON is handled by the parser below.
    if bool(cfg.get("llm.json_mode", True)):
        request_body["response_format"] = {"type": "json_object"}
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": f"litradar/{__version__}",
    }

    last_error = "未知错误"
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400] if hasattr(exc, "read") else ""
            last_error = f"HTTP {exc.code}: {redact(detail)}"
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise LlmError(f"模型接口失败（{last_error}）") from exc
        except TimeoutError as exc:
            last_error = f"请求超时（{timeout:g}s）"
            if attempt < retries:
                continue
            raise LlmError(f"模型接口失败（{last_error}）") from exc
        except urllib.error.URLError as exc:
            raise LlmError(f"模型接口不可达：{redact(str(getattr(exc, 'reason', exc)))}") from exc
        except (OSError, ValueError) as exc:
            raise LlmError(f"模型接口失败：{redact(str(exc))}") from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise LlmError(f"模型接口失败（{last_error}）")

    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LlmError(f"模型返回的不是 JSON：{redact(payload[:200])}") from exc
    if isinstance(envelope, dict) and envelope.get("error"):
        raise LlmError(f"模型返回错误：{redact(str(envelope['error'])[:300])}")
    try:
        content = envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError("模型响应缺少 choices[0].message.content") from exc
    if not content:
        raise LlmError("模型返回了空内容")
    if usage is not None:
        raw = envelope.get("usage") or {} if isinstance(envelope, dict) else {}
        prompt = raw.get("prompt_tokens") or raw.get("input_tokens")
        completion = raw.get("completion_tokens") or raw.get("output_tokens")
        usage["calls"] = int(usage.get("calls", 0)) + 1
        if prompt is None:
            usage["prompt_tokens"] = int(usage.get("prompt_tokens", 0)) + estimate_tokens(
                "".join(str(message.get("content") or "") for message in messages)
            )
            usage["estimated"] = True
        else:
            usage["prompt_tokens"] = int(usage.get("prompt_tokens", 0)) + int(prompt)
        if completion is None:
            usage["completion_tokens"] = int(usage.get("completion_tokens", 0)) + estimate_tokens(content)
            usage["estimated"] = True
        else:
            usage["completion_tokens"] = int(usage.get("completion_tokens", 0)) + int(completion)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Some providers wrap JSON in a fenced block despite response_format.
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            stripped = stripped.rsplit("```", 1)[0]
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LlmError("模型输出不是合法 JSON（可能是被截断）") from exc
