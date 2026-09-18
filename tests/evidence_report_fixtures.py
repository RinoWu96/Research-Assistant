"""Small deterministic corpus for V0.3 evidence-report integration tests."""

from pathlib import Path

from app.parser.pdf_parser import PageText
from app.search.database import Database
from app.zotero.models import Attachment, Paper

SECONDARY_FULLTEXT_TAIL = """

References
[29] Someone Else. An unrelated study of graphene curvature. Journal of Nothing 2019.
[30] Zhao, W. Strain-Driven Phase Transition in In2Se3. Nature Materials, 2020. \
https://doi.org/10.1000/primary
"""


def build_in2se3_corpus(path: Path) -> None:
    """Five papers covering DIRECT/SECONDARY/CONTEXTUAL/LIMITING relationships to
    the claim "In2Se3 phase change by strain", plus a duplicate Zotero record of
    the primary paper (same DOI, different item/attachment) for Task 1 dedup."""
    with Database(path) as db:
        papers = [
            (Paper("PRIM1", "Strain-Driven Phase Transition in In2Se3", ("Zhao",), "2020", "Nature Materials",
                   "10.1000/primary", (), "journalArticle"),
             ["We show that applied mechanical strain induces the beta-prime to alpha phase transition "
              "in In2Se3 through accumulated internal strain and laser-induced wrinkling.",
              # A second page, only surfaced when citation tracing re-searches this
              # specific paper directly (the narrower top-1-per-paper evidence pack
              # only keeps the single best-scoring page).
              "Additional measurements confirm that mechanical strain reproducibly induces the same "
              "beta-prime to alpha phase transition in In2Se3 across multiple independent samples."]),
            (Paper("PRIM2", "Strain-Driven Phase Transition in In2Se3 (corrected)", ("Zhao",), "2021",
                   "Nature Materials", "10.1000/primary", (), "journalArticle"),
             ["Applied mechanical strain drives the beta-prime to alpha phase transition in In2Se3 via "
              "wrinkling and accumulated internal strain, consistent with the original report."]),
            (Paper("SEC1", "A Review of In2Se3 Phase Engineering", ("Lee",), "2023", "Chem Reviews",
                   "", (), "review"),
             # The citing sentence is on page 1; the bibliography is on page 2 of the
             # same attachment, matching how a real PDF separates body text from
             # references (citation markers must not be pulled from the bibliography itself).
             ["Zhao et al. reported that mechanical strain induces the phase transition in In2Se3 [30].",
              SECONDARY_FULLTEXT_TAIL.strip()]),
            (Paper("CTX1", "Electrically Driven Transitions in In2Se3", ("Wu",), "2022", "Adv Materials",
                   "10.1000/contextual", (), "journalArticle"),
             ["We observe a current-induced alpha to beta phase transition in In2Se3 devices under "
              "applied bias and gate voltage."]),
            (Paper("LIM1", "Strain Independence of Nonlayered In2Se3 Transitions", ("Kim",), "2021",
                   "ACS Nano", "10.1000/limiting", (), "journalArticle"),
             ["External strain imposed on gamma-In2Se3 and InSe exhibits a negligible effect during the "
              "phase transition process."]),
        ]
        for paper, texts in papers:
            with db.transaction():
                paper_id = db.save_paper(paper)
            attachment = Attachment("A" + paper.key, "PDF", "application/pdf", "paper.pdf", "imported_file", None)
            pages = tuple(PageText(str(paper_id), paper.key, attachment.key, paper.title, paper.doi, index, text)
                          for index, text in enumerate(texts, 1))
            db.save_attachment(paper_id, attachment, Path(f"{paper.key}.pdf"), f"fp-{paper.key}", "OK", None,
                               pages, content_hash=f"hash-{paper.key}")
