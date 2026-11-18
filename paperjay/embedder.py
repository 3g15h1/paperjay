"""The built-in model for search by meaning: nomic-embed-text v1.5 (quantized, 768 numbers per passage) run
on the CPU with ONNX Runtime through fastembed. No AI program needed; the model (about 130 MB) is downloaded
once into DATA_DIR/models (or MODELS_DIR), by `paperjay fetch-models` at install/update time or on first use.

Nomic's model is trained with a task prefix: passages are embedded as "search_document: ..." and questions as
"search_query: ...". Both sides must use them or the scores drift.
"""
import logging
import os
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("paperjay")

MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
KEY = "builtin:nomic-embed-text-v1.5"   # what the stored vectors are filed under; a new model needs a new key
PREFIX = {"document": "search_document: ", "query": "search_query: "}

BACKEND = None  # tests swap in a function (texts) -> list of vectors
_model = None
_lock = threading.Lock()


class Unavailable(RuntimeError):
    pass


def cache_dir() -> Path:
    """MODELS_DIR (the Docker image bakes the model in there), else DATA_DIR/models."""
    if os.environ.get("MODELS_DIR", "").strip():
        return Path(os.environ["MODELS_DIR"].strip())
    from .config import Config
    return Config.from_env().data_dir / "models"


def _load():
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:  # a platform without onnxruntime wheels
            raise Unavailable(f"the built-in search model can't run here ({e})")
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            _model = TextEmbedding(MODEL, cache_dir=str(d), threads=os.cpu_count() or 1)
        except Exception as e:
            raise Unavailable(f"couldn't load the built-in search model (no internet for the first download?): {e}")
        log.info("built-in search model loaded from %s", d)
    return _model


def embed(texts: list[str], kind: str = "document") -> np.ndarray:
    """Unit-length float32 vectors, one row per text. kind: "document" (passages) or "query" (questions)."""
    texts = [PREFIX[kind] + t for t in texts]
    with _lock:  # one at a time: the model already uses every core
        vecs = BACKEND(texts) if BACKEND else list(_load().embed(texts))
    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1, norms)


def fetch() -> Path:
    """Download the model now (install and update do this, so the first search doesn't wait for it)."""
    _load()
    return cache_dir()
