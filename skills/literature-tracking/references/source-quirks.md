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

> **Re-measured 2026-07-30: arXiv now uses status codes for this.** The same
> `all:`-wrapped query returns **HTTP 400**, and an unbalanced bracket returns
> **HTTP 500**; both land in `FetchError` rather than the guard. The guard is
> kept as belt-and-braces — it costs one `findall` per fetch, and this API has
> changed shape before — but it is no longer the thing catching this case.
> `tests/literature-tracking/test_sources.py::TestLiveBehaviour` asserts the *invariant* (a
> malformed structured query must be refused **somehow**) rather than the
> mechanism, so it will fail if arXiv ever starts answering one with plausible
> unfiltered results.

### An unknown field prefix returns 200 with zero results

Measured 2026-07-30 — the silent failure that replaced the one above:

| `search_query` | HTTP | `totalResults` | `Error` entry |
|---|---|---|---|
| `all:cat:q-bio.BM` | **400** | — | — |
| `cat:q-bio.BM AND submittedDate:[… ` (unbalanced) | **500** | — | — |
| `nosuchfield:xyz` | **200** | **0** | no |
| `cat:q-bio.BM` | 200 | 6819 | no |

A misspelled field is therefore indistinguishable from a quiet week. There is
no guard, because `build_query` only ever emits `cat:`, `all:` and
`submittedDate:` — but anything adding a new field must check that it returns
results, not merely that it returns 200.

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

> This exact bug was written, and survived a green test suite, while the
> paragraph warning about it was already in this file. The fixture returned
> whatever page size its author assumed, so the assertion and the mock agreed
> with each other and neither agreed with the API.
>
> Fixing the instance is not enough — a constant that happens to equal today's
> page size passes just as well. `tests/literature-tracking/test_sources.py` therefore serves
> **ragged** batches (30, 17, 30, 8, 25) and derives its assertion from the
> stub's own log of what it returned, so no literal can satisfy it. Verify page
> size against the live API, never against your own mock.

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
(the literal string `NA` until then). This is dedup rule 2 and costs nothing.
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
PubMed records on their initials puts them in different buckets from the
matching preprint, and the rule never fires. On a sampled run essentially every
PubMed record was affected.

The inverse matters too: when the surname comes out **empty** the key degenerates
to the title alone, which pools unrelated records sharing a boilerplate title.
`dedup.py` skips the bucket entirely in that case rather than merging on half a
key.

### Rate limits

3 requests/second anonymously, 10 with `NCBI_API_KEY` set. Exceeding it earns a
block, not a 429. `_http._HOST_INTERVAL` paces to the anonymous limit **whether
or not a key is set** — deliberately, since the key's value here is headroom
against a ban rather than throughput. Raise the interval too if you ever need
the extra speed; setting the key alone changes nothing about how fast this runs.

### Other notes

- Structured abstracts arrive as several `<AbstractText>` elements with `Label`
  attributes; concatenating without the labels loses the structure.
- DOI lives in `<ArticleIdList>/<ArticleId IdType="doi">`, sometimes only in
  `<ELocationID EIdType="doi">`.
- Only records with a PMC id have a fetchable PDF.
- Author names come as `LastName` + `Initials`; consortia use `CollectiveName`.

---

## Europe PMC

The keyword-searchable door onto bioRxiv and medRxiv, which have no keyword
search of their own. `SRC:"PPR"` scopes to preprints, `PUBLISHER:"bioRxiv"`
narrows the server, `FIRST_PDATE:[a TO b]` bounds the window, and records carry
the **same DOI** as the direct fetch, so dedup merges the two views for free.

### It is a complement, not a replacement

Measured against the bioRxiv API on 2026-07-30:

| Date | Europe PMC | bioRxiv | Coverage |
|---|---|---|---|
| 07-23 | 183 | 261 | 70% |
| 07-25 | 52 | 70 | 74% |
| 07-27 | 186 | 238 | 78% |
| 07-28 | 199 | 299 | 67% |
| 07-29 | 90 | 210 | **43%** |
| 07-30 | 0 | — | **0%** |

Indexing lags about a day and settles below 80% even for older days. The
newest records — the ones a tracking query exists to find — are the worst
covered, so relying on this channel alone would quietly drop most of the week's
new preprints.

### Malformed queries return plausible results, not errors

| Query | Result |
|---|---|
| `SRC:"PPR" AND (((unbalanced` | HTTP 200, **hitCount 2733** — the broken clause is dropped |
| `NOSUCHFIELD:"x"` | HTTP 200, **hitCount 0** — indistinguishable from an empty window |

A keyword containing a bracket, quote or colon is enough to trigger the first
case. Two guards: strip query syntax out of user keywords before embedding
them, and compare `request.queryString` in the response against what was sent —
Europe PMC echoes the query it actually ran.

### Other notes

- `resultType=core` is needed for abstracts; `lite` omits them.
- Paginate with `cursorMark`, starting at `*`. **`nextCursorMark` stops
  changing at the end** rather than disappearing, so a loop that only checks
  for its presence never terminates.
- Author names come as `Smith J, Okafor A.` — surname first, like PubMed.
- Coverage extends past bioRxiv/medRxiv to Research Square and others; filter
  by `PUBLISHER` unless that is wanted.

## Crossref

Preprint links are recorded from both directions:

- on the preprint: `message.relation["is-preprint-of"]`
- on the journal article: `message.relation["has-preprint"]`

Both entries look like `{"id-type": "doi", "id": "10.…", "asserted-by": "…"}`.

Send a `User-Agent` with a `mailto:` — it buys the polite rate-limit pool.
Set `BIO_RESEARCH_CONTACT` to your address.

### It costs ~1.4s a lookup, so spend the budget deliberately

A measured run with the budget raised to 250 issued **216 lookups for zero
merges** (the shipped default is 60, so reproducing that figure needs
`--max-crossref-lookups`). The reason is structural: a lookup only merges when
the counterpart is already in the result set, and a preprint posted this week
cannot have a journal version yet — its journal article would have to predate it.

The budget is charged on the **attempt**, not the success. Crossref 404s every
arXiv DOI (those are DataCite, not Crossref) and 503s under load; charging only
for successes made `--max-crossref-lookups` no ceiling at all, and an outage
would walk every candidate at three retries apiece while `crossref_skipped`
stayed at zero.

`dedup.py` therefore runs Crossref **last**, after the free rules have taken
what they can, and orders the remaining candidates by expected payoff:

1. journal-side records — their preprint can be of any age
2. revised preprints (v2+) — the original may already be published
3. first-version preprints — essentially never pays off

It also skips the rule entirely for single-source result sets, and caps the
rest via `max_crossref_lookups`. This orders rather than excludes: a large
enough budget still reaches every record, so no merge is lost, only deferred.
