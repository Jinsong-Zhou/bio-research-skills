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
- **Quoting does not stop hyphen splitting.** `all:"cryo-EM"` tokenises to
  `cryo` and `EM`, so it matches "expectation-maximization" — a 30-day probe on
  that phrase returned an earthquake-forecasting paper as its top hit. Always
  pair keywords with a `cat:` filter, or accept cross-field noise.
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

### Page size is not what the cursor increments suggest

`/details/` returns **30 records per page**, while `/pubs/` returns 100. Nothing
in the response advertises a page size in advance; `messages[0].count` reports
what you got and `messages[0].total` the true window size.

Measured on `/details/biorxiv/2026-06-30/2026-07-30/{cursor}`:

```
cursor=0    count=30  total=6482
cursor=30   count=30  total=6482
cursor=100  count=30  total=6482
```

Assuming 100 is doubly wrong: a `len(batch) < 100` stop condition fires on the
first page, and advancing the cursor by 100 skips 70 records per step.
**Advance by `len(batch)` and stop against `total`.**

> This exact bug shipped in v0.1 of this skill. The paragraph warning about it
> was already here; the code used the wrong constant. Constructed tests passed
> because the fixture returned whatever page size the test author assumed.
> Verify page size against the live API, not against your own mock.

### Records come back oldest-first

Within a window the collection ascends by date, so page 1 is the **oldest**
slice — exactly backwards for a "what's new" query. Fetching the newest *N*
means seeking to `total - N` and reading to the end.

Measured on a 7-day window (`total=1437`):

| cursor | dates in page |
|---|---|
| 0 | 2026-07-23 |
| 718 | 2026-07-27 |
| 1407 | 2026-07-29 |

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

### Bound the window server-side — but know which date you bounded

`esearch` accepts `datetype=edat` with `mindate`/`maxdate` (`YYYY/MM/DD`).
Use it. Fetching a year of results to filter locally is both slow and rude.

**The Entrez date is when PubMed indexed the record, not when the paper was
published.** A seven-day `edat` window legitimately returns papers published
months earlier — a measured run had 41% of records outside the nominal window,
the oldest by 3.5 months, and two dated *after* it (ahead-of-print).

Neither date is wrong; reporting only one is. `published_date` carries the
publication date and `extra["entrez_date"]` the one that bounded the search, so
a digest can say which it means rather than listing an April paper under a
"this week" banner.

### Author names are `Surname Initials`

`<LastName>Falzone</LastName><Initials>ME</Initials>` renders as `Falzone ME` —
**surname first**, unlike arXiv's `Wei Zhang`. Splitting on whitespace and
taking the last token yields `ME`, the initials.

This is not cosmetic. Dedup buckets on `(title fingerprint, surname)`; keying
99.7% of PubMed records on their initials puts them in different buckets from
the matching preprint, and the tier never fires. That bug also shipped in v0.1.

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
