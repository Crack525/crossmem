"""Lazy-loaded fastembed wrapper — optional embeddings backend for crossmem."""

from __future__ import annotations

import struct

_model = None
_EMBEDDING_DIM = 384
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def is_available() -> bool:
    """Return True if fastembed is installed."""
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=_MODEL_NAME, show_download_progress=False)
    return _model


def embed(text: str) -> bytes | None:
    """Embed text into a 384-dim float32 vector packed as bytes. None if unavailable."""
    if not text or not text.strip():
        return None
    try:
        model = _get_model()
        vectors = list(model.embed([text.strip()]))
        if not vectors:
            return None
        vec = vectors[0]
        return struct.pack(f"{_EMBEDDING_DIM}f", *vec[:_EMBEDDING_DIM])
    except Exception:
        return None
