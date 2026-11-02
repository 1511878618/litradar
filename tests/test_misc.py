"""Small modules: paths, events, logs, embed guard rails, Zotero mapping, doctor."""

import io
import json
import time
from datetime import date
from pathlib import Path

from helpers import make_config, make_context, sample_article, temp_dir

from litradar import __version__
from litradar.config import Config, write_skeleton
from litradar.doctor import has_errors, run_doctor, summarize
from litradar.embed import EmbedUnavailable, profile_texts, scores_payload, top_k
from litradar.events import Emitter
from litradar.logs import Logger, rotate_logs
from litradar.paths import Paths, expand_template
from litradar.util import ConfigError
from litradar.window import Window
from litradar.zotero import to_zotero_item

WINDOW = Window(kind="day", start=date(2026, 10, 30), end=date(2026, 10, 30))


# ------------------------------------------------------------------ paths
def test_report_path_default_template():
    with temp_dir() as tmp:
        paths = Paths(tmp, make_config(tmp))
        target = paths.report_file(WINDOW)
        assert target == Path(tmp).resolve() / "02-Reading" / "文献雷达" / "2026" / "10" / "2026-10-30.md"


def test_report_path_custom_template():
    with temp_dir() as tmp:
        config = make_config(tmp, output={"report_dir": "radar/{yyyy}", "filename": "daily-{date}"})
        target = Paths(tmp, config).report_file(WINDOW)
        assert target.name == "daily-2026-10-30.md"
        assert target.parent.name == "2026"


def test_report_path_rejects_unknown_placeholder():
    with temp_dir() as tmp:
        config = make_config(tmp, output={"report_dir": "radar/{quarter}"})
        try:
            Paths(tmp, config).report_file(WINDOW)
        except ConfigError as exc:
            assert "quarter" in str(exc)
            return
    raise AssertionError("expected ConfigError")


def test_expand_template_uses_window_end():
    assert expand_template("{yyyy}/{MM}/{dd}", WINDOW) == "2026/10/30"


def test_paths_ensure_creates_layout():
    with temp_dir() as tmp:
        paths = Paths(tmp)
        paths.ensure()
        for directory in (paths.store_dir, paths.runs_dir, paths.state_dir, paths.logs_dir):
            assert directory.is_dir()


# ------------------------------------------------------------------ events
def test_emitter_writes_one_json_object_per_line():
    buffer = io.StringIO()
    emitter = Emitter(True, stream=buffer)
    emitter.run_start("2026-10-30", "run")
    emitter.emit("journal_done", journal="J", status="ok", http=200, reason=None)
    emitter.run_end(True, 0)
    lines = [json.loads(line) for line in buffer.getvalue().splitlines()]
    assert lines[0]["event"] == "run_start"
    assert lines[0]["engine"] == __version__
    assert lines[0]["schema"] == 2
    assert "reason" not in lines[1]  # None fields are omitted
    assert lines[-1] == {"event": "run_end", "ok": True, "exit": 0}


def test_emitter_disabled_is_silent():
    buffer = io.StringIO()
    emitter = Emitter(False, stream=buffer)
    emitter.run_start("x", "run")
    assert buffer.getvalue() == ""


def test_emitter_survives_a_closed_stream():
    class Broken:
        def write(self, *_args):
            raise OSError("pipe closed")

        def flush(self):
            raise OSError("pipe closed")

    emitter = Emitter(True, stream=Broken())
    emitter.emit("progress", stage="screen", done=1, total=2)
    assert emitter.failed is True


# ------------------------------------------------------------------ logs
def test_logger_redacts_secrets():
    buffer = io.StringIO()
    logger = Logger(level="info", stream=buffer, timestamp=False)
    logger.info("key=sk-abcdef0123456789 done")
    assert "sk-abcdef0123456789" not in buffer.getvalue()
    assert "sk-***" in buffer.getvalue()


def test_logger_writes_to_file():
    with temp_dir() as tmp:
        log = tmp / "logs" / "a.log"
        logger = Logger(level="info", stream=io.StringIO(), file_path=log, timestamp=False)
        logger.warn("hello")
        logger.close()
        assert "WARN  hello" in log.read_text(encoding="utf-8")


