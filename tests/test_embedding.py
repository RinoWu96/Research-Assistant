"""Embedding adapter tests use a fake backend and never download a model."""

import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from app.embedding.embedder import LocalEmbedder, QUERY_INSTRUCTION


class FakeModel:
    def __init__(self):
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return 3

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return np.array([[1, 2, 3] for _ in texts], dtype="float64")


class EmbeddingTests(unittest.TestCase):
    def test_lazy_replaceable_interface_and_query_instruction(self):
        model = FakeModel()
        embedder = LocalEmbedder(model=model, batch_size=4)
        self.assertEqual(embedder.dimension, 3)
        documents = embedder.embed_texts(["one", "two"])
        query = embedder.embed_text("concept", is_query=True)
        self.assertEqual(documents.shape, (2, 3))
        self.assertEqual(str(documents.dtype), "float32")
        self.assertEqual(model.calls[0][0], ["one", "two"])
        self.assertEqual(model.calls[0][1]["batch_size"], 4)
        self.assertEqual(model.calls[1][0], [QUERY_INSTRUCTION + "concept"])
        self.assertEqual(query.shape, (3,))

    def test_empty_batch(self):
        result = LocalEmbedder(model=FakeModel()).embed_texts([])
        self.assertEqual(result.shape, (0, 3))


class _SlowConstructedModel:
    """Stands in for SentenceTransformer(...): slow enough to widen the race
    window between two threads racing into LocalEmbedder._load()."""

    construction_count = 0
    _count_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        with _SlowConstructedModel._count_lock:
            _SlowConstructedModel.construction_count += 1
        time.sleep(0.2)

    def get_sentence_embedding_dimension(self):
        return 3


class EmbedderConcurrentLoadTests(unittest.TestCase):
    """V0.5: the Core Service's background warm-up and a real find_evidence
    call can race into the same process-wide embedder - the model must
    still be constructed exactly once (section 4 of the V0.5 brief)."""

    def test_concurrent_dimension_access_constructs_the_model_only_once(self):
        _SlowConstructedModel.construction_count = 0
        embedder = LocalEmbedder(local_files_only=True)
        start_barrier = threading.Barrier(2)
        results: list[int] = []

        def worker():
            start_barrier.wait(timeout=5)
            results.append(embedder.dimension)

        with patch("sentence_transformers.SentenceTransformer", _SlowConstructedModel):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(_SlowConstructedModel.construction_count, 1)
        self.assertEqual(results, [3, 3])
