# Full-text sources

How `scripts/fetch.py` gets a PDF, and what each route does when it cannot.
Measured against the live APIs on **2026-07-30**.

Each claim below that a `live` test guards is marked 🔬, and the test name is
given. The unmarked ones are measurements nobody re-checks — treat them as
history, not as guarantees.

Read this before touching `scripts/fetch.py`.

---

## The routes, and the one that is missing

| Reference | Route | Full text? |
|---|---|---|
| arXiv id or URL | `https://arxiv.org/pdf/{id}` | Usually |
| `10.1101/…` or `10.64898/…` | bioRxiv/medRxiv details API → `.full.pdf` | Usually |
| Any other DOI, PMCID, PMID | Europe PMC | Only if open access |

"Usually", not "always": every one of these can answer HTTP 200 with an
interstitial, and `download_pdf` exists to catch it. A route that could not
fail would not need a guard.

**Paywalled journal articles are out of scope by design.** There is no legal
route to them without institutional credentials, and pretending otherwise
produces a note written from an abstract that reads like one written from the
paper. The script returns `fulltext: "abstract-only"` with the abstract and
says why.

---

## bioRxiv / medRxiv

### The DOI prefix is no longer `10.1101` alone

`10.64898` is now in use, and **the two prefixes do not map to two servers**.
Measured 2026-07-30 on `/details/medrxiv/2026-07-20/2026-07-22/0`:

| DOI | Server |
|---|---|
| `10.1101/2024.07.19.24310695` | medRxiv |
| `10.64898/2026.07.17.26358308` | medRxiv |
| `10.64898/2026.05.20.26353725` | medRxiv |

The same window on `/details/biorxiv/…` returned `10.64898` DOIs. So the prefix
narrows a DOI to "some preprint" and nothing more; the details endpoint decides
which server, which is why `resolve_preprint()` tries both.

The details endpoint accepts the new prefix:
`/details/biorxiv/10.64898/2026.07.16.739021` → `{"status": "ok"}`, one record.

🔬 `test_biorxiv_issues_no_prefix_we_do_not_route` catches a *new* prefix;
🔬 `test_the_newer_prefix_is_still_in_use` catches a *retired* one. Both
directions are needed and a subset assertion only covers the first — if
`10.64898` were withdrawn, the remaining set would still be a subset and the
test would pass while saying it checked.
🔬 `test_the_details_endpoint_answers_a_doi_under_the_newer_prefix`.

**Guard:** `PREPRINT_DOI_PREFIXES` holds both, and a DOI neither server claims
falls through to Europe PMC. A DOI Europe PMC has never heard of falls *back*
to the preprint servers — so a prefix nobody has documented yet still resolves,
one request later.

### `published` is the string `"NA"`, not an empty field

An unpublished preprint carries `"published": "NA"`. A truthiness check
therefore reports **every** preprint as already published, and the note warns
the reader about a journal version that does not exist.

**Guard:** `_published_doi()` treats `"NA"` and blank as "not published".
`literature-tracking` hit the same thing (`sources/biorxiv.py`, `_to_paper`).
🔬 `test_an_unpublished_preprint_still_carries_the_literal_string_NA` — asserted
against the live window rather than a constant the test supplies, since a unit
test of `_published_doi("NA")` re-checks our own code, not upstream's.

### One entry per version, oldest first

`collection` holds every version. `resolve_preprint` takes `[-1]`, and the
distinction is not cosmetic: `10.64898/2026.07.16.739021` v1 and v2 have
different titles, and the v1 URL still serves a valid PDF. Reading the first
entry would hand back a superseded manuscript with nothing to signal it.

### PDF URL

`https://www.{server}.org/content/{doi}v{version}.full.pdf`. Both the server
and the version come from the details response; neither is derivable from the
DOI. 🔬 `test_a_preprint_resolves_end_to_end_and_serves_a_whole_pdf` — the one
route whose download was never live-tested, and the one most likely to answer
with an interstitial.

bioRxiv sits behind a bot filter that was **not** triggered on 2026-07-30. It
is not guaranteed to stay that way, which is what `download_pdf`'s magic-byte
check is for — a challenge page arrives as HTTP 200. The live download test
above is what will notice when it changes.

### An unreachable server is not an answer

`api.biorxiv.org` returned an **HTTP 408** during review. `except FetchError:
continue` made that read exactly like "no such record", so an outage became the
sentence "this is not on bioRxiv or medRxiv" — a claim about the paper, made
when the truth was a claim about the network.

