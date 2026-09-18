"""Lexical normalization with offsets back to the unmodified source text."""

import re
import unicodedata

NORMALIZER_VERSION = "1"


def normalized_with_offsets(text: str) -> tuple[str, list[int]]:
    """Normalize typography and chemical formula spacing, retaining source offsets."""
    chars, offsets = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            if normalized != "\u00ad":
                chars.append(normalized)
                offsets.append(index)
    value = "".join(chars)

    def replace(pattern: str, transform, flags: int = 0) -> None:
        nonlocal value, offsets
        parts, mapped, end = [], [], 0
        for match in re.finditer(pattern, value, flags):
            parts.append(value[end:match.start()])
            mapped.extend(offsets[end:match.start()])
            replacement = transform(match.group())
            parts.append(replacement)
            # Every replacement maps into the exact source span it represents.
            mapped.extend(offsets[match.start() + round(i * (len(match.group()) - 1) /
                          max(1, len(replacement) - 1))] for i in range(len(replacement)))
            end = match.end()
        parts.append(value[end:])
        mapped.extend(offsets[end:])
        value, offsets = "".join(parts), mapped

    replace(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", lambda _: "")
    # General formula typography, e.g. In 2 Se 3 and Mo S 2 are not conflated
    # with ordinary prose. Formulae with two numbered elements are joined.
    replace(r"\b[A-Z][a-z]?\s*\d+(?:\s*[A-Z][a-z]?\s*\d+)+\b",
            lambda word: re.sub(r"\s+", "", word))
    replace(r"\bband[\s-]+gap(s?)\b", lambda word: "bandgaps" if word.endswith("s") else "bandgap", re.I)
    return value, offsets


def normalize(text: str) -> str:
    """Return text suitable for an FTS index; source text is stored separately."""
    return normalized_with_offsets(text)[0]


def query_terms(query: str) -> list[str]:
    """Treat user input as literal words, never executable FTS syntax."""
    if len(query) > 1000:
        raise ValueError("Search is limited to 1,000 characters.")
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", normalize(query).lower())))
    if len(terms) > 32:
        raise ValueError("Use at most 32 search words.")
    return terms


def source_snippet(text: str, terms: list[str], length: int = 320) -> str:
    """Choose a dense matching excerpt and return only original PDF text."""
    normalized, offsets = normalized_with_offsets(text)
    lowered = normalized.lower()
    positions = [m.start() for term in terms for m in re.finditer(re.escape(term), lowered)]
    if not positions:
        start = 0
    else:
        # Favor a window that shows several distinct query terms.
        best = max(positions, key=lambda pos: sum(
            term in lowered[max(0, pos - 80):pos + length - 80] for term in terms))
        start = max(0, offsets[best] - 80)
    end = min(len(text), start + length)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
