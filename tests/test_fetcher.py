"""HTTP client: caching, retries and honest failure reasons."""

import json

from helpers import temp_dir

import litradar.fetcher as fetcher_module
from litradar.fetcher import Fetcher, Response


def _fetcher(tmp, **kwargs):
    return Fetcher({"rate_limit_s": 0, "retries": 2, "cache_ttl_hours": 12},
                   cache_dir=tmp / "cache", **kwargs)


def test_reason_for_cloudflare_403():
    response = Response(url="u", status=403, text="<html>Just a moment... cloudflare</html>")
    assert "Cloudflare" in response.reason


def test_reason_for_plain_403():
    assert Response(url="u", status=403, text="<html>forbidden</html>").reason.startswith("HTTP 403")


def test_reason_for_404_and_429():
    assert "404" in Response(url="u", status=404).reason
    assert "限流" in Response(url="u", status=429).reason


def test_ok_requires_2xx_and_no_error():
    assert Response(url="u", status=200).ok
    assert not Response(url="u", status=200, error="x").ok
    assert not Response(url="u", status=None).ok


def test_cache_hit_avoids_second_request():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        calls = []
        fetcher._request = lambda url, headers: (calls.append(url) or Response(url=url, status=200, text="body"))
        first = fetcher.get("https://example.org/x")
        second = fetcher.get("https://example.org/x")
        assert first.text == "body"
        assert second.from_cache is True
        assert len(calls) == 1


def test_no_cache_forces_request():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp, no_cache=True)
        calls = []
        fetcher._request = lambda url, headers: (calls.append(url) or Response(url=url, status=200, text="body"))
        fetcher.get("https://example.org/x")
        fetcher.get("https://example.org/x")
        assert len(calls) == 2


def test_retries_on_503_then_succeeds():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        sleep_calls = []
        original_sleep = fetcher_module.time.sleep
        fetcher_module.time.sleep = lambda seconds: sleep_calls.append(seconds)
        responses = [Response(url="u", status=503), Response(url="u", status=503),
                     Response(url="u", status=200, text="ok")]
        fetcher._request = lambda url, headers: responses.pop(0)
        try:
            response = fetcher.get("https://example.org/y")
        finally:
            fetcher_module.time.sleep = original_sleep
        assert response.ok
        assert sleep_calls == [1, 2]


def test_no_retry_on_403():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        calls = []
        fetcher._request = lambda url, headers: (calls.append(url) or Response(url="u", status=403, text="nope"))
        response = fetcher.get("https://example.org/z")
        assert not response.ok
        assert len(calls) == 1


def test_get_json_parses_and_reports_broken_payload():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        fetcher._request = lambda url, headers: Response(url=url, status=200, text=json.dumps({"a": 1}))
        payload, response = fetcher.get_json("https://example.org/j")
        assert payload == {"a": 1}
        assert response.ok

        fetcher2 = _fetcher(tmp / "other")
        fetcher2._request = lambda url, headers: Response(url=url, status=200, text="<html>not json</html>")
        payload, response = fetcher2.get_json("https://example.org/k")
        assert payload is None
        assert response.error


def test_timeout_reason():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        fetcher._request = lambda url, headers: Response(url="u", error="请求超时（25s）")
        response = fetcher.get("https://example.org/slow")
        assert "超时" in response.reason


def test_cache_ignores_expired_entries():
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        fetcher._request = lambda url, headers: Response(url=url, status=200, text="one")
        fetcher.get("https://example.org/exp")
        # Rewrite the cache entry as if it were fetched a week ago.
        for path in (tmp / "cache").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["fetched_at"] = 0
            path.write_text(json.dumps(payload), encoding="utf-8")
        calls = []
        fetcher._request = lambda url, headers: (calls.append(url) or Response(url=url, status=200, text="two"))
        response = fetcher.get("https://example.org/exp")
        assert response.text == "two"
        assert response.from_cache is False


def test_fetcher_survives_an_unwritable_cache_dir():
    from pathlib import Path
    from unittest.mock import patch

    # Windows chmod does not emulate a POSIX unwritable directory. Inject the
    # actual filesystem error so the fallback is exercised on every platform.
    with temp_dir() as tmp:
        with patch.object(Path, "mkdir", side_effect=PermissionError("read-only cache")):
            fetcher = Fetcher({}, cache_dir=tmp / "cache")
        assert fetcher.cache_dir is None
        assert fetcher.no_cache is True



def test_tls_eof_uses_system_transport_but_certificate_errors_do_not():
    import ssl
    import urllib.error
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        original = fetcher_module.urllib.request.urlopen
        calls = []
        fetcher._curl_request = lambda url, headers, timeout: (calls.append(url) or Response(url=url, status=200, text='ok'))
        def eof(*args, **kwargs):
            raise urllib.error.URLError(ssl.SSLEOFError('UNEXPECTED_EOF_WHILE_READING'))
        fetcher_module.urllib.request.urlopen = eof
        try:
            assert fetcher._request('https://example.org/', {}).ok
            assert calls == ['https://example.org/']
            def certificate(*args, **kwargs):
                raise urllib.error.URLError(ssl.SSLCertVerificationError('certificate verify failed'))
            fetcher_module.urllib.request.urlopen = certificate
            response = fetcher._request('https://example.org/', {})
            assert not response.ok and len(calls) == 1
        finally:
            fetcher_module.urllib.request.urlopen = original


def test_system_tls_transport_preserves_status_and_decodes_decompressed_body():
    import subprocess
    from pathlib import Path
    with temp_dir() as tmp:
        fetcher = _fetcher(tmp)
        original = fetcher_module.subprocess.run
        def run(args, **kwargs):
            assert '--insecure' not in args and '-k' not in args
            assert args[1] == '--disable'
            assert args[args.index('--proto-redir') + 1] == '=https'
            Path(args[args.index('--output') + 1]).write_text('{"ok": true}')
            Path(args[args.index('--dump-header') + 1]).write_text('HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Type: application/json\r\n\r\n')
            return subprocess.CompletedProcess(args, 0, stdout=b'200', stderr=b'')
        fetcher_module.subprocess.run = run
        try:
            response = fetcher._curl_request('https://example.org/', {}, 4)
            assert response.ok and response.text == '{"ok": true}'
        finally:
            fetcher_module.subprocess.run = original
