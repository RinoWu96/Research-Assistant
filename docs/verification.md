# V0.1 verification

## Incremental-update workflow revision

31 offline tests now pass, including new-item detection, metadata-only updates without
PDF parsing, content changes with identical size/mtime, attachment metadata/removal,
exclusive item counters, legacy hash backfill, additive v1→v2 migration, explicit
rebuild, and distinct Qt actions with persistent progress counts.

Live Strain update after migration: 26 metadata updates, zero PDFs parsed. A second
update: 0 indexed, 26 skipped, 0 updated, 0 failed, zero PDFs parsed. The existing
expanded application corpus (201 papers, 200 attachments, 2,618 pages at this check)
was preserved. The acceptance query still returns the original paper's page 5 as
its first result. Full Rebuild was tested against isolated fixtures, not invoked on
the user's entire library. Earlier V0.1 figures below describe the original corpus.

Validated on Windows, Python 3.13.14, PyMuPDF 1.28.2, PySide6 6.11.2, httpx 0.28.1.
Dependency versions are recorded in requirements-lock.txt.

## Real Zotero corpus

- 46 collections read using the Local API.
- `Strain` (`IXEN6C3A`): 26 papers, 26 PDFs, 237 physical pages; zero failures.
- Second indexing pass: 0 re-parsed, 26 unchanged, zero failures.
- The entire personal library was not pre-indexed; other collections can be indexed in the UI.

## Acceptance query

`strain bandgap In2Se3`

10 consecutive searches returned the same 8 pages in the same order, approximately
28–32 ms each in this run. Search uses no Zotero HTTP requests. Each cached page was
compared to a fresh extraction of the actual source PDF page. FTS5 integrity passed.

| Paper | DOI | Matching PDF pages |
| --- | --- | --- |
| Giant Bandgap Engineering in Two-Dimensional Ferroelectric α-In₂Se₃ | 10.1021/acs.jpclett.2c00618 | 1, 3, **5 (top result)**, 6, 7 |
| Giant piezoresistivity in a van der Waals material induced by intralayer atomic motions | 10.1038/s41467-023-37239-9 | 2, 3, 4 |

These are physical PDF pages, not journal pagination. Reference sections can match;
keyword occurrence is not a judgement that a paper supports a claim.

## Automated and desktop checks

23 offline tests passed: connection errors/pagination; metadata/library snapshot;
Windows paths; blank/corrupt/encrypted PDFs; page mapping; unchanged source files;
foreign keys; rollback/FTS replacement; incremental indexing; cancellation; multiple
PDFs; same-page AND search; typography; filters; literal input; Qt worker search;
copying and PDF preview.

Windows native Qt was also exercised using the real index: 8 page results; first
result page 5 of item `5VRINQRW`; exact source preview; citation clipboard included
page 5; correct PDF page rendered. Screenshots were inspected for layout and readable
English/Chinese text. Local screenshots live in `docs/screenshots/` (gitignored).

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.acceptance
.\.venv\Scripts\python.exe -m tests.desktop_acceptance
```

Real-data checks need the existing index and source PDFs. The desktop check briefly
opens native windows. Unit tests use isolated temporary fixtures and never access
Zotero's database.
