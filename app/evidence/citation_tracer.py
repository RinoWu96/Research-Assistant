"""Trace a SECONDARY passage's in-text citations back to a local primary source.

Resolution is deliberately conservative (Task 4/5 of the V0.3 brief): a DOI
match is required for high-confidence resolution; the title+author+year
fallback requires an exact normalized title (never a similarity score) plus a
matching first-author surname and year; anything else stays unresolved rather
than guessing. Never assumes the cited paper supports the claim - the caller
must independently re-retrieve and re-classify passages from it.
"""

from dataclasses import dataclass
from enum import Enum

from app.evidence.paper_identity import (
    PaperIdentity,
    PaperRecord,
    first_author_surname,
    normalize_doi,
    normalize_title,
    resolve_identities,
)
from app.evidence.reference_parser import BibliographyEntry, find_citation_markers, parse_bibliography
from app.search.database import Database


class CitationStatus(str, Enum):
    RESOLVED_LOCAL = "resolved_local"
    REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY = "reference_identified_but_not_in_library"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class CitationTrace:
    marker: str
    reference_number: int
    status: CitationStatus
    reference_doi: str | None
    reference_title_guess: str | None
    reference_authors_guess: str | None
    reference_year: str | None
    resolved_identity: PaperIdentity | None

    @property
    def resolved_paper_id(self) -> int | None:
        return self.resolved_identity.paper_ids[0] if self.resolved_identity else None


def load_attachment_fulltext(db: Database, attachment_key: str) -> str:
    """Concatenate an attachment's already-extracted page text, in page order."""
    rows = db.connection.execute(
        """SELECT g.text FROM pages g JOIN attachments a ON a.id = g.attachment_id
           WHERE a.zotero_key = ? ORDER BY g.page_number""", (attachment_key,)).fetchall()
    return "\n".join(row[0] for row in rows)


def _resolve_reference(entry: BibliographyEntry | None, identities: list[PaperIdentity]) -> tuple[
        CitationStatus, PaperIdentity | None]:
    if entry is None:
        return CitationStatus.UNRESOLVED, None
    doi = normalize_doi(entry.doi)
    if doi:
        matches = [identity for identity in identities if identity.doi == doi]
        if len(matches) == 1:
            return CitationStatus.RESOLVED_LOCAL, matches[0]
        if len(matches) > 1:
            return CitationStatus.AMBIGUOUS, None
        return CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY, None
    title = normalize_title(entry.title_guess) if entry.title_guess else ""
    authors = tuple(entry.authors_guess.split(",")[:1]) if entry.authors_guess else ()
    surname = first_author_surname(authors) if authors else None
    if title and surname and entry.year:
        matches = [identity for identity in identities
                   if normalize_title(identity.title) == title
                   and first_author_surname(identity.authors) == surname
                   and identity.year == entry.year]
        if len(matches) == 1:
            return CitationStatus.RESOLVED_LOCAL, matches[0]
        if len(matches) > 1:
            return CitationStatus.AMBIGUOUS, None
    return CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY, None


def trace_citations(passage_text: str, attachment_fulltext: str,
                     local_papers: list[PaperRecord]) -> list[CitationTrace]:
    """Resolve every unique numbered citation in `passage_text` against the local library."""
    markers = find_citation_markers(passage_text)
    if not markers:
        return []
    bibliography = parse_bibliography(attachment_fulltext)
    # resolve_identities keys by paper_id, so a merged identity appears once per
    # underlying paper row; collapse back to distinct identities before matching,
    # otherwise a duplicate-record merge would look like an ambiguous citation.
    identities = list({identity.canonical_id: identity
                       for identity in resolve_identities(local_papers).values()}.values())
    traces: list[CitationTrace] = []
    seen_numbers: set[int] = set()
    for marker in markers:
        for number in marker.reference_numbers:
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            entry = bibliography.get(number)
            status, identity = _resolve_reference(entry, identities)
            traces.append(CitationTrace(
                marker=f"[{number}]", reference_number=number, status=status,
                reference_doi=entry.doi if entry else None,
                reference_title_guess=entry.title_guess if entry else None,
                reference_authors_guess=entry.authors_guess if entry else None,
                reference_year=entry.year if entry else None,
                resolved_identity=identity,
            ))
    return traces
