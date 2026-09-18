"""Deterministic paper-level identity resolution, independent of retrieval.

Zotero can hold more than one library item for the same real-world paper
(duplicate imports, metadata variants). The application database already
groups multiple PDF attachments under a single Zotero parent item/paper row,
so this module only needs to merge distinct `papers` rows that describe the
same underlying publication. Matching is intentionally conservative: a DOI
mismatch always wins over a title match, and title-only matching requires an
exact normalized title, never a similarity threshold.
"""

from dataclasses import dataclass
import hashlib
import re
import unicodedata

from app.search.database import Database


@dataclass(frozen=True)
class PaperRecord:
    """A single row from the local `papers` table."""

    paper_id: int
    zotero_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    doi: str


@dataclass(frozen=True)
class PaperIdentity:
    """The logical, deduplicated paper that one or more `papers` rows resolve to."""

    canonical_id: str
    doi: str | None
    zotero_item_keys: tuple[str, ...]
    title: str
    authors: tuple[str, ...]
    year: str | None
    paper_ids: tuple[int, ...]


def normalize_doi(doi: str | None) -> str | None:
    """Strip protocol/prefix noise and case so equivalent DOIs compare equal."""
    if not doi:
        return None
    value = doi.strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = value.removeprefix("doi:").strip()
    return value or None


# V0.3.2: Greek phase-symbol letters and their spelled-out equivalents are
# deterministically equivalent for title comparison (e.g. a cited reference's
# ASCII-transliterated "gamma-InSe" vs. the local Zotero title's "γ-InSe").
# This is symbol substitution, not fuzzy matching: every token is mapped to
# exactly one canonical spelling, or left untouched.
_GREEK_PHASE_SYMBOLS = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "κ": "kappa",
}


def normalize_title(title: str | None) -> str:
    """Casefold, strip punctuation/whitespace, and normalize Greek phase symbols
    for exact-match comparison only (never a similarity threshold)."""
    value = unicodedata.normalize("NFKC", title or "").casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return value
    # Token-aware: only a whole token that IS a Greek phase-symbol letter is
    # remapped, so unrelated substrings (e.g. a word merely containing "gamma"
    # or a Greek letter fused into a larger token with no separator) are left
    # exactly as-is.
    return " ".join(_GREEK_PHASE_SYMBOLS.get(token, token) for token in value.split(" "))


def first_author_surname(authors: tuple[str, ...]) -> str | None:
    """Best-effort surname of the first author, for a three-field fallback match.

    NFKC-normalizes before casefolding (mirroring `normalize_title` above) so a
    PDF-extracted typographic ligature in a surname (e.g. "Eﬀendi") still
    casefold-matches the clean Zotero-sourced form ("Effendi")."""
    if not authors or not authors[0].strip():
        return None
    parts = unicodedata.normalize("NFKC", authors[0].strip()).split()
    return parts[-1].casefold() if parts else None


def _completeness(record: PaperRecord) -> tuple[int, int, int, int]:
    """Rank records so the identity's display fields come from the richest source."""
    return (
        1 if normalize_doi(record.doi) else 0,
        len(record.authors),
        1 if record.year else 0,
        len(record.title or ""),
    )


def _make_identity(kind: str, key: str, records: list[PaperRecord]) -> PaperIdentity:
    primary = max(records, key=_completeness)
    doi = normalize_doi(primary.doi)
    for record in records:
        doi = doi or normalize_doi(record.doi)
    digest = hashlib.sha1(f"{kind}:{key}".encode("utf-8")).hexdigest()[:16]
    return PaperIdentity(
        canonical_id=f"{kind}:{digest}",
        doi=doi,
        zotero_item_keys=tuple(sorted({r.zotero_key for r in records})),
        title=primary.title,
        authors=primary.authors,
        year=primary.year,
        paper_ids=tuple(sorted({r.paper_id for r in records})),
    )


def resolve_identities(records: list[PaperRecord]) -> dict[int, PaperIdentity]:
    """Map each local paper_id to its deduplicated PaperIdentity.

    Priority: (1) normalized DOI, (2) normalized title + first author + year,
    (3) normalized title alone, (4) an untitled/standalone fallback per row.
    A DOI mismatch is never overridden by a title match at any later stage.
    """
    remaining = list(records)

    by_doi: dict[str, list[PaperRecord]] = {}
    without_doi: list[PaperRecord] = []
    for record in remaining:
        doi = normalize_doi(record.doi)
        if doi:
            by_doi.setdefault(doi, []).append(record)
        else:
            without_doi.append(record)

    by_title_author_year: dict[tuple[str, str, str], list[PaperRecord]] = {}
    without_tay: list[PaperRecord] = []
    for record in without_doi:
        title = normalize_title(record.title)
        author = first_author_surname(record.authors)
        if title and title != normalize_title("(Untitled)") and author and record.year:
            by_title_author_year.setdefault((title, author, record.year), []).append(record)
        else:
            without_tay.append(record)

    by_title: dict[str, list[PaperRecord]] = {}
    without_title: list[PaperRecord] = []
    for record in without_tay:
        title = normalize_title(record.title)
        if title and title != normalize_title("(Untitled)"):
            by_title.setdefault(title, []).append(record)
        else:
            without_title.append(record)

    identity_by_paper_id: dict[int, PaperIdentity] = {}
    for doi, group in by_doi.items():
        identity = _make_identity("doi", doi, group)
        for record in group:
            identity_by_paper_id[record.paper_id] = identity
    for key, group in by_title_author_year.items():
        identity = _make_identity("tay", "|".join(key), group)
        for record in group:
            identity_by_paper_id[record.paper_id] = identity
    for title, group in by_title.items():
        identity = _make_identity("title", title, group)
        for record in group:
            identity_by_paper_id[record.paper_id] = identity
    for record in without_title:
        identity = _make_identity("paper", str(record.paper_id), [record])
        identity_by_paper_id[record.paper_id] = identity

    return identity_by_paper_id


def load_paper_records(db: Database) -> list[PaperRecord]:
    """Read all papers from the application database as plain identity records."""
    import json

    rows = db.connection.execute("SELECT id, zotero_key, title, authors, year, doi FROM papers").fetchall()
    return [
        PaperRecord(row["id"], row["zotero_key"], row["title"], tuple(json.loads(row["authors"])),
                    row["year"], row["doi"])
        for row in rows
    ]
