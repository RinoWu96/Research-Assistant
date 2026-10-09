"""GET-only Local API adapter. Never opens Zotero's database or writes files."""

from dataclasses import replace
import logging
from typing import Any
from collections import defaultdict
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx

from app.config import READ_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS, ZOTERO_API_URL
from app.zotero.models import LEGACY_SOURCE_IDENTITY, Attachment, Collection, Paper

logger = logging.getLogger(__name__)


class ZoteroError(RuntimeError):
    """A Zotero connection or response error suitable for display to a user."""


class ZoteroClient:
    """Read the personal library through loopback HTTP only."""

    def __init__(self, base_url: str = ZOTERO_API_URL,
                 transport: httpx.BaseTransport | None = None) -> None:
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.port != 23119 or url.path != "/api/" or url.query
                or url.fragment or url.username or url.password):
            raise ValueError("Zotero must use the local http://localhost:23119/api/ endpoint.")
        self._http = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS),
            follow_redirects=False, trust_env=False, transport=transport,
            headers={"Zotero-API-Version": "3", "Accept": "application/json"},
        )
        self.source_identity = LEGACY_SOURCE_IDENTITY

    def __enter__(self) -> "ZoteroClient":
        return self

    def __exit__(self, *args: object) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if ":" in path or path.startswith("/") or ".." in path:
            raise ValueError("Only relative Local API paths are allowed.")
        try:
            response = self._http.get(path, params=params)
        except httpx.ConnectError as exc:
            raise ZoteroError("Cannot reach Zotero. Start Zotero and enable its Local API in Settings > Advanced.") from exc
        except httpx.TimeoutException as exc:
            raise ZoteroError("Zotero did not respond in time. Check Zotero and retry.") from exc
        except httpx.HTTPError as exc:
            raise ZoteroError(f"Zotero connection failed: {exc}") from exc
        if response.status_code == 403:
            raise ZoteroError("Zotero denied access. Enable 'Allow other applications on this computer to communicate with Zotero' in Settings > Advanced.")
        if not response.is_success:
            raise ZoteroError(f"Zotero Local API returned HTTP {response.status_code} for {path or '/api/'}.")
        return response

    def check_connection(self) -> None:
        """Probe API availability; a failed probe cannot prove the process is closed."""
        response = self._get("")
        if response.headers.get("Zotero-API-Version") != "3":
            raise ZoteroError("The local service did not identify itself as Zotero API version 3.")
        server_id = response.headers.get("Zotero-Server-ID")
        if server_id:
            self.source_identity = server_id
            self._http.headers["Zotero-Server-ID"] = server_id

    def _list(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        start, limit = 0, 100
        while True:
            response = self._get(path, {**(params or {}), "format": "json", "start": start, "limit": limit})
            try:
                batch = response.json()
            except ValueError as exc:
                raise ZoteroError("Zotero returned invalid JSON.") from exc
            if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
                raise ZoteroError("Zotero returned an unexpected item list.")
            records.extend(batch)
            if len(batch) < limit:
                return records
            start += len(batch)

    @staticmethod
    def _key(key: str) -> str:
        if not key or not key.isascii() or not key.isalnum():
            raise ValueError("Invalid Zotero key.")
        return key

    def collections(self) -> list[Collection]:
        """Return all personal-library collections, including nested collections."""
        return [Collection(row["key"], row["data"]["name"],
                           row["data"].get("parentCollection") or None)
                for row in self._list("users/0/collections")]

    def pdf_attachments(self, item_key: str) -> tuple[Attachment, ...]:
        """Read PDF child metadata and the API's local file URL, without opening PDFs."""
        attachments = []
        for row in self._list(f"users/0/items/{self._key(item_key)}/children"):
            data = row.get("data", {})
            if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf":
                continue
            key = self._key(row["key"])
            local_url, error = None, None
            try:
                value = self._get(f"users/0/items/{key}/file/view/url").text.strip()
                if urlsplit(value).scheme != "file":
                    raise ZoteroError("The API did not provide a local file URL.")
                local_url = value
            except ZoteroError as exc:
                error = str(exc)
                logger.warning("Attachment %s: %s", key, error)
            attachments.append(Attachment(
                key, data.get("title", ""), data["contentType"], data.get("filename"),
                data.get("linkMode"), data.get("path"), local_url, error,
            ))
        return tuple(attachments)

    def collection_items(self, collection_key: str) -> list[Paper]:
        """Return literature items directly in a collection, with PDF metadata."""
        papers = []
        for row in self._list(f"users/0/collections/{self._key(collection_key)}/items"):
            if row.get("data", {}).get("itemType") in {"attachment", "note", "annotation"}:
                continue
            try:
                paper = Paper.from_api(row)
            except (KeyError, TypeError, AttributeError) as exc:
                logger.warning("Skipping malformed item %s: %s", row.get("key", "unknown"), exc)
                continue
            try:
                paper = replace(paper, attachments=self.pdf_attachments(paper.key))
            except ZoteroError as exc:
                logger.warning("Could not retrieve attachments for %s: %s", paper.key, exc)
                paper = replace(paper, attachments_complete=False)
            papers.append(paper)
        return papers

    def library_items(self) -> Iterator[Paper]:
        """Stream papers from a complete metadata snapshot, resolving PDFs lazily."""
        # Annotations are skipped below anyway and can outnumber everything else.
        records = self._list("users/0/items", {"itemType": "-annotation"})
        children: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in records:
            data = row.get("data", {})
            if data.get("itemType") == "attachment" and not data.get("deleted"):
                children[data.get("parentItem", "")].append(row)
        for row in records:
            data = row.get("data", {})
            if data.get("itemType") in {"attachment", "note", "annotation"} or data.get("deleted"):
                continue
            paper = Paper.from_api(row)
            attachments = []
            for child in children[paper.key]:
                item = child["data"]
                if item.get("contentType") != "application/pdf":
                    continue
                key = self._key(child["key"])
                local_url, error = None, None
                try:
                    value = self._get(f"users/0/items/{key}/file/view/url").text.strip()
                    if urlsplit(value).scheme != "file":
                        raise ZoteroError("No local file URL returned.")
                    local_url = value
                except ZoteroError as exc:
                    error = str(exc)
                    logger.warning("Attachment %s: %s", key, error)
                attachments.append(Attachment(key, item.get("title", ""), item["contentType"],
                    item.get("filename"), item.get("linkMode"), item.get("path"), local_url, error))
            yield replace(paper, attachments=tuple(attachments))
