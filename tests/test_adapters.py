"""Adapters: parsing real-world feed shapes without inventing data."""

from helpers import ATOM_FEED, RDF_FEED, FakeFetcher, crossref_payload, temp_dir

from litradar.adapters.base import failed, get_adapter, ok
from litradar.adapters.biorxiv import BiorxivAdapter
from litradar.adapters.crossref import CrossrefAdapter
from litradar.adapters.html_surface import HtmlAdapter
from litradar.adapters.manual import ManualAdapter
from litradar.adapters.rss import RssAdapter
from litradar.fetcher import Response
from litradar.util import ConfigError
from litradar.window import Window

WINDOW = Window(kind="day", start=__import__("datetime").date(2026, 10, 30),
                end=__import__("datetime").date(2026, 10, 30))


def _journal(**overrides):
    data = {"name": "Example Genetics", "adapter": "rss", "feed": "https://example.org/feed",
            "surface": "Latest Research", "slug": "example-genetics"}
    data.update(overrides)
    return data


def test_rss_parses_rdf_items():
    fetcher = FakeFetcher({"example.org/feed": RDF_FEED})
    items, scan = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    assert scan.status == "ok"
    assert scan.items_seen == 3
    assert len(items) == 3
    first = items[0]
    assert first.title.startswith("Positive selection")
    assert first.doi == "10.1234/abc.1"
    assert first.online_date.isoformat() == "2026-10-30"
    assert first.date_source == "publisher"
    assert first.authors == ["Ann Lee", "Bob Lee"]
    assert first.surface == "Latest Research"


def test_rss_abstract_drops_publisher_boilerplate():
    fetcher = FakeFetcher({"example.org/feed": RDF_FEED})
    items, _ = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    abstract = items[0].abstract_raw
    assert abstract.startswith("Genome-wide scans")
    assert "Published online" not in abstract
    assert "doi:" not in abstract


def test_rss_flags_editorials():
    fetcher = FakeFetcher({"example.org/feed": RDF_FEED})
    items, _ = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    assert items[1].content_type == "other"


def test_rss_keeps_missing_date_missing():
    fetcher = FakeFetcher({"example.org/feed": RDF_FEED})
    items, _ = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    undated = [item for item in items if not item.online_date]
    assert undated and undated[0].date_source == "missing"


def test_atom_entry_and_byline():
    fetcher = FakeFetcher({"example.org/atom": ATOM_FEED})
    items, scan = RssAdapter().fetch(_journal(feed="https://example.org/atom"), fetcher, WINDOW)
    assert scan.status == "ok"
    assert items[0].doi == "10.1371/journal.pgen.0001"
    assert items[0].online_date.isoformat() == "2026-10-30"
    assert items[0].abstract_raw.startswith("We report")
    assert items[0].authors == ["Siyi Chen"]


def test_rss_blocked_detects_cloudflare():
    body = "<html>Just a moment... cloudflare challenge</html>"
    fetcher = FakeFetcher({"example.org/feed": Response(url="u", status=403, text=body)})
    items, scan = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    assert items == []
    assert scan.status == "blocked"
    assert "Cloudflare" in scan.reason


def test_rss_malformed_feed_is_error():
    fetcher = FakeFetcher({"example.org/feed": Response(url="u", status=200, text="<not xml")})
    items, scan = RssAdapter().fetch(_journal(), fetcher, WINDOW)
    assert scan.status == "error"
    assert "解析失败" in scan.reason


def test_crossref_is_metadata_only():
    payload = crossref_payload()
    fetcher = FakeFetcher({"api.crossref.org": payload})
    journal = {"name": "MBE", "adapter": "crossref", "issn": "0737-4038",
               "surface": "Advance Access", "slug": "mbe"}
    items, scan = CrossrefAdapter().fetch(journal, fetcher, WINDOW)
    assert scan.status == "metadata-only"
    assert scan.visibility == "metadata-only"
    assert items[0].date_source == "crossref"
    assert items[0].content_type == "research-article"
    assert items[0].abstract_raw.startswith("We analyse")
    assert items[0].authors == ["Dana Kim"]


def test_crossref_bad_payload():
    fetcher = FakeFetcher({"api.crossref.org": {"status": "ok", "message": {}}})
    journal = {"name": "MBE", "adapter": "crossref", "issn": "1", "slug": "mbe"}
    _, scan = CrossrefAdapter().fetch(journal, fetcher, WINDOW)
    assert scan.status == "error"


HTML_PAGE = """
<html><body>
<ul>
  <li class="al-article-item">
    <h4><a href="/article/one">Selection on gene families</a></h4>
    <span class="al-doi">10.9999/one</span>
    <span class="al-date">2026-10-30</span>
    <div class="al-abstract"><p>We describe selection on gene families in plants.</p></div>
  </li>
  <li class="al-article-item">
    <h4><a href="/article/two">A second article</a></h4>
    <span class="al-date">2026-10-29</span>
  </li>
</ul>
</body></html>
"""


