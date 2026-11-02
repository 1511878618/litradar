"""Small, dependency-light helpers shared by every layer."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ISO_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"<[^>]+>")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")


class ConfigError(Exception):
    """Raised for user-facing configuration problems (exit code 2)."""


class EngineError(Exception):
    """Raised for engine-level failures that map to a generic exit code."""


def tzinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - depends on host tzdata
        raise ConfigError(
            f"未知时区 {name!r}。请安装 tzdata 或改用 IANA 时区名（如 Asia/Shanghai）。"
        ) from exc


def now(name: str = "UTC") -> datetime:
    return datetime.now(tzinfo(name))


def today(name: str = "UTC") -> date:
    return now(name).date()


def iso(dt: datetime | None = None) -> str:
    """ISO-8601 with offset, second precision — used for every timestamp we write.

    Defaults to the machine's local offset, which is what a human expects to see in
    ``scan_at``; explicit windows and report dates still use ``schedule.tz``.
    """
    if dt is None:
        dt = datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.isoformat(timespec="seconds")


def parse_date(value: object) -> date | None:
    """Best-effort date parsing that never guesses.

    Returns ``None`` when the surface did not state a usable calendar date.  Callers
    must treat ``None`` as "unverified", never as "today".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None

    match = _ISO_DATE_RE.match(raw)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = _COMPACT_DATE_RE.match(raw)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    # RFC 2822 ("Fri, 24 Oct 2026 00:00:00 GMT") and ISO-8601 with time.
    try:
        if "," in raw or re.match(r"^[A-Za-z]{3},", raw):
            return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    # "22 October 2026", "Published online: 22 Oct 2026".
    match = _MONTH_DAY_YEAR_RE.search(raw)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    return None


def strip_html(text: str | None) -> str:
    """HTML fragment -> readable plain text (entities decoded, whitespace collapsed)."""
    if not text:
        return ""
    import html as html_module

    plain = _TAG_RE.sub(" ", text)
    plain = html_module.unescape(plain)
    plain = plain.replace("\xa0", " ")
    plain = _WS_RE.sub(" ", plain)
    return _MULTI_NL_RE.sub("\n\n", plain).strip()


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


_BOILERPLATE_RE = re.compile(
    r"^(?P<prefix>[^:]{0,80}?,\s*)?Published online:\s*[^;]{0,40};\s*doi:\s*\S+\s*",
    re.IGNORECASE,
)


def clean_abstract(text: str | None, *, max_chars: int = 1500) -> str | None:
    """Normalise abstracts coming from RSS/JATS/Crossref into one comparable form.

    Publisher feeds routinely prepend a boilerplate sentence such as
    ``Nature Genetics, Published online: 22 October 2026; doi:10.1038/...`` — that
    text is metadata, not the abstract, and leaving it in poisons the verbatim
    similarity check later on.
    """
    if not text:
        return None
    # PLOS-style Atom content starts with a "by <authors>" paragraph.
    text = re.sub(r"^\s*<p>\s*by\s[^<]*</p>\s*", "", text, flags=re.IGNORECASE)
    plain = strip_html(text)
    if not plain:
        return None
    plain = _BOILERPLATE_RE.sub("", plain)
    plain = re.sub(r"^\s*(abstract|summary)\s*[:.\-]?\s*", "", plain, flags=re.IGNORECASE)
    plain = re.sub(r"^\[?jats:p\]?", "", plain, flags=re.IGNORECASE)
    plain = collapse(plain)
    if len(plain) < 30:
        return None
    if len(plain) > max_chars:
        cut = plain[:max_chars]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        plain = cut.rstrip(" ,;:.")
    return plain


def clean_title(text: str | None, *, max_chars: int = 500) -> str:
    """Titles have no minimum length and no boilerplate — strip tags, collapse, cut.

    (``clean_abstract`` is wrong here: it drops anything shorter than 30 chars,
    which would silently discard legitimate short titles.)
    """
    plain = collapse(strip_html(text))
    if max_chars and len(plain) > max_chars:
        plain = plain[:max_chars].rstrip()
    return plain


def truncate(text: str, limit: int) -> str:
    """Cut text to at most ``limit`` characters, on a word boundary when possible."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    cut = cut.rstrip(" ,;:.")
    return cut + "…" if cut else text[:limit]


def redact(text: str) -> str:
    """Never let a key reach a log file."""
    return _SECRET_RE.sub("sk-***", text or "")


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "journal"


def norm_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return collapse(text)


def similar(a: str, b: str) -> float:
    """Similarity in [0, 1] used by the verbatim rule.

    ``difflib`` is deterministic and dependency-free; for "did the model copy the
    abstract" this is exactly the right granularity.
    """
    from difflib import SequenceMatcher

    left, right = collapse(a).casefold(), collapse(b).casefold()
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()
