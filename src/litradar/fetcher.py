"""HTTP client: retries, per-host rate limiting, disk cache, honest failure reasons.

Adapters never talk to the network themselves — they call ``Fetcher.get`` so that
timeouts, caching and politeness behave identically everywhere.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_UA = "litradar/2.0 (personal research use; +https://github.com/wanglab/litradar)"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class Response:
    url: str
    status: int | None = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    from_cache: bool = False
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and not self.error

    @property
    def reason(self) -> str:
        """One human sentence that goes straight into the report and the plugin."""
        if self.error:
            return self.error
        if self.status is None:
            return "没有拿到 HTTP 响应"
        if self.status == 403:
            lowered = (self.text or "")[:4000].lower()
            if "cloudflare" in lowered or "captcha" in lowered or "just a moment" in lowered:
                return "HTTP 403 (Cloudflare / bot protection)"
            return "HTTP 403（站点拒绝访问）"
        if self.status == 404:
            return "HTTP 404（页面不存在，可能改版）"
        if self.status == 429:
            return "HTTP 429（被限流，稍后重试）"
        if self.status >= 500:
            return f"HTTP {self.status}（服务端错误）"
        return f"HTTP {self.status}"


class Fetcher:
    def __init__(
        self,
        http_cfg: dict[str, Any] | None = None,
        *,
        cache_dir: Path | str | None = None,
        no_cache: bool = False,
        timeout_s: float | None = None,
        logger: Any = None,
    ) -> None:
        cfg = dict(http_cfg or {})
        self.timeout = float(timeout_s if timeout_s is not None else cfg.get("timeout_s", 25))
        self.retries = max(0, int(cfg.get("retries", 2)))
        self.rate_limit = max(0.0, float(cfg.get("rate_limit_s", 2)))
        self.user_agent = str(cfg.get("user_agent") or DEFAULT_UA)
        self.cache_ttl = float(cfg.get("cache_ttl_hours", 12)) * 3600
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.no_cache = no_cache or self.cache_dir is None
        self.logger = logger
        # Optional wall-clock deadline shared by an adapter's whole fetch call
        # (``pipeline`` sets it to enforce the per-journal 60s budget).
        self.deadline: float | None = None
        self._last_request: dict[str, float] = {}
        if self.cache_dir is not None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Read-only vault / TCC: keep working, just without a disk cache.
                self.cache_dir = None
                self.no_cache = True

    # ---- public -----------------------------------------------------------
    def get(self, url: str, headers: dict[str, str] | None = None, *, cache_ttl: float | None = None) -> Response:
        started = time.monotonic()
        cached = self._cache_read(url, cache_ttl)
        if cached is not None:
            cached.elapsed_ms = 0
            return cached

        merged = {
            "User-Agent": self.user_agent,
            "Accept": BROWSER_ACCEPT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        merged.update(headers or {})

        response = Response(url=url)
        for attempt in range(self.retries + 1):
            self._respect_rate_limit(url)
            response = self._request(url, merged)
            if response.ok:
                self._cache_write(url, response)
                break
            retryable = response.status in (429, 500, 502, 503, 504) or response.status is None
            if not retryable or attempt >= self.retries:
                break
            if self.deadline is not None and time.monotonic() >= self.deadline:
                break
            backoff = 2**attempt
            if self.logger:
                self.logger.debug(f"{url} 失败（{response.reason}），{backoff}s 后重试")
            time.sleep(backoff)
        response.elapsed_ms = int((time.monotonic() - started) * 1000)
        return response

    def get_bytes(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[bytes | None, int | None, str | None, str | None]:
        """Binary GET for images: returns (content, status, content_type, error)."""
        merged = {
            "User-Agent": self.user_agent,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        merged.update(headers or {})
        for attempt in range(self.retries + 1):
            self._respect_rate_limit(url)
            request = urllib.request.Request(url, headers=merged, method="GET")
            timeout = self._timeout()
            try:
                with urllib.request.urlopen(request, timeout=timeout) as raw:
                    content = raw.read()
                    status = getattr(raw, "status", None) or raw.getcode()
                    content_type = (raw.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                return content, status, content_type, None
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2**attempt)
                    continue
                return None, exc.code, None, f"HTTP {exc.code}"
            except TimeoutError:
                if attempt < self.retries:
                    continue
                return None, None, None, f"请求超时（{timeout:g}s）"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return None, None, None, f"请求失败：{exc}"
        return None, None, None, "请求失败"

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[Any, Response]:
        merged = {"Accept": "application/json"}
        merged.update(headers or {})
        response = self.get(url, merged)
        if not response.ok:
            return None, response
        try:
            return json.loads(response.text), response
        except json.JSONDecodeError:
            response.error = "响应不是合法 JSON"
            return None, response

    def close(self) -> None:  # pragma: no cover - symmetry for tests/front-ends
        return None

    # ---- internals --------------------------------------------------------
    def _respect_rate_limit(self, url: str) -> None:
        if self.rate_limit <= 0:
            return
        host = urllib.parse.urlparse(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = self.rate_limit - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _timeout(self) -> float:
        if self.deadline is None:
            return self.timeout
        return max(1.0, min(self.timeout, self.deadline - time.monotonic()))

    def _request(self, url: str, headers: dict[str, str]) -> Response:
        request = urllib.request.Request(url, headers=headers, method="GET")
        timeout = self._timeout()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:
                body = raw.read()
                status = getattr(raw, "status", None) or raw.getcode()
                response_headers = {k.lower(): v for k, v in raw.headers.items()}
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            status = exc.code
            response_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            text = self._decode(body, response_headers)
            return Response(url=url, status=status, text=text, headers=response_headers)
        except TimeoutError:
            return Response(url=url, error=f"请求超时（{self.timeout:g}s）")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                return Response(url=url, error=f"请求超时（{timeout:g}s）")
            if isinstance(reason, ssl.SSLError):
                if self._tls_eof(reason):
                    return self._curl_request(url, headers, timeout)
                return Response(url=url, error=f"TLS 握手失败：{reason}")
            text = str(reason)
            if "Name or service not known" in text or "nodename nor servname" in text:
                return Response(url=url, error="域名解析失败（检查网络或代理）")
            return Response(url=url, error=f"连接失败：{text}")
        except ssl.SSLError as exc:
            if self._tls_eof(exc):
                return self._curl_request(url, headers, timeout)
            return Response(url=url, error=f"TLS 握手失败：{exc}")
        except (OSError, ValueError) as exc:
            return Response(url=url, error=f"请求失败：{exc}")

        text = self._decode(body, response_headers)
        return Response(url=url, status=status, text=text, headers=response_headers)

    @staticmethod
    def _tls_eof(error: ssl.SSLError) -> bool:
        return isinstance(error, ssl.SSLEOFError) or "UNEXPECTED_EOF_WHILE_READING" in str(error)

    def _curl_request(self, url: str, headers: dict[str, str], timeout: float) -> Response:
        """Retry TLS EOF with the system TLS stack; certificate validation stays on."""
        if urllib.parse.urlsplit(url).scheme != "https":
            return Response(url=url, error="TLS 回退仅支持 HTTPS")
        budget = self._timeout() if self.deadline is not None else timeout
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return Response(url=url, error="TLS 重试前已达到抓取时间上限")
        with tempfile.TemporaryDirectory(prefix="litradar-http-") as temp:
            body_path, header_path = Path(temp) / "body", Path(temp) / "headers"
            args = ["curl", "--disable", "--silent", "--show-error", "--location", "--max-redirs", "5",
                    "--proto", "=https", "--proto-redir", "=https", "--compressed",
                    "--connect-timeout", str(min(10, budget)), "--max-time", str(budget),
                    "--output", str(body_path), "--dump-header", str(header_path),
                    "--write-out", "%{http_code}"]
            for name, value in headers.items():
                if name.lower() != "accept-encoding":
                    args.extend(["--header", f"{name}: {value}"])
            args.extend(["--url", url])
            try:
                result = subprocess.run(args, capture_output=True, timeout=budget + 1, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return Response(url=url, error=f"TLS 连接中断，系统 TLS 重试失败：{type(exc).__name__}")
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace")[:250]
                return Response(url=url, error=f"TLS 连接中断，系统 TLS 重试失败：{detail}")
            try:
                status = int(result.stdout.decode().strip())
                raw_headers = header_path.read_text(errors="replace").replace("\r\n", "\n")
                block = raw_headers.strip().split("\n\n")[-1]
                response_headers = {key.lower().strip(): value.strip() for line in block.splitlines()[1:]
                                    if ":" in line for key, value in [line.split(":", 1)]}
                # curl --compressed has already decompressed the response.
                response_headers.pop("content-encoding", None)
                text = self._decode(body_path.read_bytes(), response_headers)
                return Response(url=url, status=status, text=text, headers=response_headers)
            except (OSError, ValueError) as exc:
                return Response(url=url, error=f"系统 TLS 响应解析失败：{exc}")

    @staticmethod
    def _decode(body: bytes, headers: dict[str, str]) -> str:
        encoding = (headers.get("content-encoding") or "").lower()
        if encoding == "gzip":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        elif encoding == "deflate":
            try:
                body = zlib.decompress(body)
            except zlib.error:
                pass
        charset = "utf-8"
        content_type = headers.get("content-type") or ""
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip().strip('"')
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    # ---- cache ------------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if self.no_cache or self.cache_dir is None:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _cache_read(self, url: str, ttl: float | None) -> Response | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        ttl = self.cache_ttl if ttl is None else ttl
        if ttl >= 0 and _now_ts() - float(payload.get("fetched_at", 0)) > ttl:
            return None
        if int(payload.get("status") or 0) != 200:
            return None
        return Response(
            url=url,
            status=payload.get("status"),
            text=payload.get("text") or "",
            headers=payload.get("headers") or {},
            from_cache=True,
        )

    def _cache_write(self, url: str, response: Response) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        payload = {
            "url": url,
            "status": response.status,
            "headers": response.headers,
            "text": response.text,
            "fetched_at": _now_ts(),
        }
        tmp = path.with_name(f".{path.name}.{id(self)}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