def _html_journal(**overrides):
    journal = {
        "name": "The Plant Cell",
        "adapter": "html",
        "url": "https://example.org/advance",
        "base_url": "https://example.org",
        "surface": "Advance Articles",
        "selectors": {
            "item": "li.al-article-item",
            "title": "h4 a",
            "link": "h4 a::attr(href)",
            "doi": "span.al-doi",
            "date": "span.al-date",
            "abstract": "div.al-abstract",
        },
        "slug": "the-plant-cell",
    }
    journal.update(overrides)
    return journal


def test_html_parses_selectors_and_joins_urls():
    fetcher = FakeFetcher({"example.org/advance": HTML_PAGE})
    items, scan = HtmlAdapter().fetch(_html_journal(), fetcher, WINDOW)
    assert scan.status == "ok"
    assert len(items) == 2
    assert items[0].url == "https://example.org/article/one"
    assert items[0].doi == "10.9999/one"
    assert items[0].online_date.isoformat() == "2026-10-30"
    assert items[0].abstract_raw.startswith("We describe")


def test_html_missing_selector_is_explicit():
    fetcher = FakeFetcher({"example.org/advance": HTML_PAGE})
    journal = _html_journal(selectors={"item": "li.does-not-exist"})
    _, scan = HtmlAdapter().fetch(journal, fetcher, WINDOW)
    assert scan.status == "error"
    assert "selectors.item 未匹配" in scan.reason


def test_html_items_without_title_or_link():
    page = '<ul><li class="x"><span>no link</span></li></ul>'
    fetcher = FakeFetcher({"example.org/advance": page})
    journal = _html_journal(selectors={"item": "li.x", "title": "span"})
    _, scan = HtmlAdapter().fetch(journal, fetcher, WINDOW)
    assert scan.status == "error"
    assert "没有" in scan.reason


def test_manual_inline_items():
    journal = {
        "name": "Manual Journal",
        "adapter": "manual",
        "slug": "manual",
        "items": [
            {"title": "Hand-entered paper", "url": "https://example.org/m1",
             "doi": "10.1/m1", "date": "2026-10-30", "abstract": "A hand written abstract that is long enough."}
        ],
    }
    items, scan = ManualAdapter().fetch(journal, FakeFetcher(), WINDOW)
    assert scan.status == "ok"
    assert items[0].date_source == "manual"
    assert items[0].doi == "10.1/m1"


def test_manual_requires_data():
    try:
        ManualAdapter().fetch({"name": "M", "adapter": "manual", "slug": "m"}, FakeFetcher(), WINDOW)
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")


def test_custom_adapter_requires_module_file():
    with temp_dir() as tmp:
        try:
            get_adapter({"name": "X", "adapter": "custom", "module": "journals/x.py", "slug": "x"}, tmp)
        except ConfigError as exc:
            assert "不存在" in str(exc)
            return
    raise AssertionError("expected ConfigError")


def test_ok_and_failed_helpers():
    scan = ok(_journal(), [1, 2], http=200)
    assert scan.items_seen == 2 and scan.status == "ok"
    broken = failed(_journal(), "timeout", "请求超时（25s）", http=None)
    assert broken.status == "timeout"
    assert broken.items_seen == 0


CUSTOM_MODULE = '''
from litradar.model import Article
from litradar.adapters.base import ok


def fetch(journal, fetcher, window):
    items = [Article(
        title="Custom parsed title",
        journal=journal["name"],
        url="https://example.org/c1",
        doi="10.1/c1",
        online_date=window.start,
        date_source="publisher",
    )]
    return items, ok(journal, items, http=200)
'''


def test_custom_adapter_runs_user_module():
    with temp_dir() as tmp:
        module = tmp / ".litradar" / "journals" / "x_custom.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(CUSTOM_MODULE, encoding="utf-8")
        journal = {"name": "X Journal", "adapter": "custom",
                   "module": "journals/x_custom.py", "slug": "x-journal"}
        adapter = get_adapter(journal, tmp)
        items, scan = adapter.fetch(journal, FakeFetcher(), WINDOW)
        assert scan.status == "ok"
        assert items[0].journal == "X Journal"
        assert items[0].online_date.isoformat() == "2026-10-30"


def test_custom_adapter_rejects_bad_return_shape():
    with temp_dir() as tmp:
        module = tmp / ".litradar" / "journals" / "bad.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("def fetch(journal, fetcher, window):\n    return 42\n", encoding="utf-8")
        journal = {"name": "Bad", "adapter": "custom", "module": "journals/bad.py", "slug": "bad"}
        adapter = get_adapter(journal, tmp)
        try:
            adapter.fetch(journal, FakeFetcher(), WINDOW)
        except ConfigError as exc:
            assert "fetch()" in str(exc)
            return
    raise AssertionError("expected ConfigError")


