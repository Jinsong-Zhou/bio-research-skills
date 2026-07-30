# Source quirks

Failure modes measured against the live APIs on 2026-07-30. Every one of these
returns **HTTP 200**. Checking the status code tells you nothing; you have to
inspect the payload's shape.

Read this before touching `scripts/sources/`.

---

## arXiv

### Structured queries must not be wrapped in a field prefix

`cat:` and `submittedDate:` are top-level query operators. Nesting them inside
`all:` produces a query arXiv cannot parse — and it says so *inside a 200
response*, as a feed containing one fake entry titled `Error`.

Measured:

| `search_query` | `totalResults` | first entry title |
|---|---|---|
| `cat:q-bio.BM AND submittedDate:[202607200000 TO 202607300000]` | **12** | real paper |
| `all:cat:q-bio.BM AND submittedDate:[202607200000 TO 202607300000]` | **1** | **`Error`** |
| `all:protein folding` | 37800 | real paper |

A client that hardcodes `search_query=all:{query}` therefore cannot filter by
category or date at all, and fails silently when you try.

**Guard:** `arxiv._check_for_error_entry()` raises `ArxivQueryError` on a
single-entry feed titled `Error`, surfacing the reason from its `<summary>`.

### Other notes

- Timestamps in `submittedDate` are `YYYYMMDDHHMM`, UTC, inclusive on both ends.
- Multi-word terms need quoting (`all:"protein folding"`), else arXiv splits on
  whitespace and ORs the words.
- Entries repeat across page boundaries occasionally; dedupe by id while paging.
- arXiv asks for ≥3 seconds between requests. `_http._HOST_INTERVAL` enforces it.
- Paper ids carry a version suffix (`2501.01234v2`). Strip it so v1 and v3 of
  one preprint collapse; the full string stays in `extra.arxiv_id_versioned`.

---

## bioRxiv / medRxiv

### An unknown `category` is silently ignored

This is the nastiest one. The API does not reject an unrecognised subject
area — it drops the filter and returns everything in the date window.

Measured on `/details/biorxiv/2026-07-25/2026-07-29/0`:

| `category` | records | categories in response |
|---|---|---|
| `neuroscience` | 30 | `neuroscience` only ✅ |
| `protein_folding` | 30 | biochemistry, bioinformatics, biophysics, cancer biology, cell biology, genetics, immunology… ❌ |

The failure returns *real papers with real abstracts and real DOIs*. Nothing in
the response says the filter was dropped. Pass a keyword where a category
belongs and you get a plausible, entirely wrong answer.

**Guard:** `biorxiv.resolve_category()` validates against a whitelist before any
network call and raises `UnknownCategoryError` with close-match suggestions.

### There is no keyword search

`/details/{server}/{start}/{end}/{cursor}` accepts only a date range and one
optional category. This is an upstream limitation, not an oversight — filter by
subject area, then narrow the results downstream.

### Category casing is inconsistent across records

The same subject area comes back as `cancer biology` in one window and
`Cancer Biology` in another, while the URL parameter wants `cancer_biology`.
Match case- and separator-insensitively; never compare raw strings.

### Pagination needs the cursor, not just a bigger limit

Page size is fixed at 100. `messages[0].total` carries the true count. A loop
that breaks after the first page silently caps every query at 100 results.

### DOI prefixes change

Records seen in 2026 carry `10.64898/…`; older ones carry `10.1101/…`. Do not
detect preprints by DOI prefix.

### Preprint → journal links come free

Each record has a `published` field holding the journal DOI once one exists
(the literal string `NA` until then). This is dedup tier 1 and costs nothing.
`/pubs/{server}/{start}/{end}/{cursor}` gives the same mapping in bulk, keyed by
*published* date rather than preprint date.

### TLS resets under load

Sustained paging occasionally throws `EOF occurred in violation of protocol`.
Retry with backoff; `_http.fetch()` treats it as transient.

---

## PubMed (NCBI E-utilities)

### `<PubDate>` is frequently year-only

Parsing only `<PubDate>/<Year>` puts every record on 1 January, which makes any
"what appeared this week" query meaningless. It can also be a `<MedlineDate>`
range like `2024 Jul-Aug`.

**Guard:** `pubmed._best_date()` prefers, in order:

1. `<Article>/<ArticleDate>` — electronic publication, has a real day
2. `<PubmedData>/<History>/<PubMedPubDate PubStatus="entrez|pubmed|medline">`
3. `<Journal>/<JournalIssue>/<PubDate>` — last resort

### Bound the window server-side

`esearch` accepts `datetype=edat` with `mindate`/`maxdate` (`YYYY/MM/DD`).
Use it. Fetching a year of results to filter locally is both slow and rude.

### Rate limits

3 requests/second anonymously, 10 with `NCBI_API_KEY` set. Exceeding it earns a
block, not a 429. `_http._HOST_INTERVAL` paces to the anonymous limit.

### Other notes

- Structured abstracts arrive as several `<AbstractText>` elements with `Label`
  attributes; concatenating without the labels loses the structure.
- DOI lives in `<ArticleIdList>/<ArticleId IdType="doi">`, sometimes only in
  `<ELocationID EIdType="doi">`.
- Only records with a PMC id have a fetchable PDF.
- Author names come as `LastName` + `Initials`; consortia use `CollectiveName`.

---

## Crossref

Preprint links are recorded from both directions:

- on the preprint: `message.relation["is-preprint-of"]`
- on the journal article: `message.relation["has-preprint"]`

Both entries look like `{"id-type": "doi", "id": "10.…", "asserted-by": "…"}`.

Send a `User-Agent` with a `mailto:` — it buys the polite rate-limit pool.
Set `BIO_RESEARCH_CONTACT` to your address.

**Cost guard:** tier 2 issues one request per unmatched DOI, so `dedup.py`
skips it entirely when the result set has only one source (a counterpart can
only merge if the other record is already present), and caps the rest via
`max_crossref_lookups`.
