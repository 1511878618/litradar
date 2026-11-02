"""Shared fixtures: temp vaults, fake HTTP, RSS/Atom/Crossref payloads."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from litradar.config import DEFAULTS, Config, deep_merge
from litradar.events import Emitter
from litradar.fetcher import Response
from litradar.logs import Logger
from litradar.paths import Paths
from litradar.pipeline import Context


@contextmanager
def temp_dir():
    path = Path(tempfile.mkdtemp(prefix="litradar-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeFetcher:
    """Routes by substring, so tests never touch the network."""

    def __init__(
        self,
        routes: dict[str, Any] | None = None,
        bytes_routes: dict[str, Any] | None = None,
    ) -> None:
        self.routes = routes or {}
        self.bytes_routes = bytes_routes or {}
        self.calls: list[str] = []
        self.byte_calls: list[str] = []

    def get(self, url: str, headers: dict | None = None, *, cache_ttl: float | None = None):
        self.calls.append(url)
        for needle, value in self.routes.items():
            if needle in url:
                if callable(value):
                    value = value(url)
                    if isinstance(value, Response):
                        return value
                    return Response(url=url, status=200, text=json.dumps(value, ensure_ascii=False))
                if isinstance(value, Response):
                    return value
                if isinstance(value, str):
                    return Response(url=url, status=200, text=value)
                if isinstance(value, dict):
                    return Response(url=url, status=200, text=json.dumps(value, ensure_ascii=False))
        return Response(url=url, status=404, error="HTTP 404（测试未配置该路由）")

    def get_json(self, url: str, headers: dict | None = None):
        response = self.get(url)
        if not response.ok:
            return None, response
        return json.loads(response.text), response

    def get_bytes(self, url: str, headers: dict | None = None):
        """Fake binary GET: value may be bytes or (bytes, status, content_type)."""
        self.byte_calls.append(url)
        for needle, value in self.bytes_routes.items():
            if needle in url:
                if callable(value):
                    value = value(url)
                if isinstance(value, tuple):
                    return value if len(value) == 4 else (*value, None)
                return value, 200, "image/png", None
        return None, 404, None, "HTTP 404（测试未配置该图片）"

    def close(self) -> None:
        return None


def make_config(vault: Path, **overrides: Any) -> Config:
    return Config(deep_merge(DEFAULTS, overrides), vault)


def make_context(vault: Path, routes: dict | None = None, **overrides: Any) -> Context:
    config = make_config(vault, **overrides)
    paths = Paths(vault, config)
    paths.ensure()
    logger = Logger(level="error", stream=io.StringIO())
    return Context(
        vault=Path(vault),
        config=config,
        paths=paths,
        logger=logger,
        emitter=Emitter(False),
        fetcher=FakeFetcher(routes),
        tz=config.tz,
    )


def write_journal(vault: Path, entry: dict) -> Path:
    import yaml

    from litradar.config import validate_journal

    validated = validate_journal(entry, "test")
    path = Path(vault) / ".litradar" / "journals" / f"{validated['slug']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(validated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


RDF_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
         xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel rdf:about="https://example.org/feed">
    <title>Example Genetics</title>
    <link>https://example.org/</link>
  </channel>
  <item rdf:about="https://example.org/a1">
    <title><![CDATA[Positive selection on a gene family in plants]]></title>
    <link>https://example.org/a1</link>
    <content:encoded><![CDATA[
      <p>Example Genetics, Published online: 30 October 2026; <a href="https://doi.org/10.1234/abc.1">doi:10.1234/abc.1</a></p>
      Genome-wide scans of positive selection reveal repeated expansions of a gene family across lineages.
    ]]></content:encoded>
    <dc:creator>Ann Lee</dc:creator>
    <dc:creator>Bob Lee</dc:creator>
    <dc:date>2026-10-30</dc:date>
    <prism:doi>10.1234/abc.1</prism:doi>
  </item>
  <item rdf:about="https://example.org/a2">
    <title>Editorial: something administrative</title>
    <link>https://example.org/a2</link>
    <dc:date>2026-10-30</dc:date>
    <dc:creator>Carl Editor</dc:creator>
  </item>
  <item rdf:about="https://example.org/a3">
    <title><![CDATA[No date but visible]]></title>
    <link>https://example.org/a3</link>
    <prism:doi>10.1234/abc.3</prism:doi>
    <description>A description without any date.</description>
  </item>
</rdf:RDF>
"""

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example PLoS</title>
  <entry>
    <title>GWAS of complex traits in a founder population</title>
    <link rel="alternate" href="https://example.org/p1"/>
    <id>10.1371/journal.pgen.0001</id>
    <published>2026-10-30T14:00:00Z</published>
    <content type="html">&lt;p&gt;by Siyi Chen&lt;/p&gt;
      We report a genome-wide association study in a founder population that identifies loci for complex traits.
    </content>
    <author><name>Siyi Chen</name></author>
  </entry>
</feed>
"""


def crossref_payload(doi: str = "10.1093/molbev/msag999", day: str = "2026-10-30") -> dict:
    parts = [[int(part) for part in day.split("-")]]
    return {
        "status": "ok",
        "message": {
            "total-results": 1,
            "items": [
                {
                    "DOI": doi,
                    "title": ["Self-incompatibility alleles diverge under frequency-dependent selection"],
                    "URL": f"https://doi.org/{doi}",
                    "type": "journal-article",
                    "published-online": {"date-parts": parts},
                    "abstract": "<jats:title>Abstract</jats:title><jats:p>We analyse self-incompatibility allele frequencies across populations.</jats:p>",
                    "author": [{"given": "Dana", "family": "Kim"}],
                    "container-title": ["Molecular Biology and Evolution"],
                }
            ],
        },
    }


def sample_article(**overrides: Any):
    from datetime import date

    from litradar.model import Article

    data = {
        "title": "A study of positive selection",
        "journal": "Example Genetics",
        "url": "https://example.org/paper",
        "doi": "10.1234/xyz.1",
        "online_date": date(2026, 10, 30),
        "date_source": "publisher",
        "abstract_raw": "We study positive selection in a population genomic dataset of many species.",
        "authors": ["Ann Lee", "Bob Smith"],
    }
    data.update(overrides)
    return Article(**data)


def capture_stdout(function: Callable[[], Any]) -> tuple[Any, str]:
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = function()
    return result, buffer.getvalue()
