"""Run live listing with python -m tests.test_zotero_connection."""

import argparse
import logging
import sys
import unittest

import httpx

from app.zotero.client import ZoteroClient, ZoteroError


class ConnectionTests(unittest.TestCase):
    def test_read_only_listing_and_metadata(self) -> None:
        calls = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, "localhost")
            path = request.url.path
            if path == "/api/":
                return httpx.Response(200, headers={"Zotero-API-Version": "3"})
            if path.endswith("/collections"):
                data = [{"key": "COL1", "data": {"name": "Strain", "parentCollection": False}}]
            elif path.endswith("/items"):
                data = [{"key": "PAPER1", "data": {
                    "itemType": "journalArticle", "title": "Example", "date": "2024-05",
                    "publicationTitle": "Journal", "DOI": "10.example/test",
                    "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
                    "tags": [{"tag": "strain"}],
                }}, {"key": "NOTE1", "data": {"itemType": "note"}}]
            elif path.endswith("/children"):
                data = [{"key": "PDF1", "data": {"itemType": "attachment", "title": "PDF",
                         "contentType": "application/pdf", "filename": "paper.pdf", "linkMode": "imported_file"}}]
            elif path.endswith("/file/view/url"):
                return httpx.Response(200, text="file:///C:/Research/paper.pdf")
            else:
                self.fail(f"Unexpected request: {request.url}")
            return httpx.Response(200, json=data)

        with ZoteroClient(transport=httpx.MockTransport(handle)) as client:
            client.check_connection()
            self.assertEqual(client.collections()[0].name, "Strain")
            papers = client.collection_items("COL1")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual((paper.key, paper.title, paper.year, paper.journal, paper.doi,
                          paper.authors, paper.tags, paper.item_type),
                         ("PAPER1", "Example", "2024", "Journal", "10.example/test",
                          ("Ada Lovelace",), ("strain",), "journalArticle"))
        self.assertEqual(paper.attachments[0].local_url, "file:///C:/Research/paper.pdf")
        self.assertEqual(len(calls), 5)

    def test_unavailable(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)
        with ZoteroClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaisesRegex(ZoteroError, "Start Zotero"):
                client.check_connection()

    def test_error_responses(self) -> None:
        for status, message in [(403, "Allow other applications"), (500, "HTTP 500"), (302, "HTTP 302")]:
            with self.subTest(status=status):
                with ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
                    with self.assertRaisesRegex(ZoteroError, message):
                        client.check_connection()

    def test_pagination(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["start"])
            rows = [{"key": f"C{i}", "data": {"name": str(i)}} for i in range(start, min(start + 100, 101))]
            return httpx.Response(200, json=rows)
        with ZoteroClient(transport=httpx.MockTransport(handle)) as client:
            self.assertEqual(len(client.collections()), 101)

    def test_invalid_json_and_empty_collection(self) -> None:
        with ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="invalid"))) as client:
            with self.assertRaisesRegex(ZoteroError, "invalid JSON"):
                client.collections()
        with ZoteroClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))) as client:
            self.assertEqual(client.collection_items("EMPTY"), [])

    def test_cloud_endpoint_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ZoteroClient("https://api.zotero.org/api/")

    def test_library_snapshot_groups_children_and_excludes_trash(self) -> None:
        def handle(request):
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("/file/view/url"):
                return httpx.Response(200, text="file:///C:/paper.pdf")
            return httpx.Response(200, json=[
                {"key": "P1", "data": {"itemType": "journalArticle", "title": "Paper", "collections": ["C1"]}},
                {"key": "A1", "data": {"itemType": "attachment", "parentItem": "P1", "contentType": "application/pdf"}},
                {"key": "N1", "data": {"itemType": "note"}},
                {"key": "P2", "data": {"itemType": "journalArticle", "deleted": 1}},
            ])
        with ZoteroClient(transport=httpx.MockTransport(handle)) as client:
            papers = list(client.library_items())
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].collection_keys, ("C1",))
        self.assertEqual(papers[0].attachments[0].key, "A1")


def main() -> int:
    """Print real collection/item metadata; this never writes to Zotero."""
    # Windows redirected terminals may otherwise use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="Exact collection key or name; otherwise choose interactively")
    parser.add_argument("--base-url", default="http://localhost:23119/api/", help="Local endpoint; use http://127.0.0.1:23119/api/ if localhost is slow")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        with ZoteroClient(args.base_url) as client:
            client.check_connection()
            print("Connected to Zotero: OK\n\nCollections:")
            collections = client.collections()
            for index, collection in enumerate(collections, 1):
                print(f"{index}. {collection.name} [{collection.key}] (parent: {collection.parent_key or 'none'})")
            if not collections:
                print("No collections found in the personal library.")
                return 0
            if args.collection:
                matches = [c for c in collections if c.key == args.collection]
                if not matches:
                    matches = [c for c in collections if c.name == args.collection]
                if len(matches) != 1:
                    raise ValueError("Collection not found or name is ambiguous; use its displayed key.")
                selected = matches[0]
            else:
                choice = int(input("\nSelect collection number: "))
                if not 1 <= choice <= len(collections):
                    raise ValueError("Select a collection number from the list.")
                selected = collections[choice - 1]
            papers = client.collection_items(selected.key)
            print(f"\nCollection: {selected.name}\nFound {len(papers)} literature items")
            for index, paper in enumerate(papers, 1):
                print(f"\n[{index}]\nItem key: {paper.key}\nTitle: {paper.title}\n"
                      f"Authors: {', '.join(paper.authors) or '(not provided)'}\nYear: {paper.year or '(not provided)'}\n"
                      f"Journal: {paper.journal}\nDOI: {paper.doi}\nTags: {', '.join(paper.tags)}\n"
                      f"Item type: {paper.item_type}\nPDF attachment: {'Yes' if paper.attachments else 'None retrieved'}")
                for attachment in paper.attachments:
                    print(f"  Key: {attachment.key}\n  Title: {attachment.title}\n"
                          f"  Content type: {attachment.content_type}\n  Filename: {attachment.filename}\n"
                          f"  Link mode: {attachment.link_mode}\n  API path: {attachment.api_path}\n"
                          f"  Local URL: {attachment.local_url or attachment.location_error}")
            return 0
    except (ZoteroError, ValueError, EOFError) as exc:
        print(f"Connection/listing test failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
