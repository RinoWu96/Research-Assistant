"""Minimal, conservative in-text citation and bibliography extraction.

This is a proof-of-concept for the numbered citation style ([30], [30,31],
[30-33]) used by most papers in the target collection, reused from PDF text
already extracted by `app.parser.pdf_parser`. It never invents a reference:
if a "References"/"Bibliography" heading cannot be found with enough
supporting structural evidence, bibliography parsing returns an empty
mapping rather than guessing over body text.

Real two-column academic PDFs commonly extract with the "References" heading
landing mid-line (interleaved with the previous page's second-column text)
instead of alone on its own line, and with the bibliography entries
themselves interleaved column-by-column rather than in numeric order. The
standalone-heading regex below remains the highest-confidence detector;
`_find_embedded_heading` is a conservative fallback gated on multiple
structural signals (document position, nearby numbered entries, year-like
patterns), and `_split_two_column_streams` is a conservative fallback for
splitting interleaved two-column entry text before parsing, used only when
the plain single-stream parse looks unreliable (numbers not roughly
increasing). Neither fallback ever alters the stored passage text; both only
change how much of it we can confidently attribute to which entry.
"""

from dataclasses import dataclass
import re

SECTION_HEADING_RE = re.compile(r"(?im)^\s*(references|bibliography)\s*$")
STOP_HEADING_RE = re.compile(r"(?im)^\s*(supporting information|supplementary|appendix|acknowledgh?ements?)\s*$")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

CITATION_MARKER_RE = re.compile(r"\[(\d{1,4}(?:\s*[,–—-]\s*\d{1,4})*)\]")
BRACKET_ENTRY_RE = re.compile(r"\[(\d{1,4})\]\s*")
LINE_NUMBER_ENTRY_RE = re.compile(r"(?m)^\s*(\d{1,4})[.)]\s+(?=[A-Z(])")

# Fallback heading candidate: the word standing alone (not part of a larger
# word), anywhere in the text - including mid-line. Gated heavily below
# before it is trusted; see `_find_embedded_heading`.
_EMBEDDED_HEADING_CANDIDATE_RE = re.compile(r"(?<![A-Za-z])(References|REFERENCES|Bibliography|BIBLIOGRAPHY)(?![A-Za-z])")

# A candidate preceded by one of these (case-insensitive) words is almost
# always prose ("see references...", "the references...") rather than a
# heading.
_HEADING_PRECEDING_STOPWORDS = {
    "the", "these", "those", "such", "other", "some", "many", "few", "most",
    "prior", "previous", "further", "additional", "corresponding", "listed",
    "cited", "above", "following", "see", "in", "and", "or", "its", "their",
    "our", "aforementioned", "relevant", "numerous", "full", "cross",
}

# A candidate immediately followed (ignoring whitespace) by one of these
# lowercase words is almost always prose ("references have shown...",
# "references indicate...") rather than a heading.
_HEADING_FOLLOWING_STOPWORDS = {
    "have", "has", "had", "show", "shows", "showed", "indicate", "indicates",
    "suggest", "suggests", "confirm", "confirms", "reveal", "reveals",
    "demonstrate", "demonstrates", "support", "supports", "state", "states",
    "note", "notes", "report", "reports", "cite", "cites", "to", "for", "in",
    "on", "of", "and", "or", "that", "which", "are", "were", "was", "is",
    "include", "includes", "listed", "provided", "given", "discussed",
    "discuss", "below", "above", "herein", "therein",
}

# How far into the document (as a fraction of its length) a heading candidate
# must appear before the embedded-heading fallback will consider it.
_MIN_HEADING_POSITION_RATIO = 0.5
# How far past a heading candidate to look for corroborating bibliography
# structure (numbered entries, years).
_HEADING_EVIDENCE_WINDOW = 4000
_MIN_HEADING_EVIDENCE_ENTRIES = 3
_MIN_HEADING_EVIDENCE_YEARS = 2

# Two-column extraction artifact: PDF text extractors commonly pad column
# text with a wide run of spaces where the physical page had a column gutter.
_COLUMN_GAP_RE = re.compile(r" {6,}")
_MIN_MONOTONIC_FRACTION = 0.7


@dataclass(frozen=True)
class CitationMarker:
    raw: str
    reference_numbers: tuple[int, ...]
    start: int
    end: int


@dataclass(frozen=True)
class BibliographyEntry:
    number: int
    raw_text: str
    doi: str | None
    year: str | None
    authors_guess: str | None
    title_guess: str | None


