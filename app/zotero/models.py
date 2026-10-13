"""Typed metadata independent of HTTP and desktop widgets."""

from dataclasses import dataclass, field
import re
from typing import Any

# Source identity used when Zotero sends no Zotero-Server-ID header.
LEGACY_SOURCE_IDENTITY = "local-personal-library"
# PDFs saved next to a stored attachment without being registered in Zotero
# (typically supporting information) are indexed under "<KEY>-SI-<hash>".
SUPPLEMENTARY_KEY_MARKER = "-SI-"


def is_supplementary_key(attachment_key: str) -> bool:
    return SUPPLEMENTARY_KEY_MARKER in attachment_key


# Publisher SI naming: "Supporting Information", ACS "_si_001", Nature "MOESM",
# Elsevier "mmc1", Wiley "suppmat", Science "_sm". Deliberately not a bare
# "SI", which also matches author names and silicon.
_SUPPLEMENTARY_NAME = re.compile(
    r"support(?:ing)?\s+info|supplement(?:ary|al)|suppmat|moesm\d|"
    r"(?:^|[_.-])si[_.-]\d|(?:^|[_.-])(?:sm|mmc\d+)\.pdf$", re.IGNORECASE)


def looks_supplementary(*names: str | None) -> bool:
    return any(name and _SUPPLEMENTARY_NAME.search(name) for name in names)


@dataclass(frozen=True)
class Collection:
    key: str
    name: str
    parent_key: str | None = None


@dataclass(frozen=True)
class Attachment:
    key: str
    title: str
    content_type: str
    filename: str | None
    link_mode: str | None
    api_path: str | None
    local_url: str | None = None
    location_error: str | None = None


@dataclass(frozen=True)
class Paper:
    key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    tags: tuple[str, ...]
    item_type: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    collection_keys: tuple[str, ...] = field(default_factory=tuple)
    attachments_complete: bool = True

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> "Paper":
        """Normalize an API item while tolerating absent bibliography fields."""
        data = record.get("data", record)
        authors = tuple(
            creator.get("name") or " ".join(
                part for part in (creator.get("firstName"), creator.get("lastName")) if part
            )
            for creator in data.get("creators", [])
            if creator.get("creatorType") == "author"
        )
        year = re.search(r"\b\d{4}\b", data.get("date", ""))
        return cls(
            key=record.get("key") or data["key"],
            title=data.get("title") or "(Untitled)",
            authors=authors,
            year=year.group() if year else None,
            journal=data.get("publicationTitle", ""),
            doi=data.get("DOI", ""),
            tags=tuple(tag["tag"] for tag in data.get("tags", []) if tag.get("tag")),
            item_type=data.get("itemType", "unknown"),
            collection_keys=tuple(data.get("collections", [])),
        )


def is_supplementary(attachment: "Attachment") -> bool:
    """SI found beside a stored attachment, or a Zotero attachment named like one."""
    return is_supplementary_key(attachment.key) or looks_supplementary(attachment.title, attachment.filename)
