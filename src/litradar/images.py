"""Optional figures for highlighted articles.

The report is text-first; images are a bonus and must never block it.  For each
highlight we fetch the article page once, pick a representative image (first figure,
publisher's ``og:image``, or a configured journal cover) and download it next to the
report so the note still renders offline.

Nothing here is publisher-specific: the generic selectors cover Nature, PLOS and
bioRxiv, and a journal can override them with ``image_selector`` / ``cover_url`` /
``image_page`` in its YAML.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Article

CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}
ALLOWED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
_LOGO_HINTS = (
    "logo",
    "sprite",
    "icon",
    "badge",
    "placeholder",
    "blank.gif",
    "1x1",
    "pixel",
    "doubleclick",
    "analytics",
    "avatar",
    "banner",
)
_FIGURE_IMG_RE = re.compile(r"/F\d+\.(?:large|full|small|med)\.(?:jpe?g|png|gif)", re.IGNORECASE)


@dataclass
class ImageStats:
    attempted: int = 0
    attached: int = 0
    failed: int = 0
    skipped: int = 0
    urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "attached": self.attached,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def images_enabled(config: Any) -> bool:
    return bool(config.get("images.enabled", False)) if config is not None else False


def _src_of(element: Any) -> Iterable[str]:
    for attribute in ("src", "data-src", "data-original"):
        value = element.get(attribute)
        if value:
            yield value
    srcset = element.get("srcset")
    if srcset:
        for part in srcset.split(","):
            candidate = part.strip().split(" ")[0]
            if candidate:
                yield candidate


def _figure_sources(soup: Any) -> Iterable[str]:
    for figure in soup.find_all("figure"):
        for image in figure.find_all("img"):
            yield from _src_of(image)
        for source in figure.find_all("source"):
            yield from _src_of(source)
    # PLOS keeps figures in divs; bioRxiv uses /F1.large.jpg paths.
    for image in soup.find_all("img"):
        src = image.get("src") or image.get("data-src") or ""
        if "/figure/image" in src or _FIGURE_IMG_RE.search(src):
            yield src


def _sources(elements: Iterable[Any]) -> Iterable[str]:
    for element in elements:
        yield from _src_of(element)


def _acceptable(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    lowered = url.lower()
    if lowered.endswith(".svg"):
        return False
    return not any(hint in lowered for hint in _LOGO_HINTS)


def extract_image_url(
    html: str, page_url: str, journal: dict | None = None, *, source: str = "auto"
) -> str | None:
    """Pick one representative image from an article page (no network)."""
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - declared dependency
        return None
    soup = BeautifulSoup(html, "html.parser")
    journal = journal or {}
    source = (source or "auto").lower()
    candidates: list[str] = []

    selector = str(journal.get("image_selector") or "").strip()
    if selector:
        try:
            candidates.extend(_sources(soup.select(selector)))
        except Exception:  # noqa: BLE001 - a bad selector must not break rendering
            pass

    cover = journal.get("cover_url")
    if source == "cover" and cover:
        candidates.append(str(cover))

    if source in ("auto", "first-figure"):
        candidates.extend(_figure_sources(soup))

    if source in ("auto", "cover"):
        if cover:
            candidates.append(str(cover))
        for attrs in ({"property": "og:image"}, {"name": "og:image"}, {"name": "twitter:image"}):
            meta = soup.find("meta", attrs=attrs)
            if meta and meta.get("content"):
                candidates.append(str(meta["content"]))

    seen: set[str] = set()
    for raw in candidates:
        absolute = urllib.parse.urljoin(page_url, raw.strip())
        if absolute in seen:
            continue
        seen.add(absolute)
        if _acceptable(absolute):
            return absolute
    return None


def _extension(content_type: str | None, url: str) -> str:
    if content_type and content_type in CONTENT_TYPES:
        return CONTENT_TYPES[content_type]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in ALLOWED_SUFFIXES else ".img"


def download_image(
    fetcher: Any,
    url: str,
    directory: Path,
    *,
    max_bytes: int = 2_000_000,
    referer: str | None = None,
) -> Path | None:
    """Download once (content-hash name), skip anything that is not a small image."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    for existing in directory.glob(f"{stem}.*"):  # already downloaded in an earlier run
        if existing.is_file() and existing.stat().st_size > 0:
            return existing
    headers = {"Referer": referer} if referer else None
    content, status, content_type, error = fetcher.get_bytes(url, headers)
    if not content or (status or 0) >= 400 or error:
        return None
    if content_type and not content_type.startswith("image/"):
        return None
    if len(content) > max_bytes:
        return None
    name = stem + _extension(content_type, url)
    target = directory / name
    if target.exists() and target.stat().st_size > 0:
        return target
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(content)
        tmp.replace(target)
    except OSError:
        return None
    return target


def attach_images(
    ctx: Any,
    report_dir: Path,
    articles: list[Article],
    journals_by_name: dict[str, dict],
) -> ImageStats:
    """Best-effort: fill ``extra.image_path`` (or ``extra.image_url``) per article."""
    stats = ImageStats()
    config = ctx.config
    if not images_enabled(config) or not articles:
        return stats
    source = str(config.get("images.source", "auto") or "auto")
    download = bool(config.get("images.download", True))
    limit = max(0, int(config.get("images.max_per_run", 12) or 12))
    max_bytes = int(config.get("images.max_bytes", 2_000_000) or 2_000_000)
    assets = Path(report_dir) / "assets"

    for article in articles[:limit]:
        journal = journals_by_name.get(article.journal) or {}
        page_suffix = str(journal.get("image_page") or "")
        page_url = f"{article.url}{page_suffix}"
        stats.attempted += 1
        image_url: str | None = None
        try:
            page = ctx.fetcher.get(page_url)
            if page.ok:
                image_url = extract_image_url(page.text, page_url, journal, source=source)
        except Exception:  # noqa: BLE001 - images are cosmetic
            image_url = None
        if not image_url and journal.get("cover_url"):
            image_url = str(journal["cover_url"])
        if not image_url or not _acceptable(image_url):
            stats.failed += 1
            ctx.logger.debug(f"[image] {article.journal} 无可用配图：{article.title[:60]}")
            continue
        if download:
            path = download_image(
                ctx.fetcher, image_url, assets, max_bytes=max_bytes, referer=page_url
            )
            if path is None:
                stats.failed += 1
                ctx.logger.debug(f"[image] 下载失败：{image_url}")
                continue
            article.extra["image_path"] = f"assets/{path.name}"
        else:
            article.extra["image_url"] = image_url
        article.extra.setdefault("image_source", image_url)
        stats.attached += 1
        stats.urls.append(image_url)
    if stats.attempted:
        ctx.logger.info(
            f"配图：{stats.attached}/{stats.attempted} 成功"
            + (f"，{stats.failed} 失败" if stats.failed else "")
        )
    return stats
