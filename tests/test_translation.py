"""Public translation stays optional and report generation survives outages."""
from helpers import FakeFetcher, make_config, sample_article, temp_dir

from litradar.paths import Paths
from litradar.translation import translate_titles


def _client():
    return FakeFetcher({"translate.googleapis.com": lambda _: [[["基因组研究", "Genome study"]]]})


def test_disabled_translation_never_requests():
    with temp_dir() as root:
        client = _client()
        result, note = translate_titles(make_config(root), Paths(root), [sample_article()], fetcher=client)
        assert result == {} and note == "" and not client.calls


def test_translation_cache_and_changed_title():
    with temp_dir() as root:
        cfg = make_config(root, translation={"enabled": True})
        article = sample_article(title="Genome study")
        client = _client()
        result, _ = translate_titles(cfg, Paths(root), [article], fetcher=client)
        assert result[article.id] == "基因组研究" and len(client.calls) == 1
        result, _ = translate_titles(cfg, Paths(root), [article], fetcher=client)
        assert result[article.id] == "基因组研究" and len(client.calls) == 1
        article.title = "Different study"
        translate_titles(cfg, Paths(root), [article], fetcher=client)
        assert len(client.calls) == 2


def test_outage_circuit_breaker_and_no_failure_cache():
    with temp_dir() as root:
        cfg = make_config(root, translation={"enabled": True})
        client = FakeFetcher()
        articles = [sample_article(doi=f"10.1/{i}", title=f"Genome {i}") for i in range(8)]
        result, note = translate_titles(cfg, Paths(root), articles, fetcher=client)
        assert result == {} and len(client.calls) == 3
        assert "8 篇" in note
        assert not (Paths(root).cache_dir / "title-translations.json").exists()


def test_dry_run_no_network_or_writes():
    with temp_dir() as root:
        cfg = make_config(root, translation={"enabled": True})
        client = _client()
        translate_titles(cfg, Paths(root), [sample_article()], fetcher=client, commit=False)
        assert not client.calls
        assert not Paths(root).cache_dir.exists()


def test_existing_deepread_and_chinese_titles_skip_requests():
    with temp_dir() as root:
        cfg = make_config(root, translation={"enabled": True})
        article = sample_article(title="Genome study")
        client = _client()
        result, _ = translate_titles(cfg, Paths(root), [article, sample_article(title="中文研究")],
                                     existing={article.id: "已有精读标题"}, fetcher=client)
        assert result[article.id] == "已有精读标题" and not client.calls


def test_invalid_payload_keeps_original_and_request_limit():
    with temp_dir() as root:
        cfg = make_config(root, translation={"enabled": True, "max_per_run": 1})
        client = FakeFetcher({"translate.googleapis.com": lambda _: [[["English only"]]]})
        articles = [sample_article(doi=f"10.1/{i}", title=f"Genome {i}") for i in range(2)]
        result, note = translate_titles(cfg, Paths(root), articles, fetcher=client)
        assert result == {} and "2 篇" in note and len(client.calls) == 1
