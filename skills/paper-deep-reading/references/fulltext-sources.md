# Full-text sources

How `scripts/fetch.py` gets a PDF, and what each route does when it cannot.
Measured against the live APIs on **2026-07-30**; the `live` tests in
`tests/test_fetch.py` re-check the load-bearing claims here.

Read this before touching `scripts/fetch.py`.

---

## The routes, and the one that is missing

| Reference | Route | Full text? |
|---|---|---|
| arXiv id or URL | `https://arxiv.org/pdf/{id}` | Always |
| `10.1101/…` or `10.64898/…` | bioRxiv/medRxiv details API → `.full.pdf` | Always |
| Any other DOI, PMCID, PMID | Europe PMC | Only if open access |

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

**Guard:** `PREPRINT_DOI_PREFIXES` holds both, and a DOI neither server claims
falls through to Europe PMC. A DOI Europe PMC has never heard of falls *back*
to the preprint servers — so a prefix nobody has documented yet still resolves,
one request later.

### `published` is the string `"NA"`, not an empty field

An unpublished preprint carries `"published": "NA"`. A truthiness check
therefore reports **every** preprint as already published, and the note warns
the reader about a journal version that does not exist.

**Guard:** `_published_doi()` treats `"NA"` and blank as "not published".
`literature-tracking` hit the same thing (`sources/biorxiv.py:174`).

### PDF URL

`https://www.{server}.org/content/{doi}v{version}.full.pdf`. Both the server
and the version come from the details response; neither is derivable from the
DOI. Measured: `10.64898/2026.07.16.739021` v1 → 1,835,774 bytes, valid PDF.

bioRxiv sits behind a bot filter that was **not** triggered on 2026-07-30. It
is not guaranteed to stay that way, which is what `download_pdf`'s magic-byte
check is for — a challenge page arrives as HTTP 200.

---

## arXiv

`https://arxiv.org/pdf/{id}` needs no lookup. Version suffixes are stripped
during parsing, because the bare id always resolves to the latest version.
Measured: `1706.03762` → 2,215,244 bytes.

`_http` paces arxiv.org at one request per second and export.arxiv.org at one
per three, which is what arXiv asks for.

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
| `PMC13222519` | `Y` | `Y` | PDF via render endpoint |
| `10.3389/fmolb.2026.1767821` | `Y` | `Y` | 3,474,220 bytes |
| `10.1073/pnas.2513585123` | `N` | `N` | no PDF; abstract returned |

The last row is the case worth designing for. It is not an error — the record
is complete, the metadata is good, and the abstract is real. What is missing is
the only thing an assessment can be built on.

### Author strings differ by source

bioRxiv returns `"Surname, I.; Surname, J."` — semicolon-separated. Europe PMC
returns `authorString` as `"Han Y, Mei J, Li G."` — comma-separated, with no
comma inside a name. The script normalises the second into the first before
splitting. This works because EPMC uses surname-then-initial without a
separator; it would break on a source that writes `"Han, Y., Mei, J."`.

---

## Local files

A path is taken at face value. If it does not start with `%PDF-` the report
carries a warning rather than an error — the user may have a reason, and
refusing outright would be the script overruling them about their own file.

### A remote URL can end in `.pdf`

`https://www.biorxiv.org/content/10.64898/…v2.full.pdf` has a `.pdf` suffix, so
a suffix check alone classifies it as a file on disk and then fails to find it.
`parse_identifier` checks for a URL scheme first. This was a real bug, caught
the first time the parser met a real preprint link.
