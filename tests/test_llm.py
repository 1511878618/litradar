"""LLM wiring: wire format, failure degradation, and screen.json persistence."""

from __future__ import annotations

import functools
import http.server
import json
import os
import threading
from contextlib import contextmanager

from helpers import RDF_FEED, make_config, make_context, temp_dir, write_journal

import litradar.llm as llm
from litradar.config import load_journals
from litradar.pipeline import fetch_all, render_report
from litradar.state import read_json
from litradar.window import Window


class _ChatHandler(http.server.BaseHTTPRequestHandler):
    reply: dict = {}
    status: int = 200
    seen: list = []

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(body)}
        )
        payload = json.dumps(self.reply).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


@contextmanager
def chat_server(reply: dict, status: int = 200):
    _ChatHandler.reply = reply
    _ChatHandler.status = status
    _ChatHandler.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_ChatHandler))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", _ChatHandler
    finally:
        server.shutdown()
        server.server_close()


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content, "role": "assistant"}}]}


def test_chat_json_roundtrip():
    reply = _completion(json.dumps({"results": [{"id": "sha1:x", "relevant": True}]}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key_env": "LITRADAR_TEST_KEY",
                                           "max_retries": 0})
            os.environ["LITRADAR_TEST_KEY"] = "sk-test-000000000000"
            try:
                payload = llm.chat_json(config, [{"role": "user", "content": "hi"}])
            finally:
                os.environ.pop("LITRADAR_TEST_KEY", None)
            assert payload["results"][0]["id"] == "sha1:x"
            call = handler.seen[-1]
            assert call["path"].endswith("/chat/completions")
            assert call["auth"].startswith("Bearer sk-")
            assert call["body"]["model"] == "deepseek-chat"
            assert call["body"]["response_format"] == {"type": "json_object"}


def test_chat_json_accepts_fenced_output():
    fenced = "```json\n" + json.dumps({"results": []}) + "\n```"
    with chat_server(_completion(fenced)) as (base, _):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key_env": "LITRADAR_TEST_KEY",
                                           "max_retries": 0})
            os.environ["LITRADAR_TEST_KEY"] = "sk-test-000000000000"
            try:
                assert llm.chat_json(config, []) == {"results": []}
            finally:
                os.environ.pop("LITRADAR_TEST_KEY", None)


def test_missing_key_is_a_clean_error():
    with temp_dir() as tmp:
        config = make_config(tmp, llm={"api_key_env": "LITRADAR_TEST_KEY_UNSET"})
        os.environ.pop("LITRADAR_TEST_KEY_UNSET", None)
        try:
            llm.chat_json(config, [])
        except llm.LlmError as exc:
            assert "环境变量" in str(exc)
            return
    raise AssertionError("expected LlmError")


def test_http_error_is_wrapped():
    with chat_server({"error": "boom"}, status=500) as (base, _):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key_env": "LITRADAR_TEST_KEY",
                                           "max_retries": 0})
            os.environ["LITRADAR_TEST_KEY"] = "sk-test-000000000000"
            try:
                llm.chat_json(config, [])
            except llm.LlmError as exc:
                assert "HTTP 500" in str(exc)
                return
            finally:
                os.environ.pop("LITRADAR_TEST_KEY", None)
    raise AssertionError("expected LlmError")


def _store(tmp):
    ctx = make_context(tmp, routes={"example.org/feed": RDF_FEED}, screening={"mode": "llm"})
    write_journal(tmp, {"name": "Example Genetics", "adapter": "rss",
                        "feed": "https://example.org/feed", "surface": "Latest"})
    window = Window(kind="day", start=__import__("datetime").date(2026, 10, 30),
                    end=__import__("datetime").date(2026, 10, 30))
    summary = fetch_all(ctx, window, load_journals(ctx.config))
    assert summary.new_items
    return ctx, summary.new_items[0]


def test_render_persists_screen_json_and_uses_it():
    with temp_dir() as tmp:
        ctx, article = _store(tmp)
        results = [{
            "id": article.id,
            "relevant": True,
            "relevance": 0.88,
            "why": "正选择扫描",
            "topic_tags": ["正选择"],
            "abstract_en": article.abstract_raw,
            "abstract_mode": "verbatim",
        }]
        original = llm.chat_json
        llm.chat_json = lambda *args, **kwargs: {"results": results}
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        assert summary.highlights == 1
        assert "llm-v1" in summary.screening
        screen = read_json(ctx.paths.screen_file("2026-10-30"))
        assert screen["results"][0]["id"] == article.id
        assert screen["model"] == "deepseek-chat"
        assert ctx.paths.screen_file("2026-10-30").exists()


def test_render_degrades_to_keyword_without_key():
    with temp_dir() as tmp:
        ctx, _ = _store(tmp)
        os.environ.pop("LITRADAR_API_KEY", None)
        summary = render_report(ctx, "2026-10-30", use_llm=True)
        assert "keyword" in summary.screening
        report = summary.report_path.read_text(encoding="utf-8")
        assert "关键词降级" in report or "跳过模型" in report
        assert not ctx.paths.screen_file("2026-10-30").exists()