def test_rotate_logs_removes_old_files():
    with temp_dir() as tmp:
        old = tmp / "cli-2020-01-01.log"
        old.write_text("old", encoding="utf-8")
        ten_days = time.time() - 10 * 86400
        import os

        os.utime(old, (ten_days, ten_days))
        fresh = tmp / "cli-2026-10-31.log"
        fresh.write_text("new", encoding="utf-8")
        removed = rotate_logs(tmp, keep_days=7)
        assert old in removed
        assert fresh.exists()


# ------------------------------------------------------------------ embed
def test_embed_without_profile_raises_clean_error():
    with temp_dir() as tmp:
        config = Config({"embedding": {"profile": ""}, "direction": ""}, tmp)
        try:
            profile_texts(config, Paths(tmp, config))
        except EmbedUnavailable as exc:
            assert "profile" in str(exc) or "direction" in str(exc)
            return
    raise AssertionError("expected EmbedUnavailable")


def test_embed_profile_from_text():
    with temp_dir() as tmp:
        config = Config({"embedding": {"profile": "text:进化基因组学；正选择"}}, tmp)
        texts = profile_texts(config, Paths(tmp, config))
        assert texts == ["进化基因组学", "正选择"]


def test_scores_payload_shape():
    with temp_dir() as tmp:
        config = make_config(tmp)
        payload = scores_payload(config, {"10.1/a": 0.7}, 3)
        assert payload["schema"] == 2
        assert payload["scores"]["10.1/a"] == 0.7
        assert payload["profile"]["n_items"] == 3


def test_top_k_sorted():
    scores = {"a": 0.1, "b": 0.9, "c": 0.5}
    assert [key for key, _ in top_k(scores, 2)] == ["b", "c"]


# ------------------------------------------------------------------ zotero
def test_zotero_item_mapping():
    article = sample_article(doi="10.1/zz", authors=["Ann Lee", "Cher"])
    item = to_zotero_item(article)
    assert item["itemType"] == "journalArticle"
    assert item["DOI"] == "10.1/zz"
    assert item["creators"][1] == {"creatorType": "author", "name": "Cher"}
    assert item["creators"][0]["lastName"] == "Lee"


def test_zotero_item_omits_empty_fields():
    article = sample_article(abstract_raw=None, doi=None)
    item = to_zotero_item(article)
    assert "abstractNote" not in item
    assert "DOI" not in item


# ------------------------------------------------------------------ doctor
def test_doctor_reports_uninitialised_vault():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        checks = run_doctor(ctx, network=False)
        assert has_errors(checks)
        assert "vault" in {check.name for check in checks if not check.ok}


def test_doctor_passes_on_initialised_vault():
    with temp_dir() as tmp:
        write_skeleton(tmp)
        ctx = make_context(tmp)
        checks = run_doctor(ctx, network=False)
        assert not has_errors(checks), [check.to_dict() for check in checks if not check.ok]
        assert summarize(checks)


def test_doctor_json_serialisable():
    with temp_dir() as tmp:
        write_skeleton(tmp)
        ctx = make_context(tmp)
        payload = [check.to_dict() for check in run_doctor(ctx, network=False)]
        assert json.loads(json.dumps(payload))


def test_logger_redacts_configured_secrets():
    buffer = io.StringIO()
    logger = Logger(level="info", stream=buffer, timestamp=False)
    logger.add_secret("AIzaSy-very-secret-value")
    logger.info("key=AIzaSy-very-secret-value done")
    assert "AIzaSy-very-secret-value" not in buffer.getvalue()
    assert "***" in buffer.getvalue()


def test_doctor_warns_about_inline_api_key():
    from litradar.doctor import _check_llm

    with temp_dir() as tmp:
        write_skeleton(tmp)
        ctx = make_context(tmp)
        ctx.config.raw["llm"]["api_key"] = "sk-inline-abcdef123456"
        check = _check_llm(ctx)
        assert check.name == "llm"
        assert check.severity == "warn"
        assert "config.yaml" in check.detail
