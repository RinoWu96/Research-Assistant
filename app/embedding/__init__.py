"""Local document chunking and replaceable embedding model boundary."""

from app.embedding.chunker import Chunk, PageSource, chunk_pages
from app.embedding.embedder import LocalEmbedder

__all__ = ["Chunk", "PageSource", "LocalEmbedder", "chunk_pages"]
