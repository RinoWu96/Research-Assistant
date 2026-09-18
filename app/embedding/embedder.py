"""Lazy, CPU-only local embeddings with no API or CUDA dependency."""

from collections.abc import Sequence
import threading
from typing import Any


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingError(RuntimeError):
    """A local model load or inference failure suitable for user display."""


class LocalEmbedder:
    """Replaceable embedding adapter; the model loads on the first encode call."""

    def __init__(self, model_name: str = DEFAULT_MODEL, *, batch_size: int = 32,
                 local_files_only: bool = False, model: Any | None = None) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive.")
        self.model_name = model_name
        self.batch_size = batch_size
        self.local_files_only = local_files_only
        self._model = model
        self._load_lock = threading.Lock()

    def _load(self):
        """Load the model at most once, even if two threads call this concurrently.

        The Research Assistant Core Service (app.core_service) can receive a
        background warm-up and a real `find_evidence` request racing into the
        same process-wide embedder; the outer check avoids taking the lock on
        the (overwhelmingly common) already-loaded path, and the inner check
        stops a second thread from re-loading after losing the race.
        """
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    self.model_name,
                    device="cpu",
                    local_files_only=self.local_files_only,
                    trust_remote_code=False,
                )
                return self._model
            except Exception as exc:
                mode = "local cache" if self.local_files_only else "local cache or model download"
                raise EmbeddingError(f"Could not load {self.model_name} from {mode}: {exc}") from exc

    @property
    def dimension(self) -> int:
        """Return the output dimension, loading the model only when requested."""
        try:
            model = self._load()
            getter = getattr(model, "get_embedding_dimension", None)
            return int(getter() if getter else model.get_sentence_embedding_dimension())
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Could not read embedding dimension: {exc}") from exc

    @property
    def model_version(self) -> str | None:
        """Return the cached model commit when the backend exposes it."""
        model = self._load()
        config = getattr(getattr(model, "_first_module", lambda: None)(), "auto_model", None)
        return getattr(getattr(config, "config", None), "_commit_hash", None)

    def embed_texts(self, texts: Sequence[str], *, is_query: bool = False):
        """Return normalized float32 vectors; source text is never generated."""
        if not texts:
            try:
                import numpy as np
                return np.empty((0, self.dimension), dtype="float32")
            except Exception as exc:
                raise EmbeddingError(f"NumPy is unavailable: {exc}") from exc
        inputs = [QUERY_INSTRUCTION + text if is_query else text for text in texts]
        try:
            vectors = self._load().encode(
                inputs,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return vectors.astype("float32", copy=False)
        except Exception as exc:
            raise EmbeddingError(f"Local embedding failed: {exc}") from exc

    def embed_text(self, text: str, *, is_query: bool = False):
        """Embed one document or query into a normalized vector."""
        return self.embed_texts([text], is_query=is_query)[0]