**Guard:** transport failures are collected and raised as
`SourceUnavailableError`, which the fallback still catches (Europe PMC may have
it) but now records as a warning, so a substitution is never silent. 408 is
also in `RETRY_STATUSES` now; it was not, so the first attempt raised.

---

## arXiv

`https://arxiv.org/pdf/{id}` needs no lookup. Version suffixes are stripped
during parsing, because the bare id always resolves to the latest version.
🔬 `test_arxiv_serves_a_pdf_at_the_constructed_url`.

`_http` paces arxiv.org at one request per second, which is what arXiv asks
for. Note this route returns **no abstract** — there is no lookup to get one
from — so a failed download here leaves nothing at all to read, and the report
says so rather than reporting `abstract-only` with a null abstract.

---

## Europe PMC

Europe PMC rather than NCBI's own PMC: it mirrors the same open-access subset,
indexes preprints as well, and does not gate automated requests the way
`ncbi.nlm.nih.gov` does.

### Deciding whether a PDF exists

`resultType=core` returns `isOpenAccess`, `inEPMC` and `fullTextUrlList`. The
script prefers a `fullTextUrl` entry with `documentStyle: "pdf"` and
`availabilityCode: "OA"`, and falls back to
`https://europepmc.org/articles/{pmcid}?pdf=render` when the record is in the
EPMC repository.

Measured:

| Reference | `isOpenAccess` | `inEPMC` | Result |
|---|---|---|---|
| `PMC13222519` | `Y` | `Y` | PDF via render endpoint 🔬 |
| `10.3389/fmolb.2026.1767821` | `Y` | `Y` | PDF |
| `10.1073/pnas.2513585123` | `N` | `N` | no PDF; abstract returned 🔬 |

The last row is the case worth designing for. It is not an error — the record
is complete, the metadata is good, and the abstract is real. What is missing is
the only thing an assessment can be built on.

For `PMC13222519` the OA-table URL and the constructed fallback are
byte-identical, so a test that only asserts `pdf_url` is truthy passes with
either branch deleted. Assert the *source* if you tighten this.

### The record that comes back may not be the one asked for

The query language is exact — `DOI:"…"`, `PMCID:…`, `EXT_ID:… AND SRC:MED` —
so a record with a different identifier is not a near miss. Every field taken
from it (title, authors, PDF URL) would describe another paper while the
report kept the requested reference at the top. `_identity_mismatch` compares
the record's own identifier and refuses, and `resolve()` deliberately does
**not** fall back on that: the other routes exist for "nobody has this", not
for "somebody answered with the wrong thing".

A record that simply omits the field is not a mismatch. Absence is not
disagreement, and refusing on it would break records EPMC carries no DOI for.

### Author strings differ by source

bioRxiv returns `"Surname, I.; Surname, J."` — semicolon-separated. Europe PMC
returns `authorString` as `"Han Y, Mei J, Li G."` — comma-separated, with no
comma inside a name. The script normalises the second into the first before
splitting. This works because EPMC uses surname-then-initial without a
separator; it would break on a source that writes `"Han, Y., Mei, J."`.

---

## What a download has to survive

Three checks, and each exists because one of the others cannot see the failure
it catches:

| Check | Catches | Why the others miss it |
|---|---|---|
| starts with `%PDF-` | an HTML paywall or bot check served as 200 | nothing else looks at the content |
| at least `MIN_PDF_BYTES` | a placeholder that starts right | magic bytes are only five |
| `%%EOF` near the end, and `Content-Length` agrees | a body cut mid-transfer | **truncation removes the end**, so a check on the first bytes always passes |

The third is the one worth dwelling on. A truncated PDF opens without
complaint; what is missing is the discussion, the limitations and the
supplementary material — which is most of what an assessment is built from.
Both live download tests exercise this against real files from arXiv and
bioRxiv, so the trailer window is validated against real producers rather than
assumed.

`download_pdf` returns the address the bytes actually came from, which is not
always the one requested. When they differ the report records
`pdf_source_url` and warns; provenance is the point, not blocking redirects,
which are ordinary at every one of these hosts.

---

## Local files

A path is taken at face value. If it does not start with `%PDF-` the report
carries a warning rather than an error — the user may have a reason, and
refusing outright would be the script overruling them about their own file.
Note the asymmetry with a download, and that it is deliberate: nobody chose
the paywall page, but the user chose this path.

### A remote URL can end in `.pdf`

`https://www.biorxiv.org/content/10.64898/…v2.full.pdf` has a `.pdf` suffix, so
a suffix check alone classifies it as a file on disk and then fails to find it.
`parse_identifier` checks for a URL scheme first. This was a real bug, caught
the first time the parser met a real preprint link.