def test_screen_file_reused_without_calling_model():
    with temp_dir() as tmp:
        ctx, article = _store(tmp)
        ctx.paths.run_dir("2026-10-30").mkdir(parents=True, exist_ok=True)
        ctx.paths.screen_file("2026-10-30").write_text(
            json.dumps({
                "schema": 2,
                "run_id": "2026-10-30",
                "model": "manual",
                "screening": "external",
                "results": [{"id": article.id, "relevant": True, "relevance": 0.9}],
            }),
            encoding="utf-8",
        )
        calls = []
        original = llm.chat_json
        llm.chat_json = lambda *args, **kwargs: calls.append(1) or {"results": []}
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        assert calls == []
        assert "external" in summary.screening
        assert summary.highlights == 1


def test_max_items_truncation_is_noted():
    with temp_dir() as tmp:
        ctx, _ = _store(tmp)
        ctx.config.raw.setdefault("screening", {})["max_items"] = 0
        original = llm.chat_json
        llm.chat_json = lambda *args, **kwargs: {"results": []}
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        report = summary.report_path.read_text(encoding="utf-8")
        assert "截断" in report


def test_all_results_rejected_marks_screen_failure():
    with temp_dir() as tmp:
        ctx, _ = _store(tmp)
        original = llm.chat_json
        llm.chat_json = lambda *args, **kwargs: {"results": [{"id": "sha1:0000000000000000"}]}
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        assert summary.screen_rejected_all is True
        assert summary.rejected == 1
        assert summary.report_path.exists()
        assert "## 校验记录" in summary.report_path.read_text(encoding="utf-8")


def test_json_mode_can_be_disabled():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key_env": "LITRADAR_TEST_KEY",
                                           "max_retries": 0, "json_mode": False})
            os.environ["LITRADAR_TEST_KEY"] = "sk-test-000000000000"
            try:
                llm.chat_json(config, [{"role": "user", "content": "hi"}])
            finally:
                os.environ.pop("LITRADAR_TEST_KEY", None)
            assert "response_format" not in handler.seen[-1]["body"]


def test_json_mode_on_by_default():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key_env": "LITRADAR_TEST_KEY",
                                           "max_retries": 0})
            os.environ["LITRADAR_TEST_KEY"] = "sk-test-000000000000"
            try:
                llm.chat_json(config, [{"role": "user", "content": "hi"}])
            finally:
                os.environ.pop("LITRADAR_TEST_KEY", None)
            assert handler.seen[-1]["body"]["response_format"] == {"type": "json_object"}


def test_api_key_can_come_from_config_file():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            config = make_config(tmp, llm={"base_url": base, "api_key": "sk-inline-abcdef123456",
                                           "max_retries": 0})
            llm.chat_json(config, [{"role": "user", "content": "hi"}])
            assert handler.seen[-1]["auth"] == "Bearer sk-inline-abcdef123456"


def test_api_key_file_is_supported():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            key_file = tmp / "deepseek.key"
            key_file.write_text("sk-from-file-123456\n", encoding="utf-8")
            config = make_config(tmp, llm={"base_url": base, "api_key_file": str(key_file),
                                           "max_retries": 0})
            llm.chat_json(config, [{"role": "user", "content": "hi"}])
            assert handler.seen[-1]["auth"] == "Bearer sk-from-file-123456"


def test_api_key_env_placeholder_expands():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            os.environ["LITRADAR_EXPAND_KEY"] = "sk-expanded-123456"
            config = make_config(tmp, llm={"base_url": base, "api_key": "${LITRADAR_EXPAND_KEY}",
                                           "max_retries": 0})
            try:
                llm.chat_json(config, [{"role": "user", "content": "hi"}])
            finally:
                os.environ.pop("LITRADAR_EXPAND_KEY", None)
            assert handler.seen[-1]["auth"] == "Bearer sk-expanded-123456"


def test_inline_key_wins_over_env():
    reply = _completion(json.dumps({"results": []}))
    with chat_server(reply) as (base, handler):
        with temp_dir() as tmp:
            os.environ["LITRADAR_PRECEDENCE_KEY"] = "sk-from-env-999999"
            config = make_config(tmp, llm={"base_url": base, "api_key": "sk-inline-777777",
                                           "api_key_env": "LITRADAR_PRECEDENCE_KEY",
                                           "max_retries": 0})
            try:
                llm.chat_json(config, [{"role": "user", "content": "hi"}])
            finally:
                os.environ.pop("LITRADAR_PRECEDENCE_KEY", None)
            assert handler.seen[-1]["auth"] == "Bearer sk-inline-777777"


def test_missing_key_error_mentions_config_option():
    with temp_dir() as tmp:
        config = make_config(tmp, llm={"api_key_env": "LITRADAR_NOT_SET_ANYWHERE"})
        os.environ.pop("LITRADAR_NOT_SET_ANYWHERE", None)
        try:
            llm.chat_json(config, [])
        except llm.LlmError as exc:
            assert "llm.api_key" in str(exc)
            return
    raise AssertionError("expected LlmError")


def test_summary_zh_from_model_is_rendered():
    with temp_dir() as tmp:
        ctx, article = _store(tmp)
        results = [{
            "id": article.id,
            "relevant": True,
            "relevance": 0.9,
            "why": "与方向相关",
            "summary_zh": "用群体基因组数据识别了正选择位点",
            "abstract_en": article.abstract_raw,
            "abstract_mode": "verbatim",
        }]
        original = llm.chat_json
        llm.chat_json = lambda *args, **kwargs: {"results": results}
        try:
            summary = render_report(ctx, "2026-10-30", use_llm=True)
        finally:
            llm.chat_json = original
        text = summary.report_path.read_text(encoding="utf-8")
        assert "一句话解读：用群体基因组数据识别了正选择位点" in text
