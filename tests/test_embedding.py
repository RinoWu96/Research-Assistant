"""Embedding adapter tests use a fake backend and never download a model."""

import unittest
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
