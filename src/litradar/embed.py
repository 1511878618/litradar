"""Optional semantic ranking.

Embedding is a *bonus*: it refines the order of highlights and is never allowed to
block a report.  Every failure path here raises ``EmbedUnavailable`` with a human
message that ends up in the panel ("相关度不可用") instead of a traceback.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .config import Config
from .model import Article
from .paths import Paths
from .util import EngineError, collapse


class EmbedUnavailable(EngineError):
    """Optional dependency, model download or profile is missing."""


def _cache_root(paths: Paths, config: Config) -> Path:
    ref = str(config.get("embedding.cache_dir", ".litradar/vectors") or ".litradar/vectors")
    root = Path(ref)
    if not root.is_absolute():
        root = paths.vault / root
    return root


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def profile_texts(config: Config, paths: Paths, logger: Any = None) -> list[str]:
    """Resolve ``embedding.profile`` into the texts that define "relevant to me"."""
    source = str(config.get("embedding.profile", "") or "").strip()
    if source.startswith("zotero:collection/"):
        from .zotero import ZoteroUnavailable, collection_texts

        name = source.split("/", 1)[1]
        try:
            texts = collection_texts(config, name)
        except ZoteroUnavailable as exc:
            raise EmbedUnavailable(f"Zotero profile 不可用：{exc}") from exc
        if not texts:
            raise EmbedUnavailable(f"Zotero 收藏夹 {name!r} 里没有可用的标题/摘要")
        return texts
    if source.startswith("tags:"):
        tag = source.split(":", 1)[1].strip().lstrip("#")
        texts = _tag_texts(config, paths, tag, logger)
        if not texts:
            raise EmbedUnavailable(f"vault 里没有带 #{tag} 的笔记")
        return texts
    if source.startswith("text:"):
        blob = source.split(":", 1)[1]
        parts = [collapse(part) for part in blob.replace("；", ";").split(";")]
        texts = [part for part in parts if len(part) > 2]
        if texts:
            return texts
    direction = config.direction
    if direction:
        return [part for part in collapse(direction).replace("；", ";").split(";") if part.strip()]
    raise EmbedUnavailable("embedding.profile 为空且 config.direction 未填写")


def _tag_texts(config: Config, paths: Paths, tag: str, logger: Any = None) -> list[str]:
    texts: list[str] = []
    for path in sorted(paths.vault.rglob("*.md")):
        if ".litradar" in path.parts or len(texts) >= 200:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if f"#{tag}" not in content:
            continue
        title = next(
            (line.lstrip("# ").strip() for line in content.splitlines() if line.startswith("#")),
            path.stem,
        )
        texts.append(collapse(f"{title}. {content[:1200]}"))
    if logger and texts:
        logger.debug(f"tags profile 命中 {len(texts)} 条笔记")
    return texts


def _model(config: Config):
    name = str(config.get("embedding.model", "BAAI/bge-m3") or "BAAI/bge-m3")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # optional extra
        raise EmbedUnavailable(
            '未安装 embed 依赖；执行 pip install "litradar[embed]" 后再试（日报不受影响）'
        ) from exc
    try:
        return SentenceTransformer(name)
    except Exception as exc:  # noqa: BLE001 - download/offline/corrupt cache
        raise EmbedUnavailable(f"加载模型 {name} 失败：{exc}") from exc


def _encode(model, texts: list[str]):
    try:
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:  # noqa: BLE001
        raise EmbedUnavailable(f"embedding 计算失败：{exc}") from exc


def compute_scores(
    config: Config,
    paths: Paths,
    items: list[Article],
    *,
    profile: str | None = None,
    logger: Any = None,
) -> tuple[dict[str, float], int]:
    """Cosine similarity between each article and the interest centroid."""
    if profile:
        config = _with_profile(config, profile)
    texts = profile_texts(config, paths, logger)
    model = _model(config)
    profile_vec = _encode(model, [" ".join(texts)])[0]

    cache_root = _cache_root(paths, config) / str(config.get("embedding.model", "bge-m3")).replace("/", "_")
    cache_root.mkdir(parents=True, exist_ok=True)
    scores: dict[str, float] = {}
    pending: list[tuple[str, str]] = []
    for article in items:
        key = article.doi or article.id
        text = collapse(f"{article.title}. {article.abstract_raw or ''}")[:4000]
        cache_file = cache_root / f"{_text_key(key)}.npy"
        if cache_file.exists():
            try:
                import numpy as np

                vector = np.load(cache_file)
                scores[key] = float(profile_vec @ vector)
                continue
            except Exception:  # noqa: BLE001 - stale/corrupt cache is fine to recompute
                pass
        pending.append((key, text))

    if pending:
        vectors = _encode(model, [text for _, text in pending])
        import numpy as np

        for (key, _), vector in zip(pending, vectors, strict=False):
            scores[key] = float(profile_vec @ vector)
            try:
                np.save(cache_root / f"{_text_key(key)}.npy", vector)
            except OSError:
                pass
    return {key: round(score, 4) for key, score in scores.items()}, len(texts)


def _with_profile(config: Config, profile: str) -> Config:
    import copy

    data = copy.deepcopy(config.raw)
    data.setdefault("embedding", {})["profile"] = profile
    return Config(data, config.vault, config.path)


def scores_payload(config: Config, scores: dict[str, float], n_profile: int) -> dict[str, Any]:
    from . import SCHEMA
    from .util import iso

    return {
        "schema": SCHEMA,
        "computed_at": iso(),
        "model": str(config.get("embedding.model", "BAAI/bge-m3")),
        "profile": {
            "source": str(config.get("embedding.profile", "")),
            "n_items": n_profile,
        },
        "scores": scores,
    }


def top_k(scores: dict[str, float], k: int) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:k]