def _bx(doi="10.1101/2026.10.30.000001", title="A preprint title long enough to matter",
        date="2026-11-01", category="genomics", version="1"):
    return {
        "doi": doi,
        "title": title,
        "authors": "Lee, A.; Kim, B.",
        "date": date,
        "version": version,
        "type": "new results",
        "category": category,
        "server": "bioRxiv",
        "abstract": "We present a preprint abstract that is long enough to survive cleaning.",
    }


def _bx_payload(entries, total, cursor=0):
    return {
        "messages": [{"status": "ok", "count": len(entries), "total": str(total),
                      "cursor": str(cursor)}],
        "collection": entries,
    }


def _bx_fetcher(pages):
    from helpers import FakeFetcher

    def route(url):
        cursor = int(url.rstrip("/").rsplit("/", 1)[-1])
        return pages.get(cursor, _bx_payload([], 0, cursor))

    return FakeFetcher({"api.biorxiv.org/details/biorxiv": route})


BIORXIV = {"name": "bioRxiv", "adapter": "biorxiv", "server": "biorxiv",
           "surface": "Preprints", "slug": "biorxiv"}


def test_biorxiv_parses_article_and_url():
    fetcher = _bx_fetcher({0: _bx_payload([_bx()], 1)})
    items, scan = BiorxivAdapter().fetch(BIORXIV, fetcher, WINDOW)
    assert scan.status == "ok"
    assert len(items) == 1
    article = items[0]
    assert article.doi == "10.1101/2026.10.30.000001"
    assert article.url == "https://www.biorxiv.org/content/10.1101/2026.10.30.000001v1"
    assert article.online_date.isoformat() == "2026-11-01"
    assert article.authors == ["Lee, A.", "Kim, B."]
    assert article.extra["preprint"] == "bioRxiv"
    assert article.abstract_raw.startswith("We present")


def test_biorxiv_pages_until_total():
    pages = {
        0: _bx_payload([_bx("10.1/a"), _bx("10.1/b")], 4),
        2: _bx_payload([_bx("10.1/c"), _bx("10.1/d")], 4, cursor=2),
    }
    fetcher = _bx_fetcher(pages)
    items, scan = BiorxivAdapter().fetch(BIORXIV, fetcher, WINDOW)
    assert len(items) == 4
    assert scan.items_seen == 4


def test_biorxiv_medrxiv_builds_medrxiv_url():
    journal = dict(BIORXIV, server="medrxiv", name="medRxiv")
    payload = _bx_payload([_bx()], 1)
    fetcher = FakeFetcher({"api.biorxiv.org/details/medrxiv": payload})
    items, scan = BiorxivAdapter().fetch(journal, fetcher, WINDOW)
    assert scan.status == "ok"
    assert items[0].url.startswith("https://www.medrxiv.org/content/")


def test_biorxiv_category_filter_is_explicit():
    journal = dict(BIORXIV, categories=["genomics"])
    page = _bx_payload([_bx("10.1/g", category="genomics"),
                        _bx("10.1/n", category="neuroscience")], 2)
    items, scan = BiorxivAdapter().fetch(journal, _bx_fetcher({0: page}), WINDOW)
    assert len(items) == 1
    assert "仅纳入分类" in (scan.reason or "")


def test_biorxiv_cap_is_explicit():
    journal = dict(BIORXIV, max_items=1)
    page = _bx_payload([_bx("10.1/a"), _bx("10.1/b")], 10)
    items, scan = BiorxivAdapter().fetch(journal, _bx_fetcher({0: page}), WINDOW)
    assert len(items) == 1
    assert "上限" in (scan.reason or "")


def test_biorxiv_no_posts_is_ok():
    payload = {"messages": [{"status": "no posts found"}], "collection": []}
    fetcher = FakeFetcher({"api.biorxiv.org/details/biorxiv": payload})
    items, scan = BiorxivAdapter().fetch(BIORXIV, fetcher, WINDOW)
    assert items == []
    assert scan.status == "ok"


def test_biorxiv_bad_status_is_error():
    payload = {"messages": [{"status": "Server not recognized"}], "collection": []}
    fetcher = FakeFetcher({"api.biorxiv.org/details/biorxiv": payload})
    _, scan = BiorxivAdapter().fetch(BIORXIV, fetcher, WINDOW)
    assert scan.status == "error"
    assert "Server not recognized" in scan.reason


def test_biorxiv_unknown_server():
    journal = dict(BIORXIV, server="arxiv")
    _, scan = BiorxivAdapter().fetch(journal, FakeFetcher(), WINDOW)
    assert scan.status == "error"


