"""CLI behaviour against a real local HTTP server — exit codes are the contract."""

from __future__ import annotations

import functools
import http.server
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import yaml
from helpers import RDF_FEED, capture_stdout, temp_dir

from litradar.cli import main
from litradar.paths import Paths
from litradar.state import VaultLock


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, str] = {}

    def do_GET(self):  # noqa: N802 - stdlib naming
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence test output
        return


@contextmanager
def local_server(routes: dict[str, str]):
    _Handler.routes = routes
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _init(vault: Path) -> None:
    code, _ = capture_stdout(lambda: main(["init", "--vault", str(vault)]))
    assert code == 0


def _prepare(vault: Path, feed_url: str) -> None:
    """One vault, one journal, no network politeness delays."""
    config_path = vault / ".litradar" / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data.setdefault("http", {}).update({"rate_limit_s": 0, "retries": 0, "cache_ttl_hours": 0})
    data.setdefault("screening", {})["mode"] = "keyword"
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    journals = vault / ".litradar" / "journals"
    for path in journals.glob("*.yaml"):
        path.unlink()
    (journals / "example.yaml").write_text(
        yaml.safe_dump(
            {"name": "Example Genetics", "adapter": "rss", "feed": feed_url,
             "surface": "Latest Research"},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_version_json():
    code, out = capture_stdout(lambda: main(["--version", "--json"]))
    assert code == 0
    payload = json.loads(out)
    assert payload["engine"] == __import__("litradar").__version__
    assert payload["schema"] == 2


def test_init_creates_skeleton():
    with temp_dir() as tmp:
        code, out = capture_stdout(lambda: main(["init", "--vault", str(tmp)]))
        assert code == 0
        assert (tmp / ".litradar" / "config.yaml").exists()
        assert len(list((tmp / ".litradar" / "journals").glob("*.yaml"))) == 21


def test_journals_list_json():
    with temp_dir() as tmp:
        _init(tmp)
        code, out = capture_stdout(lambda: main(["journals", "list", "--vault", str(tmp), "--json"]))
        assert code == 0
        rows = json.loads(out)
        assert len(rows) == 21
        assert {row["adapter"] for row in rows} == {"rss", "crossref", "biorxiv"}


def test_doctor_on_uninitialised_vault_fails():
    with temp_dir() as tmp:
        code, _ = capture_stdout(lambda: main(["doctor", "--vault", str(tmp), "--offline"]))
        assert code == 5


def test_doctor_on_initialised_vault_passes_offline():
    with temp_dir() as tmp:
        _init(tmp)
        code, out = capture_stdout(lambda: main(["doctor", "--vault", str(tmp), "--offline", "--json"]))
        payload = json.loads(out)
        assert code == 0, out
        assert payload["ok"] is True


def test_run_end_to_end_and_idempotent():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")

        code, out = capture_stdout(
            lambda: main(["run", "--vault", str(tmp), "--date", "2026-10-30", "--no-llm", "--jsonl"])
        )
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        assert code == 0
        assert events[0]["event"] == "run_start"
        assert events[-1] == {"event": "run_end", "ok": True, "exit": 0}
        assert any(event["event"] == "journal_done" for event in events)
        assert any(event["event"] == "report_written" for event in events)

        report = tmp / "02-Reading" / "文献雷达" / "2026" / "10" / "2026-10-30.md"
        assert report.exists()
        before = report.read_text(encoding="utf-8")
        assert "Positive selection" in before

        code, out = capture_stdout(
            lambda: main(["run", "--vault", str(tmp), "--date", "2026-10-30", "--no-llm", "--quiet"])
        )
        assert code == 0
        assert report.read_text(encoding="utf-8") == before


def test_status_json():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        main(["fetch", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
        code, out = capture_stdout(lambda: main(["status", "--vault", str(tmp), "--json"]))
        assert code == 0
        payload = json.loads(out)
        assert payload["last_run"] == "2026-10-30"
        assert payload["last_scan_at"]
        assert payload["journals"]["Example Genetics"]["status"] == "ok"


def test_screen_strict_rejects_unknown_id():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        main(["fetch", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
        fake = tmp / "screen.json"
        fake.write_text(
            json.dumps({"run_id": "2026-10-30", "results": [{"id": "sha1:deadbeefdeadbeef"}]}),
            encoding="utf-8",
        )
        code, out = capture_stdout(
            lambda: main(["screen", "--vault", str(tmp), "--file", str(fake),
                          "--date", "2026-10-30", "--strict", "--json"])
        )
        assert code == 3
        payload = json.loads(out)
        assert payload["summary"]["rejected"] == 1


def test_lock_contention_returns_six():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        paths = Paths(tmp)
        lock = VaultLock(paths.lock_file).acquire()
        try:
            code, _ = capture_stdout(
                lambda: main(["fetch", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
            )
            assert code == 6
        finally:
            lock.release()


def test_bad_vault_returns_config_error():
    with temp_dir() as tmp:
        code, _ = capture_stdout(lambda: main(["fetch", "--vault", str(tmp), "--quiet"]))
        assert code == 2


def test_unknown_window_flag_is_config_error():
    with temp_dir() as tmp:
        _init(tmp)
        code, _ = capture_stdout(
            lambda: main(["render", "--vault", str(tmp), "--date", "2026-10-30",
                          "--window", "yesterday", "--quiet"])
        )
        assert code == 2


def test_weekly_report_uses_iso_week_filename_and_index_link():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        code, _ = capture_stdout(
            lambda: main(["run", "--vault", str(tmp), "--kind", "weekly",
                          "--ref", "2026-11-02", "--no-llm", "--quiet"])
        )
        assert code == 0
        report = tmp / "02-Reading" / "文献雷达" / "2026" / "11" / "2026-W44.md"
        assert report.exists()
        index = (tmp / "02-Reading" / "文献雷达" / "文献雷达索引.md").read_text(encoding="utf-8")
        assert "[[2026-W44]]" in index
        assert "2026-W44" in report.read_text(encoding="utf-8")


def test_weekly_kind_conflicts_with_date():
    with temp_dir() as tmp:
        _init(tmp)
        code, _ = capture_stdout(
            lambda: main(["render", "--vault", str(tmp), "--kind", "weekly",
                          "--date", "2026-10-30", "--quiet"])
        )
        assert code == 2


def test_screen_apply_then_render_uses_external_result():
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        main(["fetch", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
        store = json.loads((tmp / ".litradar" / "store" / "2026-10-30.json").read_text(encoding="utf-8"))
        item = store["items"][0]
        screen = tmp / "external-screen.json"
        screen.write_text(
            json.dumps({
                "run_id": "2026-10-30",
                "model": "manual",
                "screening": "external",
                "results": [{
                    "id": item["id"],
                    "relevant": True,
                    "relevance": 0.9,
                    "why": "人工挑选",
                    "abstract_en": item["abstract_raw"],
                    "abstract_mode": "verbatim",
                }],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        code, _ = capture_stdout(
            lambda: main(["screen", "--vault", str(tmp), "--file", str(screen),
                          "--date", "2026-10-30", "--apply", "--strict", "--quiet"])
        )
        assert code == 0
        code, _ = capture_stdout(
            lambda: main(["render", "--vault", str(tmp), "--date", "2026-10-30",
                          "--no-llm", "--quiet"])
        )
        assert code == 0
        report = (tmp / "02-Reading" / "文献雷达" / "2026" / "10" / "2026-10-30.md").read_text(
            encoding="utf-8"
        )
        assert "## 与你最相关（1 篇）" in report
        assert "external" in report
        assert "人工挑选" in report


def test_embed_without_extras_returns_five():
    import importlib.util

    if importlib.util.find_spec("sentence_transformers") is not None:
        return  # environment has the optional extra; nothing to assert here
    with temp_dir() as tmp, local_server({"/feed.xml": RDF_FEED}) as base:
        _init(tmp)
        _prepare(tmp, f"{base}/feed.xml")
        main(["fetch", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
        code, _ = capture_stdout(
            lambda: main(["embed", "--vault", str(tmp), "--date", "2026-10-30", "--quiet"])
        )
        assert code == 5


def test_run_creates_one_report_with_both_kinds():
    with temp_dir() as tmp:
        _init(tmp)
        journals = tmp / ".litradar" / "journals"
        for path in journals.glob("*.yaml"):
            path.unlink()
        (journals / "journal-x.yaml").write_text(yaml.safe_dump({
            "name": "Journal X", "adapter": "manual",
            "items": [{"title": "A journal paper", "url": "https://example.org/j",
                       "date": "2026-10-30", "abstract": "Genome variants in patients."}],
        }, allow_unicode=True), encoding="utf-8")
        (journals / "biorxiv.yaml").write_text(yaml.safe_dump({
            "name": "bioRxiv", "adapter": "manual", "preprint": True,
            "items": [{"title": "A preprint paper", "url": "https://example.org/p",
                       "date": "2026-10-30", "abstract": "Genome variants."}],
        }, allow_unicode=True), encoding="utf-8")
        code, _ = capture_stdout(
            lambda: main(["run", "--vault", str(tmp), "--date", "2026-10-30",
                          "--no-llm", "--quiet"])
        )
        assert code == 0
        report_dir = tmp / "02-Reading" / "文献雷达" / "2026" / "10"
        report = report_dir / "2026-10-30.md"
        assert report.exists()
        assert not (report_dir / "2026-10-30-preprint.md").exists()
        text = report.read_text(encoding="utf-8")
        assert "## 期刊（1 篇）" in text
        assert "## 预印本（1 篇）" in text
        index = (tmp / "02-Reading" / "文献雷达" / "文献雷达索引.md").read_text(encoding="utf-8")
        assert index.count("[[2026-10-30]]") == 1


def test_weekly_command_builds_recap_from_daily_stores():
    from litradar.model import Article, JournalScan
    from litradar.paths import Paths
    from litradar.state import atomic_write_json
    from litradar.window import Window

    with temp_dir() as tmp:
        _init(tmp)
        paths = Paths(tmp)
        day = Window(kind="day", start=__import__("datetime").date(2026, 11, 2),
                     end=__import__("datetime").date(2026, 11, 2))
        article = Article(
            title="Alpha paper", journal="Journal A", url="https://example.org/a",
            doi="10.1/a", online_date=day.start, date_source="publisher",
            abstract_raw="We study alpha.",
        )
        atomic_write_json(paths.store_file("2026-11-02"), {
            "schema": 2, "run_id": "2026-11-02", "engine": "2.1.0",
            "scan_at": "2026-11-02T09:00:00+08:00", "window": day.to_dict(),
            "journals": [JournalScan(journal="Journal A", status="ok", items_seen=1).to_dict()],
            "items": [article.to_dict()],
            "date_unverified": [], "out_of_window": [], "excluded_type": [],
        })
        code, out = capture_stdout(
            lambda: main(["weekly", "--vault", str(tmp), "--ref", "2026-11-06",
                          "--no-llm", "--json"])
        )
        assert code == 0
        report = tmp / "02-Reading" / "文献雷达" / "2026" / "11" / "2026-W45.md"
        assert report.exists()
        text = report.read_text(encoding="utf-8")
        assert "kind: weekly" in text
        assert "2026-W45" in text
        assert "Alpha paper" in text
        assert "没有抓取记录" in text


def test_is_weekly_day_follows_config():
    from helpers import make_context

    from litradar.cli import _is_weekly_day
    from litradar.util import today

    with temp_dir() as tmp:
        ctx = make_context(tmp)
        current = today(ctx.tz).weekday()
        ctx.config.raw.setdefault("weekly", {})["weekday"] = current
        assert _is_weekly_day(ctx)
        ctx.config.raw["weekly"]["weekday"] = (current + 1) % 7
        assert not _is_weekly_day(ctx)
        ctx.config.raw["weekly"]["weekday"] = current
        ctx.config.raw["weekly"]["enabled"] = False
        assert not _is_weekly_day(ctx)


def test_weekly_kind_can_span_explicit_dates():
    from litradar.cli import resolve_window
    from litradar.config import load_config

    with temp_dir() as tmp:
        _init(tmp)
        config = load_config(tmp)
        args = type("Args", (), {})()
        for name, value in (("date", None), ("start", "2026-10-26"),
                            ("end", "2026-11-02"), ("window", None),
                            ("kind", "weekly"), ("ref", None)):
            setattr(args, name, value)
        window = resolve_window(args, config)
        assert window.kind == "weekly"
        assert window.start.isoformat() == "2026-10-26"
        assert window.end.isoformat() == "2026-11-02"
        assert window.run_id == "2026-W44"


def test_config_set_writes_overlay_and_list_redacts_key():
    with temp_dir() as tmp:
        _init(tmp)
        code, _ = capture_stdout(
            lambda: main(["config", "set", "screening.keyword_weight", "0.8", "--vault", str(tmp), "--quiet"])
        )
        assert code == 0
        overlay = tmp / ".litradar" / "config.local.yaml"
        assert overlay.exists()
        assert "0.8" in overlay.read_text(encoding="utf-8")

        code, _ = capture_stdout(
            lambda: main(["config", "set", "llm.api_key", "sk-secret", "--vault", str(tmp), "--quiet"])
        )
        assert code == 0
        code, out = capture_stdout(
            lambda: main(["config", "list", "--json", "--vault", str(tmp), "--quiet"])
        )
        assert code == 0
        assert "sk-secret" not in out
        assert '"***"' in out