def _expand_marker(inner: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", inner):
        range_match = re.fullmatch(r"(\d+)\s*[–—-]\s*(\d+)", part)
        if range_match:
            start, end = int(range_match[1]), int(range_match[2])
            if 0 <= end - start < 50:
                numbers.extend(range(start, end + 1))
        elif part.isdigit():
            numbers.append(int(part))
    return tuple(numbers)


def find_citation_markers(text: str) -> list[CitationMarker]:
    """Find numbered in-text citation markers such as [30], [30,31], [30-33]."""
    markers = []
    for match in CITATION_MARKER_RE.finditer(text):
        numbers = _expand_marker(match.group(1))
        if numbers:
            markers.append(CitationMarker(match.group(0), numbers, match.start(), match.end()))
    return markers


def _nearby_word(text: str, index: int, *, direction: int) -> str | None:
    """The nearest contiguous alphabetic token adjacent to `index`.

    `direction` is -1 to look backward (skipping trailing whitespace/
    punctuation right before `index`) or +1 to look forward (skipping
    leading whitespace right after `index`). Only looks within the current
    line, never crossing a newline, so a heading on its own line never
    "sees" unrelated text on the previous/next line.
    """
    if direction < 0:
        line_start = text.rfind("\n", 0, index) + 1
        segment = text[line_start:index]
        match = re.search(r"([A-Za-z]+)[\s.,;:]*$", segment)
    else:
        line_end = text.find("\n", index)
        segment = text[index:] if line_end == -1 else text[index:line_end]
        match = re.match(r"\s*([A-Za-z]+)", segment)
    return match.group(1).casefold() if match else None


def _looks_like_prose_usage(full_text: str, match: re.Match) -> bool:
    preceding = _nearby_word(full_text, match.start(), direction=-1)
    if preceding in _HEADING_PRECEDING_STOPWORDS:
        return True
    following = _nearby_word(full_text, match.end(), direction=1)
    if following in _HEADING_FOLLOWING_STOPWORDS:
        return True
    return False


def _has_bibliography_evidence(full_text: str, match: re.Match) -> bool:
    window = full_text[match.end():match.end() + _HEADING_EVIDENCE_WINDOW]
    numbered_entries = {m.group(1) for m in BRACKET_ENTRY_RE.finditer(window)}
    if len(numbered_entries) < _MIN_HEADING_EVIDENCE_ENTRIES:
        numbered_entries = {m.group(1) for m in LINE_NUMBER_ENTRY_RE.finditer(window)}
    if len(numbered_entries) < _MIN_HEADING_EVIDENCE_ENTRIES:
        return False
    return len(YEAR_RE.findall(window)) >= _MIN_HEADING_EVIDENCE_YEARS


def _find_embedded_heading(full_text: str) -> re.Match | None:
    """Conservative fallback for a "References" heading that is not alone on
    its own line (e.g. two-column extraction interleaves it with unrelated
    text). Requires the candidate to be in the later portion of the document,
    not read as ordinary prose, and followed nearby by dense, year-bearing
    numbered-entry structure resembling a bibliography.
    """
    min_position = len(full_text) * _MIN_HEADING_POSITION_RATIO
    for match in _EMBEDDED_HEADING_CANDIDATE_RE.finditer(full_text):
        if match.start() < min_position:
            continue
        if _looks_like_prose_usage(full_text, match):
            continue
        if not _has_bibliography_evidence(full_text, match):
            continue
        return match
    return None


def _find_reference_heading(full_text: str) -> re.Match | None:
    return SECTION_HEADING_RE.search(full_text) or _find_embedded_heading(full_text)


# A single bibliography-style author entry, e.g. "Zhao, W." or "Duan, C. G."
_AUTHOR_ENTRY_RE = re.compile(r"[A-Z][A-Za-zÀ-ɏ'-]+,\s*(?:[A-Z]\.\s*){1,4}")
_ET_AL_RE = re.compile(r"\bet\s+al\.\s+")


def _split_authors_and_remainder(raw_text: str) -> tuple[str | None, str]:
    """Split a raw bibliography entry into an author-list prefix and the
    remaining "Title. Journal, Year..." text that follows it.

    Real multi-author entries look like "Surname, F. M.; Surname2, F. M. et
    al. Title. Journal Year." or, for short author lists, "Surname, F. M.;
    ...; LastSurname, F. M. Title. Journal Year." (no "et al."). A naive split
    on the first ". " breaks on every author's own initials, so this looks
    for the "et al." marker first, then the last "Surname, Initials" block,
    before falling back (returning `None`) to let the caller use its
    original, more conservative single-author-friendly split.
    """
    et_al = _ET_AL_RE.search(raw_text)
    if et_al:
        return raw_text[:et_al.end()].strip(), raw_text[et_al.end():].strip()
    author_entries = list(_AUTHOR_ENTRY_RE.finditer(raw_text))
    if author_entries:
        last = author_entries[-1]
        return raw_text[:last.end()].strip(), raw_text[last.end():].strip()
    return None, raw_text


def _guess_authors_and_title(raw_text: str, year_match: re.Match | None) -> tuple[str | None, str | None]:
    """Best-effort split of "Authors. Title. Journal, Year" style raw reference text.

    This is deliberately low-confidence: it is only used as a fallback when no
    DOI is present, and citation resolution still requires an exact
    normalized-title match, so a noisy guess fails safe by simply not matching.
    """
    authors_segment, remainder = _split_authors_and_remainder(raw_text)
    if authors_segment is None:
        segments = [segment.strip() for segment in re.split(r"\.\s+", raw_text) if segment.strip()]
        if not segments:
            return None, None
        authors_guess = segments[0] if (len(segments[0]) < 200 and
                                         ("," in segments[0] or " et al" in segments[0].lower())) else None
        title_guess = segments[1] if len(segments) > 1 and len(segments[1]) < 300 else None
        return authors_guess, title_guess
    authors_guess = authors_segment if authors_segment and len(authors_segment) < 200 else None
    title_segments = [segment.strip() for segment in re.split(r"\.\s+", remainder, maxsplit=1) if segment.strip()]
    title_guess = title_segments[0] if title_segments and len(title_segments[0]) < 300 else None
    return authors_guess, title_guess


def _parse_entries_from_stream(stream: str) -> tuple[dict[int, BibliographyEntry], list[int]]:
    """Parse numbered entries from one linear text stream (a full reference
    tail, or one column of a two-column reference tail). Returns the parsed
    entries plus the entry numbers in the order they occurred, so callers can
    judge whether that order looks like a clean, non-interleaved sequence.
    """
    bracket_positions = list(BRACKET_ENTRY_RE.finditer(stream))
    line_positions = list(LINE_NUMBER_ENTRY_RE.finditer(stream))
    positions = bracket_positions if len(bracket_positions) >= len(line_positions) else line_positions
    if len(positions) < 2:
        return {}, []

    entries: dict[int, BibliographyEntry] = {}
    numbers: list[int] = []
    for index, match in enumerate(positions):
        try:
            number = int(match.group(1))
        except ValueError:
            continue
        numbers.append(number)
        end = positions[index + 1].start() if index + 1 < len(positions) else len(stream)
        raw = stream[match.end():end].strip()
        if not raw:
            continue
        doi_match = DOI_RE.search(raw)
        year_match = YEAR_RE.search(raw)
        authors_guess, title_guess = _guess_authors_and_title(raw, year_match)
        entries[number] = BibliographyEntry(
            number=number,
            raw_text=raw,
            doi=doi_match.group(0).rstrip(".,);") if doi_match else None,
            year=year_match.group(0) if year_match else None,
            authors_guess=authors_guess,
            title_guess=title_guess,
        )
    return entries, numbers


def _is_monotonic_enough(numbers: list[int]) -> bool:
    if len(numbers) < 2:
        return True
    increasing = sum(1 for a, b in zip(numbers, numbers[1:]) if b > a)
    return increasing / (len(numbers) - 1) >= _MIN_MONOTONIC_FRACTION


def _split_two_column_streams(tail: str) -> tuple[str, str] | None:
    """Best-effort separation of a two-column reference tail into independent
    left/right column text streams, using the wide whitespace gap PDF
    extractors insert between columns. Returns None when the text doesn't
    consistently show such a gap (i.e. it is probably already single-column).
    """
    lines = tail.split("\n")
    left_lines: list[str] = []
    right_lines: list[str] = []
    gapped = 0
    non_blank = 0
    for line in lines:
        if not line.strip():
            left_lines.append("")
            right_lines.append("")
            continue
        non_blank += 1
        gap = _COLUMN_GAP_RE.search(line)
        if gap:
            gapped += 1
            left_lines.append(line[:gap.start()])
            right_lines.append(line[gap.end():])
        else:
            left_lines.append(line)
            right_lines.append("")
    if non_blank < 6 or gapped < 5 or gapped / non_blank < 0.4:
        return None
    return "\n".join(left_lines), "\n".join(right_lines)


def _merge_column_entries(left: dict[int, BibliographyEntry],
                           right: dict[int, BibliographyEntry]) -> dict[int, BibliographyEntry]:
    merged: dict[int, BibliographyEntry] = {}
    conflicts: set[int] = set()
    for source in (left, right):
        for number, entry in source.items():
            if number in merged and merged[number].raw_text != entry.raw_text:
                conflicts.add(number)
            else:
                merged.setdefault(number, entry)
    for number in conflicts:
        merged.pop(number, None)
    return merged


def parse_bibliography(full_text: str) -> dict[int, BibliographyEntry]:
    """Parse a "References" section into numbered entries; {} if none is found."""
    heading = _find_reference_heading(full_text)
    if not heading:
        return {}
    tail = full_text[heading.end():]
    stop = STOP_HEADING_RE.search(tail)
    if stop:
        tail = tail[:stop.start()]

    entries, numbers = _parse_entries_from_stream(tail)
    if len(entries) >= 2 and _is_monotonic_enough(numbers):
        return entries

    # The numbering isn't a clean increasing sequence - a strong signal of
    # two-column interleaving. Try to un-interleave by column before giving
    # up, rather than returning the (likely cross-contaminated) single-stream
    # result: an entry built from text that straddles two different columns
    # risks silently merging two unrelated references together.
    columns = _split_two_column_streams(tail)
    if columns:
        left_entries, _ = _parse_entries_from_stream(columns[0])
        right_entries, _ = _parse_entries_from_stream(columns[1])
        merged = _merge_column_entries(left_entries, right_entries)
        if len(merged) >= 2:
            return merged

    return {}