def test_crossref_created_date_filter():
    payload = {
        "status": "ok",
        "message": {
            "items": [{
                "DOI": "10.1016/j.jbi.2026.105114",
                "title": ["A contrastive decision transformer"],
                "URL": "https://doi.org/10.1016/j.jbi.2026.105114",
                "type": "journal-article",
                "created": {"date-parts": [[2026, 10, 30]]},
                "published-print": {"date-parts": [[2026, 10]]},
                "author": [{"given": "Dana", "family": "Kim"}],
            }],
        },
    }
    fetcher = FakeFetcher({"api.crossref.org": payload})
    journal = {"name": "JBI", "adapter": "crossref", "issn": "1532-0464",
               "date_filter": "created", "slug": "jbi"}
    items, scan = CrossrefAdapter().fetch(journal, fetcher, WINDOW)
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(fetcher.calls[0]).query)
    assert "from-created-date:2026-10-30" in query["filter"][0]
    assert "until-created-date:2026-10-30" in query["filter"][0]
    assert items[0].online_date.isoformat() == "2026-10-30"
    assert items[0].extra["date_basis"] == "created"


def test_crossref_defaults_to_online_date_filter():
    payload = {"status": "ok", "message": {"items": [{
        "DOI": "10.1/x", "title": ["T"], "URL": "https://doi.org/10.1/x",
        "type": "journal-article", "published-online": {"date-parts": [[2026, 10, 30]]},
    }]}}
    fetcher = FakeFetcher({"api.crossref.org": payload})
    journal = {"name": "JAMIA", "adapter": "crossref", "issn": "1067-5027", "slug": "jamia"}
    CrossrefAdapter().fetch(journal, fetcher, WINDOW)
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(fetcher.calls[0]).query)
    assert "from-online-pub-date:2026-10-30" in query["filter"][0]


TYPED_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <item rdf:about="https://example.org/1">
    <title>Research Article</title>
    <link>https://example.org/1</link>
    <dc:type>Research Article</dc:type>
    <dc:date>2026-10-30</dc:date>
  </item>
  <item rdf:about="https://example.org/2">
    <title>Editorial: something</title>
    <link>https://example.org/2</link>
    <dc:type>Editorial</dc:type>
    <dc:date>2026-10-30</dc:date>
  </item>
  <item rdf:about="https://example.org/3">
    <title>A review of things</title>
    <link>https://example.org/3</link>
    <dc:type>Review</dc:type>
    <dc:date>2026-10-30</dc:date>
  </item>
  <item rdf:about="https://example.org/4">
    <title>Research Highlights</title>
    <link>https://example.org/4</link>
    <dc:type>Research Highlights</dc:type>
    <dc:date>2026-10-30</dc:date>
  </item>
</rdf:RDF>
"""


def test_rss_declared_types_override_title_heuristics():
    fetcher = FakeFetcher({"example.org/typed": TYPED_FEED})
    journal = {"name": "Science", "adapter": "rss", "feed": "https://example.org/typed",
               "slug": "science"}
    items, _ = RssAdapter().fetch(journal, fetcher, WINDOW)
    by_title = {item.title: item.content_type for item in items}
    assert by_title["Research Article"] == "research-article"
    assert by_title["Editorial: something"] == "other"
    assert by_title["A review of things"] == "review"
    assert by_title["Research Highlights"] == "other"


DOI_PREFIX_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
  <item rdf:about="https://www.nature.com/articles/d41586-026-1">
    <title>Patrick Stewart dies: how a captain inspired science</title>
    <link>https://www.nature.com/articles/d41586-026-1</link>
    <prism:doi>10.1038/d41586-026-1</prism:doi>
    <dc:date>2026-10-30</dc:date>
  </item>
  <item rdf:about="https://www.nature.com/articles/s41586-026-2">
    <title>A research paper about genomes</title>
    <link>https://www.nature.com/articles/s41586-026-2</link>
    <prism:doi>10.1038/s41586-026-2</prism:doi>
    <dc:date>2026-10-30</dc:date>
  </item>
</rdf:RDF>
"""


def test_rss_excludes_configured_doi_prefixes():
    fetcher = FakeFetcher({"example.org/nature": DOI_PREFIX_FEED})
    journal = {"name": "Nature", "adapter": "rss", "feed": "https://example.org/nature",
               "slug": "nature", "exclude_doi_prefixes": ["10.1038/d"]}
    items, _ = RssAdapter().fetch(journal, fetcher, WINDOW)
    by_doi = {item.doi: item.content_type for item in items}
    assert by_doi["10.1038/d41586-026-1"] == "other"
    assert by_doi["10.1038/s41586-026-2"] == "research-article"
