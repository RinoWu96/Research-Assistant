"""Typed metadata independent of HTTP and desktop widgets."""

from dataclasses import dataclass, field
import re
from typing import Any

# Source identity used when Zotero sends no Zotero-Server-ID header.
LEGACY_SOURCE_IDENTITY = "local-personal-library"


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
