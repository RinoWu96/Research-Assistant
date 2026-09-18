"""Small deterministic semantic corpus and fake embedder for backend tests."""

from dataclasses import replace
from pathlib import Path
import numpy as np

from app.parser.pdf_parser import PageText
from app.search.database import Database
from app.zotero.models import Attachment, Collection, Paper


class FakeEmbedder:
    model_name = "test/scientific-embedding"
    model_version = "test-v1"
    dimension = 4

    def __init__(self):
        self.embedded_documents = 0
        self.embedded_queries = 0

    @staticmethod
    def _vector(text: str):
        lowered = text.lower()
        vector = np.array([
            sum(word in lowered for word in ("curvature", "bending", "flexural", "wrinkle")),
            sum(word in lowered for word in ("strain", "deformation", "distortion", "lattice")),
            sum(word in lowered for word in ("bandgap", "band gap", "electronic")),
            sum(word in lowered for word in ("in2se3", "ferroelectric")),
        ], dtype="float32")
        if not vector.any():
            vector[0] = 0.01
        return vector / np.linalg.norm(vector)

    def embed_texts(self, texts, **kwargs):
        self.embedded_documents += len(texts)
        return np.vstack([self._vector(text) for text in texts]) if texts else np.empty((0, 4), dtype="float32")

    def embed_text(self, text, **kwargs):
        self.embedded_queries += 1
        return self._vector(text)


def build_corpus(path: Path) -> None:
    with Database(path) as db:
        db.save_collections([Collection("GRAPH", "Graphene"), Collection("IN2", "In2Se3")])
        papers = [
            (Paper("P1", "Graphene curvature", ("A",), "2020", "Physics", "10/p1", (), "journalArticle",
                   collection_keys=("GRAPH",)),
             ["curvature induced strain in a graphene wrinkle " * 45,
              "curvature induced strain changes the graphene lattice " * 45]),
            (Paper("P2", "Bent ferroelectric layer", ("B",), "2022", "Materials", "10/p2", (), "journalArticle",
                   collection_keys=("IN2",)),
             ["bending deformation produces local lattice distortion in ferroelectric In2Se3 " * 45,
              "flexural deformation changes the electronic structure of In2Se3 " * 45,
              "bending deformation modifies a surface tensile response " * 45]),
            (Paper("P3", "Tensile semiconductor", ("C",), "2024", "Materials", "10/p3", (), "journalArticle",
                   collection_keys=("IN2",)),
             ["bandgap modulation under tensile strain in an electronic semiconductor " * 45]),
        ]
        for paper, texts in papers:
            with db.transaction():
                paper_id = db.save_paper(paper)
                db.connection.executemany("INSERT OR IGNORE INTO paper_collections VALUES(?,?)",
                                          [(paper_id, key) for key in paper.collection_keys])
            attachment = Attachment("A" + paper.key[1:], "PDF", "application/pdf", "paper.pdf", "imported_file", None)
            pages = tuple(PageText(str(paper_id), paper.key, attachment.key, paper.title, paper.doi, index, text)
                          for index, text in enumerate(texts, 1))
            db.save_attachment(paper_id, attachment, Path(f"{paper.key}.pdf"), f"fp-{paper.key}", "OK", None,
                               pages, content_hash=f"hash-{paper.key}")
