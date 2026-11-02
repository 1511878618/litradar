"""Image picking and downloading — cosmetic, but it must never break a report."""

from pathlib import Path

from helpers import FakeFetcher, make_config, make_context, sample_article, temp_dir

from litradar.images import attach_images, download_image, extract_image_url, images_enabled

NATURE_HTML = """
<html><head>
<meta property="og:image" content="https://media.example.org/Fig1_HTML.png">
</head><body>
<figure><figcaption>Table 1</figcaption></figure>
<figure><figcaption>Fig. 1: accuracy</figcaption>
  <picture><source type="image/webp" srcset="//media.example.org/Fig1.webp">
  <img src="//media.example.org/Fig1_HTML.png" alt="Fig 1"></picture>
</figure>
</body></html>
"""

PLOS_HTML = """
<html><head><meta property="og:image" content="https://journals.example.org/figure?id=g005&size=inline"></head>
<body>
<img src="/resource/img/logo-plos-full-color.svg">
<img src="/plosgenetics/article/figure/image?size=inline&amp;id=10.1.g001">
<img src="/plosgenetics/article/figure/image?size=inline&amp;id=10.1.g002">
</body></html>
"""

LOGO_ONLY_HTML = """
<html><head><meta property="og:image" content="https://www.example.org/logo-homepage.png"></head>
<body><img src="https://www.example.org/sprite.svg"></body></html>
"""


def test_images_enabled_flag():
    assert not images_enabled(make_config(Path("/tmp")))
    config = make_config(Path("/tmp"), images={"enabled": True})
    assert images_enabled(config)


def test_nature_style_first_figure():
    url = extract_image_url(NATURE_HTML, "https://www.nature.com/articles/x")
    assert url == "https://media.example.org/Fig1_HTML.png"


def test_plos_style_first_figure_preferred_over_og_image():
    url = extract_image_url(PLOS_HTML, "https://journals.plos.org/plosgenetics/article?id=1")
    assert url.endswith("g001")
    assert url.startswith("https://journals.plos.org/")


def test_logos_are_ignored():
    assert extract_image_url(LOGO_ONLY_HTML, "https://www.example.org/a") is None


def test_svg_and_tracking_are_ignored():
    html = '<img src="/logo.svg"><img src="https://ads.example.org/pixel.gif">'
    assert extract_image_url(html, "https://example.org/a") is None


def test_cover_mode_uses_configured_cover():
    journal = {"cover_url": "https://example.org/cover.png"}
    html = '<img src="https://example.org/Fig1.png">'
    assert extract_image_url(html, "https://example.org/a", journal, source="cover") == (
        "https://example.org/cover.png"
    )


def test_explicit_selector_wins():
    journal = {"image_selector": "div.lead img"}
    html = '<div class="lead"><img src="/lead.png"></div><img src="/other.png">'
    assert extract_image_url(html, "https://example.org/a", journal) == "https://example.org/lead.png"


def test_bad_selector_is_ignored():
    journal = {"image_selector": "[[[not-a-selector"}
    html = '<figure><img src="/fig.png"></figure>'
    assert extract_image_url(html, "https://example.org/a", journal) == "https://example.org/fig.png"


def test_download_writes_hashed_file():
    with temp_dir() as tmp:
        fetcher = FakeFetcher(bytes_routes={"example.org/fig.png": b"PNGDATA"})
        path = download_image(fetcher, "https://example.org/fig.png", tmp / "assets")
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == b"PNGDATA"
        assert path.suffix == ".png"
        # second call reuses the same file
        again = download_image(fetcher, "https://example.org/fig.png", tmp / "assets")
        assert again == path


def test_download_rejects_non_image_and_oversized():
    with temp_dir() as tmp:
        fetcher = FakeFetcher(bytes_routes={
            "example.org/file.html": (b"<html>", 200, "text/html"),
            "example.org/big.png": (b"x" * 5000, 200, "image/png"),
        })
        assert download_image(fetcher, "https://example.org/file.html", tmp) is None
        assert download_image(fetcher, "https://example.org/big.png", tmp, max_bytes=1000) is None


def test_download_handles_http_error():
    with temp_dir() as tmp:
        fetcher = FakeFetcher()
        assert download_image(fetcher, "https://example.org/missing.png", tmp) is None


def test_attach_images_end_to_end():
    with temp_dir() as tmp:
        config = make_config(
            tmp,
            images={"enabled": True, "source": "auto", "download": True, "max_per_run": 5},
        )
        ctx = make_context(tmp, routes={
            "example.org/a1": NATURE_HTML,
        }, images={"enabled": True, "max_per_run": 5})
        ctx.config = config
        ctx.fetcher.bytes_routes = {"media.example.org/Fig1_HTML.png": b"IMG"}
        article = sample_article(doi="10.1/a", url="https://example.org/a1")
        stats = attach_images(ctx, tmp / "report", [article], {})
        assert stats.attempted == 1
        assert stats.attached == 1
        assert article.extra["image_path"].startswith("assets/")
        assert (tmp / "report" / article.extra["image_path"]).exists()


def test_attach_images_respects_scope_and_limit():
    with temp_dir() as tmp:
        ctx = make_context(tmp, routes={"example.org/a1": NATURE_HTML},
                           images={"enabled": True, "max_per_run": 1})
        ctx.fetcher.bytes_routes = {"media.example.org/Fig1_HTML.png": b"IMG"}
        articles = [sample_article(doi=f"10.1/{index}", url="https://example.org/a1")
                    for index in range(3)]
        stats = attach_images(ctx, tmp, articles, {})
        assert stats.attempted == 1
        assert stats.attached == 1


def test_attach_images_disabled_makes_no_calls():
    with temp_dir() as tmp:
        ctx = make_context(tmp, routes={"example.org/a1": NATURE_HTML})
        article = sample_article(url="https://example.org/a1")
        stats = attach_images(ctx, tmp, [article], {})
        assert stats.attempted == 0
        assert ctx.fetcher.calls == []


def test_attach_images_falls_back_to_cover():
    with temp_dir() as tmp:
        ctx = make_context(tmp, images={"enabled": True})
        ctx.fetcher.bytes_routes = {"example.org/cover.png": b"COVER"}
        article = sample_article(journal="J", url="https://example.org/blocked")
        stats = attach_images(ctx, tmp, [article], {
            "J": {"cover_url": "https://example.org/cover.png"},
        })
        assert stats.attached == 1
        assert article.extra["image_path"]
