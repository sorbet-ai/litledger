# 04 — Scholarly Data Sources & Format Standards

*Survey date: 2026-10-06. Scope: the data-source, identifier, bibliographic-format, full-text and local-search layer of a reusable literature-review / bibliography MCP server aimed at AI coding agents working on ML/CS research (arXiv, NeurIPS/ICML/ICLR/ACL), with some physics/neuro/bio.*

**How this was verified.** Policies were checked against official docs and blogs where reachable. Several endpoints were also **probed live from the user's Windows 11 machine on 2026-10-06**; those results are marked **[live]**. Claims that come only from third-party write-ups, or from older docs I could not re-fetch, are marked **[unverified]**.

---

## 0. TL;DR — what changed recently and matters most

| Change | Date | Impact on this tool |
|---|---|---|
| **OpenAlex: usage-based pricing and API keys.** A free key gives about $1/day of credit; keyless gives $0.10/day. Lookups by ID/DOI are free; list calls cost $0.0001, search $0.001, PDF/TEI download $0.01. The old `mailto`/100k-per-day "polite pool" model is gone. | 2026-02-24 | OpenAlex is still excellent for free **single-record lookups**. Search and list calls must be budgeted. Add an `OPENALEX_API_KEY` setting. |
| **OpenAlex "Walden" rewrite became the default.** It added 190M works from DataCite and repositories. | 2025-11-01/03 | More junk and duplicate records (see the quirks in §1.1). |
| **Crossref REST rate limits tightened.** With `mailto`: 10 rps for single DOIs and 3 rps for list/query, 3 concurrent. Without it: 5 rps and 1 rps, 1 concurrent. | 2025-12-01 | **[live]** Response headers confirm `x-rate-limit-limit: 3`, `x-api-pool: polite-array` for queries. |
| **Papers with Code shut down.** paperswithcode.com now redirects to Hugging Face Trending Papers. | 2025-07-24/25 | Use the HF Papers API instead (it has no SOTA/leaderboard data). |
| **arXiv OAI-PMH moved** to `https://oaipmh.arxiv.org/oai`. `export.arxiv.org/oai2` now returns a 301. | 2025 | **[live]** Update the harvester URL. |
| **arXiv's "rate limit policy" of 2026-10-01 is about *submissions*** (2 per month per submitter, 3 active), **not the API**. The API terms are still 1 request per 3 s on a single connection. A 429 storm in Sept 2026 was resolved on 2026-09-15. | 2026-10-01 | Keep the 3 s throttle and add backoff. |
| **OpenReview API now challenges anonymous clients.** Requests get `403 ChallengeRequiredError`. openreview-py 2.7.1 adds token auth (tokens last at most 1 week) and MFA. This followed the 2025-11-27 security incident that leaked reviewer identities. | 2025-11 → 2026 | **[live]** You now need an OpenReview account and session token. Guest scraping no longer works. |
| **dblp.org (including the search API and `.bib` export) sits behind the Anubis proof-of-work bot wall** when called from this machine. The SPARQL endpoint `sparql.dblp.org` still works. | observed 2026-10-06 | **[live]** The DBLP BibTeX path needs a fallback: SPARQL, a local dblp.xml dump, or S2's `externalIds.DBLP` plus your own BibTeX generator. |
| **Semantic Scholar keyless access is effectively unusable.** All tries returned 429 from the shared pool. A key gives 1 rps to start. | observed 2026-10-06 | **[live]** Treat an S2 key as a hard requirement. |
| **PMC legacy FTP and the OA Web Service API are retired on or after 2026-08-24.** They are replaced by per-article files in the new PMC Cloud Service on AWS (`pmc-oa-opendata`). | 2026 | Use Europe PMC REST or the new PMC AWS layout. |
| **Google sued SerpApi** (2025-12-19). Google Scholar has no API. | 2025-12 | Do not build on scraped Google Scholar results. Offer a link-out only. |
| **Elsevier and Springer Nature had abstracts of closed articles removed** from OpenAlex. | from 2024-11 | Abstract coverage for closed publisher content is poor everywhere except S2, which still withholds some. |

---

## 1. Source-by-source survey

### Master comparison table

| Source | Best for (ML lens) | IDs accepted | Abstract | Refs / cites | Full text / PDF | Embeddings | Auth | Rate limit | Bulk | Data licence |
|---|---|---|---|---|---|---|---|---|---|---|
| **Semantic Scholar (S2)** | Discovery, ML citation graph, ID cross-walk, TLDRs | S2 sha, `CorpusId:`, `DOI:`, `ARXIV:`, `MAG:`, `ACL:`, `PMID:`, `PMCID:`, `URL:` | Yes; some elided "for legal reasons" | Yes, with **contexts, intents, isInfluential** | `openAccessPdf` URL; snippet search over full text | **SPECTER2 (768-d) per paper** | Key (free, by application) | 1 rps with key; keyless is a shared pool (429s observed) | Datasets API (key): papers, abstracts, citations, SPECTER v1/v2, tldrs, s2orc, s2orc_v2 | API licence forbids redistribution; datasets under ODC-BY / CC BY-NC per dataset |
| **OpenAlex** | Free DOI lookups, broad coverage, institutions and topics, OA locations | `W…`, DOI, PMID, PMCID, MAG (no native arXiv field) | Inverted index; closed Elsevier/Springer removed | `referenced_works`, `cites:` filter | `best_oa_location`, plus paid PDF/**TEI** downloads (about 60M OA works) | Semantic search (beta) | Free key (keyless allowed but tiny) | 100 rps; daily USD budget | Free snapshot (S3) | **CC0** |
| **Crossref** | Metadata of record for DOI'd journal and ACL papers | DOI | Only if the publisher deposits one (Elsevier, IEEE, ACS do not) | Deposited references (open since 2022) | Links only | — | None; `mailto` for polite pool | 10 / 3 rps (polite) | Annual public data file (free); Plus snapshots (paid) | Metadata effectively open; abstracts may be copyrighted |
| **arXiv** | Preprint metadata of record, versions, source/HTML | arXiv ID (versioned), DataCite DOI | Yes | No | **PDF, LaTeX source, native HTML** | — | None | 1 request per 3 s, single connection | OAI-PMH, S3 requester-pays, Kaggle | Metadata CC0; papers per-paper licence |
| **DataCite** | Resolving `10.48550/arXiv.*` DOIs, Zenodo, datasets | DOI | Sometimes | relatedIdentifiers | — | — | None | 500 per 5 min unidentified, 1000 identified, 3000 authed | Data files | CC0 |
| **DBLP** | **Clean CS venue metadata and BibTeX**; arXiv↔venue mapping (CoRR records) | DBLP key (`conf/nips/VaswaniSPUJGKP17`), DOI | **No** | No | Links (`ee`) | — | None (but bot wall) | Undocumented; 429 + Retry-After | Monthly dblp.xml, RDF dumps, SPARQL | CC0 |
| **OpenReview** | ICLR/NeurIPS/TMLR reviews, decisions, revisions | Forum/note id, venue id | Yes | No | PDF and revisions | — | **Account and token now required** | Undocumented | — | Venue-specific (often CC BY 4.0) **[unverified]** |
| **ACL Anthology** | *CL papers, BibTeX of record | Anthology ID (`2023.acl-long.1`, `P19-1423`), DOI 10.18653 | Yes (bib with abstracts) | No | PDF (CC BY 4.0) | — | None | Static site | Full `.bib` dumps, git repo | CC BY 4.0 |
| **PubMed E-utilities** | Biomedical metadata | PMID, PMCID | Yes | elink | — | — | Optional key | 3 rps, or 10 with key | Baseline FTP | NLM terms |
| **Europe PMC** | Bio/neuro full text, preprints | PMID, PMCID, DOI, PPR ids | Yes | Yes (citations/refs) | **JATS fullTextXML** for the OA subset | — | None | Undocumented | FTP | Mixed; OA subset licences |
| **bioRxiv / medRxiv API** | Preprint ↔ published mapping in bio | DOI 10.1101 | Yes | No | PDF/JATS links | — | None | Undocumented | S3 requester-pays **[unverified]** | Per-paper |
| **CORE** | Repository OA full text (long tail) | CORE id, DOI | Yes | No | **Full text** | — | Free key | Token-based **[unverified]** | Dataset dumps | Mixed |
| **Unpaywall** | DOI → OA PDF location | DOI | No | No | `best_oa_location.url_for_pdf` | — | `email=` | 100k/day | Data feed (paid) / via OpenAlex | CC0 |
| **OpenCitations** | Open CC0 DOI-to-DOI citation graph | DOI, PMID, ISSN, ISBN, OMID | No | **Yes (counts and lists)** | — | — | Optional token | 180 req/min/IP | Dumps | **CC0** |
| **HF Papers** | ML "what's hot", code/model/dataset links | arXiv ID | Yes | No | — | — | None for GET | Undocumented | — | HF ToS |
| **Google Scholar** | Coverage and citation counts | — | — | — | — | — | **No API** | ToS forbids automation | — | — |
| **scite** | Supporting/contrasting citation classes | DOI | — | Smart Citations | — | — | Paid (Pro or Enterprise) | — | — | Commercial |

### 1.1 OpenAlex

- **Docs:** https://help.openalex.org/ (developers.openalex.org redirects here). Pricing: https://help.openalex.org/access/example-costs. Announcement: https://blog.openalex.org/category/feature (2026-02-24). Walden: https://blog.openalex.org/walden-rewrite-launch/.
- **Coverage:** 300M+ works in the core corpus. An expansion corpus (`corpus=` param, `is_xpac` flag) adds about 60% more. Since Walden it also includes DataCite and repository content. Coverage is broad across all disciplines, which makes it good for physics, neuro and bio.
- **Identifiers:** OpenAlex `W…` id, DOI (`/works/doi:10.x/...` or `/works/https://doi.org/...`), PMID, PMCID, MAG. **There is no arXiv-ID field.** arXiv papers are reachable through the DataCite DOI `10.48550/arxiv.<id>` *only if that DOI was chosen as the work's canonical DOI* (see quirks).
- **Fields:** `abstract_inverted_index` (rebuild the text yourself), `authorships` (with ORCID and ROR), `primary_location`, `locations[]` (each with `landing_page_url`, `pdf_url`, `version`, `source`), `best_oa_location`, `referenced_works`, `related_works`, `cited_by_count`, `counts_by_year`, `topics`, `keywords`, `fwci`, `is_retracted`, `has_content` / `content_urls` (downloadable PDF/TEI).
- **Auth and limits:** a free API key (`api_key=` param, from openalex.org/settings/api) raises the daily budget 10×, from $0.10 to $1.00. Per-call costs:

  | Call type | Cost per call |
  |---|---|
  | Singleton lookup by ID or DOI | **free and uncapped** |
  | List/filter | $0.0001 (about 10k calls/day on the free key) |
  | Search (full-text or semantic) | $0.001 (about 1k/day) |
  | PDF/TEI download | $0.01 (about 100/day) |

  The hard limit is 100 rps. **[live]** Keyless response headers show `X-RateLimit-Limit-USD: 0.1` and `X-RateLimit-Cost-USD: 0.001` for a `title.search` filter, and every response carries `meta.cost_usd`.
- **Bulk:** a free snapshot (S3, CC0) that is "the preferred method" for large-scale use.
- **Licence:** CC0, so the data is fully cacheable and redistributable.
- **Quirks (important for ML):**
  - **[live] Bad canonical-record merges.** "Attention Is All You Need" (`W2626778328`) has the canonical DOI `10.65215/2q58a426`, a 2025 repost on a third-party preprint server. Its `publication_year` is 2025, and `best_oa_location` points at that third-party site. The arXiv DOI is just one of 11 locations, so `GET /works/doi:10.48550/arxiv.1706.03762` returns **404**. Crossref search shows the same junk DOIs (`10.65215/…`, type `posted-content`) ranked above anything legitimate.
  - **[live] Preprint and published versions are split.** "Making Pre-trained LMs Better Few-shot Learners" exists as both an arXiv record (`W3126960149`, 131 cites) and an ACL record (`W3173777717`, 1,279 cites). Citation counts for ML preprints are badly fragmented. GPT-3 (`W3030163527`) shows 2,963 cites, against tens of thousands on Google Scholar and S2.
  - **[live] Reference lists for arXiv preprints are often empty.** Mistral-7B (`W4387561528`) has `referenced_works_count = 0`.
  - Closed Elsevier and Springer Nature abstracts were removed (from 2024-11). Coverage of 2022–24 Elsevier abstracts fell from about 82% to 22.5% (Aaron Tay: https://aarontay.substack.com/p/the-petrol-tank-for-ai-discovery).
- **Verdict:** use it as a free DOI lookup and OA-location source, a cross-discipline fallback, and an institution/funder/topic enricher. **Do not use it as the resolver of record for arXiv-first ML papers.**

### 1.2 Semantic Scholar (Academic Graph, Recommendations, Datasets)

- **Docs:** https://api.semanticscholar.org/api-docs/ · Overview: https://www.semanticscholar.org/product/api · Licence: https://www.semanticscholar.org/product/api/license
- **Coverage:** about 214M papers, 2.49B citations, 79M authors (product page). Strongest coverage for CS/ML and biomed. Venue and arXiv linkage is good.
- **Identifiers accepted:** `<40-hex sha>`, `CorpusId:`, `DOI:`, `ARXIV:` (versionless), `MAG:`, `ACL:`, `PMID:`, `PMCID:`, `URL:` (arxiv.org, aclweb/aclanthology, acm, biorxiv, semanticscholar). `externalIds` returns **DOI, ArXiv, DBLP, ACL, PubMed, PubMedCentral, MAG, CorpusId**. That makes S2 the best single **ID cross-walk** for ML: one call maps arXiv ↔ DBLP key ↔ DOI ↔ ACL id.
- **Graph API (`/graph/v1`):**
  - `/paper/{id}` and `POST /paper/batch` (≤500 ids, ≤10 MB response).
  - `/paper/search` (relevance; ≤1,000 results total; 100 per page).
  - `/paper/search/bulk` (boolean query syntax; token pagination; 1,000 per page; up to millions of results; sortable).
  - `/paper/search/match` (best title match; use it for fuzzy resolution).
  - `/paper/autocomplete`.
  - `/snippet/search` (about 500-word passages from title, abstract and body, filterable by year, venue and paperIds).
  - `/paper/{id}/citations` and `/references` with **`contexts`** (citing sentences), **`intents`** (background / methodology / result), and **`isInfluential`**.
  - `/author/*`.
- **Paper fields:** `title, abstract, year, publicationDate, venue, publicationVenue, journal, authors, externalIds, citationCount, influentialCitationCount, referenceCount, openAccessPdf{url,status,license}, tldr{text}, embedding.specter_v2 (768-d), s2FieldsOfStudy, publicationTypes, citationStyles{bibtex}`.
- **Recommendations API (`/recommendations/v1`):**
  - `GET /papers/forpaper/{id}?from=recent|all-cs`.
  - `POST /papers` with positive and negative seed lists. This is a good fit for an agent loop like "more like these, not those".
  - **[live]** `forpaper/ARXIV:1706.03762` returned `[]`. The default `recent` pool only recommends recent papers, so send old seeds with `from=all-cs`.
- **Datasets API.** **[live]** Latest release is `2026-09-29`. Datasets:

  | Dataset | Size |
  |---|---|
  | `papers` | 200M |
  | `abstracts` | 100M |
  | `authors` | — |
  | `citations` | — |
  | `embeddings-specter_v1`, `embeddings-specter_v2` | 120M each, about 28 GB × 30 files |
  | `paper-ids` | — |
  | `publication-venues` | — |
  | `s2orc`, `s2orc_v2` | parsed OA full text |
  | `tldrs` | 58M |

  Listing is open, but **downloads need a key** (`401 A valid API key is required`). An incremental-diffs endpoint exists.
- **Auth and limits:** request a key via the form. The starting rate is **1 rps on all endpoints**, and higher rates are possible after review. Keyless traffic shares a pool documented at "1000 rps across all unauthenticated users". **[live]** Every keyless try on 2026-10-06 returned 429. One archived source says keys are no longer issued to gmail addresses **[unverified]**, so use an institutional or company email.
- **Licence:** the API licence forbids "repackage, sell, … distribute, or sublicense". Attribution to "Semantic Scholar" is required. Datasets carry per-dataset licences (ODC-BY, CC BY-NC). **Local caching for the user's own research is fine. Do not redistribute an S2-derived corpus** — for example, do not ship a pre-built DB.
- **Quirks:**
  - The docs note abstracts can be withheld for legal reasons, so the field may be `null` even when the paper has an abstract.
  - `openAccessPdf.url` can be empty, with a status or disclaimer, or can point to the arXiv PDF.
  - The `venue` string is not normalised; use `publicationVenue`.
  - `citationStyles.bibtex` is low quality (it often emits `@Article{…, journal={ArXiv}}`). Never emit it as final output.
  - Each S2 paper is one merged record covering preprint and published versions, which is *good* for dedup.
- **Verdict:** the **primary discovery, citation-graph and ID-cross-walk provider** for ML. It needs a key.

### 1.3 Crossref

- **Docs:** https://api.crossref.org · Rate-limit change: https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/
- **Coverage:** about 180M DOIs (journals, proceedings, books, posted-content). **arXiv is not in Crossref** (its DOIs are DataCite). **NeurIPS, ICML/PMLR and ICLR papers have no DOIs at all.** ACL (10.18653), IEEE, ACM, Springer LNCS, AAAI (10.1609), JMLR (no DOI), TMLR (no DOI).
- **Fields:** title, authors (with ORCID if deposited), container-title, issued dates, type, `reference` (if deposited), `is-referenced-by-count`, `relation` (`has-preprint`, `is-preprint-of` — sparse), license, link (full-text links for TDM), funder. Abstracts are JATS and present only if deposited. Elsevier, ACS and IEEE deposit none. **[live]** The ACL DOI `10.18653/v1/N19-1423` has no abstract and an empty `relation`.
- **Auth and limits:** add `mailto=` (or a `User-Agent` containing an email) to join the polite pool. Since 2025-12-01:
  - Polite: 10 rps / 3 concurrent for single DOIs; 3 rps / 3 concurrent for list/query.
  - Public: 5 rps / 1 concurrent and 1 rps / 1 concurrent.
  - Metadata Plus (paid) is unchanged.
- **BibTeX:** DOI content negotiation (`curl -LH "Accept: application/x-bibtex" https://doi.org/<doi>`) returns BibTeX or CSL-JSON (`application/vnd.citationstyles.csl+json`). The output is decent for journals, but titles have no brace protection and month/pages vary.
- **Bulk:** annual Public Data File (free, torrent); monthly Plus snapshots (paid).
- **Verdict:** the metadata of record **for DOI'd venues** (ACL, journals, ACM/IEEE). It is **useless for NeurIPS/ICML/ICLR**. The current prototype's Crossref-only approach is why arXiv/ML lookups are poor.

### 1.4 arXiv (API, OAI-PMH, listings/RSS, bulk, HTML)

- **Docs:** API user manual https://info.arxiv.org/help/api/ · Terms of use https://info.arxiv.org/help/api/tou.html · Bulk https://info.arxiv.org/help/bulk_data.html · API group https://groups.google.com/a/arxiv.org/g/api · Blog https://blog.arxiv.org/
- **Scale:** over 3M articles (blog, 2026-07-09). Record submissions: 40,363 in Sept 2026. arXiv spun out of Cornell as an independent nonprofit on 2026-07-01.
- **Identifiers:**
  - New-style `YYMM.NNNNN` (5 digits since 2015-01; 4 digits for 0704–1412).
  - Old-style `archive/YYMMNNN` (e.g. `hep-th/9901001`).
  - Optional version `vN`.
  - DataCite DOI `10.48550/arXiv.<id>`, which is versionless and exists for all papers.
- **Query API** (`https://export.arxiv.org/api/query`, Atom):
  - Parameters: `search_query` (fields `ti:`, `au:`, `abs:`, `cat:`, `all:`, boolean, date range via `submittedDate:[… TO …]`), `id_list`, `start`, `max_results` (≤2000 per page, about 30k total).
  - Returns: versioned `id`, `published` (v1 date), `updated` (latest version), title, summary, authors, `arxiv:primary_category`, categories, `arxiv:comment` (often "Accepted at NeurIPS 2023"), `arxiv:journal_ref`, `arxiv:doi` (author-supplied published DOI, sparse), and PDF link.
  - **[live]** The API returns versioned ids (`1706.03762v7`).
- **Terms of use:** "no more than one request every three seconds, … a single connection at a time", applied across all your machines. Metadata is CC0. Storing papers for personal or research use is allowed. **Serving PDFs or source from your own servers is not allowed** without permission, so link to arXiv.
- **Status 2025–26:**
  - The legacy `/find` search was turned off in July 2025.
  - The API saw intermittent 429/406/503 errors during 2026, including a 429 spike in Sept 2026 that was resolved on 09-15.
  - CORS headers changed in March 2026.
  - The **2026-10-01 "rate limit policy"** caps **submissions** (2 per month, 3 active per submitter) — https://aiweekly.co/alerts/fair-moderation-equitable-access-and-ai-arxivs-updated-rate-limit-policy. It is not an API change.
- **OAI-PMH:** **[live]** `https://oaipmh.arxiv.org/oai` (the old `export.arxiv.org/oai2` 301-redirects here). Formats are `oai_dc`, `arXiv`, and `arXivRaw` (includes full version history with dates). Sets are categories, e.g. `cs`. It is updated daily and is the preferred way to mirror metadata.
- **Listings/RSS:** **[live]** `https://rss.arxiv.org/rss/cs.LG` (also `/atom/`, and combined forms like `cs.LG+stat.ML`). These give daily new and cross-listed items and suit a "watch categories" feature. HTML listings are at `arxiv.org/list/cs.LG/new`.
- **Bulk:** S3 requester-pays bucket `s3://arxiv/` (`pdf/`, `src/` tars) — https://info.arxiv.org/help/bulk_data_s3.html. The Kaggle `Cornell-University/arxiv` metadata JSON is refreshed periodically. A May 2026 list thread says bulk source in requester-pays storage was out of date.
- **Full-text formats:**
  - **Native HTML:** `arxiv.org/html/<id>vN`, for TeX submissions since Dec 2023. **[live]** Generated by "LaTeXML oxide 0.7.6", the in-progress Rust port. About 75% convert error-free (target 90%).
  - **ar5iv:** `ar5iv.labs.arxiv.org/html/<id>` for older papers.
  - **LaTeX source:** `arxiv.org/e-print/<id>` (tar.gz or a single gzip'd .tex).
- **BibTeX:** **[live]** `arxiv.org/bibtex/<id>` returns
  `@misc{vaswani2023attentionneed, … year={2023}, eprint={1706.03762}, archivePrefix={arXiv}, primaryClass={cs.CL}, url=…}`.
  **Quirk:** the year is the *latest-version* year (2023), not v1 (2017), and the key follows suit. Generate your own entry from the v1 date instead.
- **Verdict:** the **metadata of record for preprints**, plus the best OA full-text source (HTML first, then source, then PDF). Throttle strictly, and cache by versioned id.

### 1.5 DataCite

- **Docs:** https://support.datacite.org/docs/api · Rate limits: https://support.datacite.org/docs/rate-limit
- **Role:** registrar for **arXiv DOIs (10.48550)**, Zenodo (10.5281), figshare, datasets and software.
- **[live]** `GET https://api.datacite.org/dois/10.48550/arxiv.1706.03762` returns JSON:API with `identifiers: [{identifierType: arXiv, identifier: 1706.03762}]`, creators, title and dates. No auth is needed.
- **Limits** (from Q3 2025, per IP):

  | Client type | Limit per 5 minutes |
  |---|---|
  | Unidentified | 500 |
  | Identified (email in UA or `mailto`) | 1,000 |
  | Authenticated | 3,000 |

- **Licence:** CC0.
- **Note:** OpenAlex now ingests DataCite, so Zenodo reposts of arXiv papers appear as extra locations. **[live]** LoRA has two Zenodo DOIs attached.

### 1.6 DBLP

- **Endpoints:**
  - Search: `https://dblp.org/search/publ/api?q=…&format=json&h=…` (also `/search/author/api`, `/search/venue/api`).
  - Record export: `https://dblp.org/rec/<key>.bib?param=0|1|2` (condensed / standard / with crossref), plus `.xml`, `.ris`, `.rdf`, `.nt`.
  - SPARQL: https://sparql.dblp.org (QLever).
  - Monthly XML dumps, now served from `drops.dagstuhl.de` (`dblp.org/xml/release/` redirects there).
- **Coverage:** about 7M+ CS publications, with excellent venue normalisation and author disambiguation. arXiv papers are indexed as CoRR (`journals/corr/abs-1706-03762`), so **DBLP links an arXiv id to the published venue record**, e.g. `conf/nips/VaswaniSPUJGKP17`.
- **Fields:** title, authors (with DBLP PIDs/ORCID), venue, year, pages, DOI/ee links. **No abstracts and no citations.**
- **BibTeX quality:** the best freely available for CS conferences. Booktitles are consistent ("Advances in Neural Information Processing Systems 30: …"), editors and pages are included, there are stable keys like `DBLP:conf/nips/VaswaniSPUJGKP17`, and `biburl`/`bibsource` fields are added (strip them). Titles are not brace-protected.
- **Limits:** no published numeric rate limit. Excess traffic gets `429` with `Retry-After`.
- **[live] Anubis bot wall:** on 2026-10-06 every `dblp.org`, `dblp.uni-trier.de` and `dblp.dagstuhl.de` HTTP request from this machine (search API and `.bib` export alike, with a custom UA) returned the Anubis "Making sure you're not a bot!" proof-of-work HTML page with status 200. **Detect this explicitly** (`text/html` where JSON was expected). `sparql.dblp.org` answered normally (about 3 s for a title `CONTAINS` query).
- **Licence:** CC0.
- **Verdict:** the **BibTeX/venue authority for CS conferences**. Implement three paths: (a) the HTTP API with Anubis detection, (b) SPARQL, and (c) an optional local dblp.xml index (about 1 GB gz). Use (c) for the offline-capable path and for "rebiber"-style upgrading.

### 1.7 OpenReview

- **Docs:** https://docs.openreview.net/ · Python client: https://openreview-py.readthedocs.io/ (openreview-py 2.7.1, 2026-10-05).
- **Base URLs:** API v2 `https://api2.openreview.net` (current venues). API v1 `https://api.openreview.net` (legacy; some pre-2024 venues).
- **Coverage:** ICLR (all years), NeurIPS (from 2021 reviews; main track public for accepted papers), TMLR, CoRL, COLM, many workshops, and ACL Rolling Review (non-public reviews).
- **Identifiers:**
  - Forum id, e.g. `rJXMpikCZ`. It equals the submission note id.
  - Venue id, e.g. `ICLR.cc/2025/Conference`.
  - Invitations: `…/-/Submission`, `…/Submission{N}/-/Official_Review`, `Meta_Review`, `Decision`, `Official_Comment`.
- **Data:** title, abstract, keywords, TL;DR, `venue` / `venueid` (accept, reject, withdrawn — very useful as an "accepted at" signal), reviews and ratings, rebuttals, PDF (`/pdf?id=`), supplementary material, and **revision history** (`/notes/edits?note.id=`).
- **Auth:** **[live]** anonymous GETs now return `403 {"name":"ChallengeRequiredError","message":"Challenge verification required …","details":{"challengeUrl":"https://openreview.net/challenge?..."}}`. The 2.7.1 client accepts `token=` (expiry configurable up to 1 week) and implements MFA (email OTP, passkey). So **the user needs an OpenReview account**. The tool should store a session token (via OS keyring) and refresh it.
- **Context:** API security incident on 2025-11-27 (profile-search endpoint exposed reviewer and author identities; about 45% of ICLR 2026 submissions affected) — https://openreview.net/forum/user%7Cstatement_regarding_api_security_incident, https://blog.iclr.cc/iclr-2026-response-to-security-incident/.
- **Limits and ToS:** no published rate limit. Be gentle (≤1 rps) and cache forever, since reviews are immutable once released.
- **Verdict:** optional, credential-gated provider for **reviews, decisions and camera-ready status**. It is also the canonical URL for ICLR BibTeX (`url={https://openreview.net/forum?id=…}`).

### 1.8 ACL Anthology

- **Docs:** https://aclanthology.org/faq/api/ · Python library: https://acl-anthology.readthedocs.io (`acl-anthology` 1.3.3, **Python ≥3.11**, needs git).
- **Coverage:** all ACL/EMNLP/NAACL/EACL/TACL/CL/workshops (about 100k+ papers).
- **IDs:** new-style `2023.acl-long.123`; old-style `P19-1423`. DOI `10.18653/v1/<id>` for most.
- **Access:** per-paper `https://aclanthology.org/<id>.bib` and `.pdf`. Full dumps `anthology.bib.gz` and `anthology+abstracts.bib.gz` (best for offline matching). There is no live query API; the library clones the metadata repo.
- **Licence:** CC BY 4.0 (papers and metadata).
- **BibTeX:** gold standard for *CL. Titles are brace-protected and `booktitle`/`publisher`/`pages`/`doi` are correct.
- **Windows note:** the user's machine runs Python 3.10.5 (pyenv-win). `acl-anthology` requires >3.11, so either parse the `.bib` dumps directly or require Python 3.11+.

### 1.9 PubMed E-utilities, Europe PMC, PMC OA

- **E-utilities** (https://www.ncbi.nlm.nih.gov/books/NBK25497/):
  - Endpoints: `esearch`, `efetch` (XML with abstract and MeSH), `esummary`, `elink` (cited-by within PMC), `idconv` (PMID↔PMCID↔DOI).
  - Limits: 3 rps without a key, **10 rps with a free NCBI key** (`api_key=`). Send `tool=` and `email=`.
  - Coverage: 37M+ biomedical citations. Neuroscience coverage is strong.
- **Europe PMC REST** (https://europepmc.org/RestfulWebService):
  - **[live]** `…/rest/search?query=DOI:10.1038/nature14539&format=json` works with no key.
  - Search syntax supports `DOI:`, `EXT_ID:`, `SRC:PPR` (preprints, including bioRxiv/medRxiv), `OPEN_ACCESS:y`.
  - `/{source}/{id}/fullTextXML` gives JATS full text for the OA subset. `/citations` and `/references` work for MED records. There is also a text-mined Annotations API.
  - No documented rate limit. Courtesy throttle about 10 rps.
- **PMC OA bulk:**
  - **Legacy FTP, the OA Web Service API and legacy AWS prefixes become unavailable on or after 2026-08-24.** Legacy files were moved under `deprecated/` on 2026-04-13.
  - The new PMC Cloud Service on AWS (`pmc-oa-opendata`) serves **individual** XML/TXT/PDF files, not tarballs — https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/ and https://www.nlm.nih.gov/pubs/techbull/jf26/jf26_Changes_to_PMC_ArtDsDistribServs_2026.html.
- **Verdict:** a "bio/neuro adapter". Use PubMed for metadata and MeSH, and Europe PMC for OA full text (JATS XML, so no PDF parsing is needed).

### 1.10 bioRxiv / medRxiv API

- **Endpoint:** https://api.biorxiv.org/.
  - `details/{biorxiv|medrxiv}/{DOI | YYYY-MM-DD/YYYY-MM-DD | N | Nd}/{cursor}/json` returns 30 per page.
  - `pubs/{server}/{interval}/{cursor}` returns 100 per page and gives **preprint DOI → published DOI**. This is the bio analogue of arXiv→venue.
  - Also `publisher/`, `funder/` (ROR), `sum/`, `usage/`.
- **[live]** `details/biorxiv/10.1101/2020.03.22.002386` works. No auth.
- Records are versioned. No documented rate limit, so be polite.

### 1.11 CORE

- **API v3:** https://api.core.ac.uk/docs/v3. The docs page returned 403 to the fetcher, so the limits here are **[unverified]**.
- **Access:** a free key for non-commercial use is needed. Token-bucket limits apply; registered or supporting members get more.
- **Endpoints:** `search/works`, `outputs/{id}` (includes `fullText`), `discover` (DOI → OA full-text link), and `outputs/{id}/download` (PDF).
- **Coverage:** aggregates over 10k repositories, with metadata for 300M+ outputs and full text for tens of millions. It is strongest on repository green OA (theses, institutional copies) that arXiv and PMC miss. Dataset dumps are available on request.
- **Verdict:** last-resort OA full-text fallback.

### 1.12 Unpaywall

- **API:** `https://api.unpaywall.org/v2/<doi>?email=<you>`. **[live]** It works and returns `best_oa_location` (`url_for_pdf`, `host_type`, `license`, `version`) and `oa_locations[]`. Some legacy fields now read `"deprecated"` because the codebase was merged into OpenAlex/Walden.
- **Limit:** 100k calls/day.
- **Licence:** CC0.
- **Overlap:** the same data is available as OpenAlex `best_oa_location` / `locations`, which costs nothing as a singleton lookup. **Use one or the other, not both.** Unpaywall is simpler (an email, no key).

### 1.13 OpenCitations (Index v2, which includes COCI)

- **Docs:** https://api.opencitations.net/index/v2 (v2.2.0, 2025-04-15).
- **Operations:** `/citations/{id}`, `/references/{id}`, `/citation-count/{id}`, `/reference-count/{id}`, `/venue-citation-count/{issn}`, `/citation/{oci}`.
- **IDs:** `doi:`, `pmid:`, `omid:`, `issn:`, `isbn:`.
- **Sources:** Crossref (the old COCI), DataCite, PubMed, OpenAIRE and JaLC, merged under OMIDs.
- **Limit:** 180 req/min/IP. An optional token goes in the `authorization` header.
- **Licence:** **CC0**, with dumps.
- **[live]** `citation-count/doi:10.1038/nature14539` returns `73495`.
- **Limitation for ML:** DOI-only. NeurIPS/ICML/ICLR papers have no DOI and arXiv references are sparse, so it is a weak graph for ML. Its strengths are licence cleanliness and journal papers.

### 1.14 Google Scholar

- **No official API.** The ToS (current version effective 2026-07-30) prohibits automated access that violates machine-readable instructions. Google has blocked bots more aggressively since Jan 2025 ("SearchGuard").
- **SerpApi** (which offers a Google Scholar engine) has been **sued by Google (2025-12-19)** and by Reddit (2025-10-22) — https://proxyway.com/news/google-sues-serpapi.
- The `scholarly` Python package breaks frequently, and CAPTCHAs/IP bans are routine.
- **Recommendation:** do not integrate. At most, generate a `https://scholar.google.com/scholar?q=<title>` link for the human, and accept user-pasted Scholar BibTeX as input.

### 1.15 Hugging Face Papers (and the Papers with Code aftermath)

- **Papers with Code:** sunset by Meta on 2025-07-24/25 without notice. At the end it held 9,327 benchmarks and 79,817 papers with leaderboards. The domain redirects to **HF Trending Papers** (https://huggingface.co/papers/trending) — https://hyper.ai/en/news/42900, https://www.codesota.com/papers-with-code/shutdown. HF does not replicate the task→benchmark→SOTA hierarchy. Third-party revivals such as Codesota exist; treat them as unvetted.
- **HF Papers API** (all **[live]** without a token):
  - `GET /api/papers/{arxivId}` returns `title, summary, authors, publishedAt, upvotes, ai_summary, ai_keywords, linkedModels / linkedDatasets / linkedSpaces counts`. `githubRepo` / `projectPage` appear when set.
  - `GET /api/papers/search?q=…&limit≤120`.
  - `GET /api/daily_papers?date=|week=|month=&sort=trending|publishedAt&limit≤100`.
  - `GET /api/arxiv/{arxivId}/repos` lists models, datasets and spaces whose cards cite the paper. This is a good proxy for "has code or artifacts".
- **Licence:** HF ToS. No documented rate limit; cache.
- **Verdict:** a **"trending/social signal" and artifact linker** for ML. It is not a metadata source.

### 1.16 Connected Papers, ResearchRabbit, Litmaps, scite

- **Connected Papers:** API in early access (email hello@connectedpapers.com), official client `connectedpapers-py` — https://pypi.org/project/connectedpapers-py/. It is built on S2 data, so its co-citation/bibliographic-coupling graph can be approximated locally from S2 references and citations.
- **ResearchRabbit:** acquired by Litmaps (May 2025), relaunched 2025-10-20. **No public API**.
- **Litmaps:** no general public API (sources conflict) **[unverified]**.
- **scite:** Smart Citations (supporting / contrasting / mentioning, over 1.2B statements). API access and an MCP server (scite.ai/mcp) come only with paid Pro (about $50/month) or Enterprise, per third-party pricing pages **[unverified]**. For an OSS tool, use S2 `intents` and `contexts` instead.

### 1.17 Briefly: others worth an adapter later

| Source | Use | Access |
|---|---|---|
| **NASA ADS** | Physics/astro metadata, refs, BibTeX (bibcodes) | Token. About 5k req/day per endpoint **[unverified]** |
| **INSPIRE-HEP** | HEP physics, excellent BibTeX and citations, arXiv-native | Open REST. About 15 req per 5 s **[unverified]** |
| **CiNii Research** | Japanese literature | OpenSearch API, appid registration |
| **Lens.org** | Patents plus scholarly | Token on request |
| **Dimensions** | Commercial index | Free for non-commercial research on application |
| **Zenodo** | Software/data DOIs | Search API rate limits tightened 2025-11 (https://blog.zenodo.org/2025/11/25/2025-11-14-search-api-updates/) |
| **BASE (Bielefeld)** | Repository search | IP whitelisting |
| **Microsoft Academic** | — | **Retired 2021-12-31.** MAG ids survive in OpenAlex `ids.mag` and S2 `externalIds.MAG`, so keep them as alias keys. |

---

## 2. Identifier resolution & deduplication

### 2.1 Identifier zoo (normalise on ingest)

| ID | Canonical form | Regex (case-insensitive) | Notes |
|---|---|---|---|
| arXiv new | `2106.09685` (+ `vN` stored separately) | `^(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?$` | Also match URLs `arxiv.org/(abs\|pdf\|html)/…`. Strip `.pdf`. |
| arXiv old | `hep-th/9901001` | `^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$` | e.g. `math.GT/0309136` |
| arXiv DOI | `10.48550/arxiv.2106.09685` | `^10\.48550/arxiv\.(.+)$` | Versionless. Lowercase the DOI (DOIs are case-insensitive). |
| DOI | `10.18653/v1/n19-1423` | `^10\.\d{4,9}/\S+$` | Strip `https://doi.org/`, `doi:`. Lowercase for keys and keep the original for display. |
| PMID / PMCID | `26017442` / `PMC1234567` | `^\d{1,8}$` / `^PMC\d+$` | |
| S2 | `CorpusId:123` or 40-hex sha | `^[0-9a-f]{40}$` | Prefer CorpusId (integer, stable). |
| OpenAlex | `W2626778328` | `^[Ww]\d+$` | Records **can merge or split** over time; re-validate. |
| DBLP key | `conf/nips/VaswaniSPUJGKP17` | `^(conf\|journals\|books\|phd\|series)/[^/]+/.+$` | `journals/corr/abs-YYMM-NNNNN` = arXiv |
| ACL Anthology | `2023.acl-long.123`, `P19-1423` | `^\d{4}\.[a-z\-]+\.\d+$\|^[A-Z]\d{2}-\d{4}$` | DOI = `10.18653/v1/<id>` (usually) |
| OpenReview | forum id `rJXMpikCZ` | `^[A-Za-z0-9_\-]{8,}$` (needs context) | Always store with the venue id. |
| bioRxiv | `10.1101/2020.03.22.002386` | DOI | Versions via `vN` in URL |

### 2.2 Data model: Work → Versions (FRBR-lite)

Store one **Work** (the citable intellectual unit) with many **Expressions/Versions**:

- `arxiv:2106.09685v1 … v2` (preprint versions, with dates from OAI `arXivRaw`)
- `doi:10.48550/arxiv.2106.09685` (DataCite, versionless)
- `venue:` published version: ICLR 2022 (OpenReview forum id, DBLP `conf/iclr/HuSWALWWC22`), *or* a journal DOI
- stray reposts (Zenodo DOIs, `10.65215/…`, Chinese mirror DOIs) — record them as `alias_low_trust` and never make them canonical

Each Work carries an `ids{}` multimap, a **preferred citation target** (the published version if one exists, otherwise the latest arXiv), and per-field provenance (`source`, `fetched_at`).

### 2.3 Resolver-of-record strategy

**Recommended "resolver of record": Semantic Scholar for the ID cross-walk, then the authority per field.** For ML, S2 is the only source whose single record links arXiv, DBLP, DOI, ACL, PMID and CorpusId and merges preprint with published version. Field authority:

| Field | Authority (in order) |
|---|---|
| Title, authors, abstract (preprint) | arXiv → S2 → OpenAlex |
| Published venue, year, pages, booktitle (CS conf) | DBLP → OpenReview (`venue`) → S2 `publicationVenue` |
| Published venue (journal / ACL) | Crossref (DOI) → ACL Anthology → DBLP |
| Abstract (closed journal) | S2 → PubMed/Europe PMC → Crossref (if deposited) → OpenAlex |
| Citation count | S2 (merged versions); show OpenAlex/OpenCitations as secondary |
| References / citations | S2 → OpenAlex → OpenCitations |
| OA PDF / full text | arXiv HTML/src/PDF → Europe PMC JATS → Unpaywall/OpenAlex `best_oa_location` → S2 `openAccessPdf` → CORE |
| Acceptance status / reviews | OpenReview (auth) |
| Code / artifacts | HF `/api/arxiv/{id}/repos`, HF paper `githubRepo` |

### 2.4 Resolution algorithm (input: any string a user or agent pastes)

1. **Parse** for an explicit ID (the regexes above, URLs, BibTeX `eprint` / `doi` fields).
2. **arXiv id →** arXiv API (`id_list`, throttled). Then S2 `ARXIV:<id>` with fields `externalIds,publicationVenue,venue,year,citationCount,openAccessPdf,tldr`. If `externalIds.DBLP` exists, get the DBLP record (API, else SPARQL, else local dump). If `externalIds.DOI` is not 10.48550, Crossref gives the published metadata. Finally, check `arxiv:journal_ref` / `arxiv:doi` / `arxiv:comment` for "Accepted at …".
3. **DOI →** a `10.48550` DOI is treated as arXiv (step 2). Otherwise call Crossref (polite), plus S2 `DOI:` for the cross-walk, plus OpenAlex singleton (free) for OA locations. If the DOI is DataCite (Crossref 404), call DataCite.
4. **Title (+ optional authors/year) →**
   - (a) S2 `/paper/search/match?query=<title>`.
   - (b) If there is no match or the score is low, try arXiv `ti:"…"`, then DBLP SPARQL or `search/publ`, then OpenAlex `title.search` (costs $0.0001), then Crossref `query.bibliographic` (watch for junk DOIs).
   - (c) Accept a candidate only if **all** of the following hold:
     - normalised-title similarity ≥ 0.93 (`rapidfuzz.fuzz.token_set_ratio` on casefolded, NFKD-stripped, LaTeX-stripped, punctuation-free titles);
     - first-author surname matches (after unidecode; allow particles such as "van", "de");
     - |year − candidate_year| ≤ 1, widening to 2 for preprint → published.
   - (d) If several candidates pass, prefer the one with an arXiv id or DBLP key and reject `posted-content` DOIs from unknown prefixes.
5. **Dedup on ingest:** union-find over a shared-ID graph, with candidate pairs blocked on (normalised title prefix, first-author surname). Merge when any strong ID matches (arXiv base id, DOI, DBLP key, S2 CorpusId, PMID) **or** the fuzzy rule above passes. Never merge on fuzzy title alone when both records carry *different* strong IDs of the same type, e.g. two different arXiv ids (as with "Attention Is All You Need In Speech Separation").
6. **Versioning:** store `arxiv_base_id` and `latest_version`. Re-check the latest version weekly for papers in active projects (OAI `arXivRaw` gives version dates cheaply). The published-version upgrade (§3.3) runs on the same schedule.
7. **Trust flags:** if OpenAlex's canonical DOI prefix is not in an allow-list of known publishers or registries, or `publication_year` is far from arXiv v1, flag the record and fall back to arXiv/S2 values. This is the `10.65215` case seen live.

---

## 3. Bibliographic formats

### 3.1 Format comparison

| Format | Strengths | Weaknesses | Use in this tool |
|---|---|---|---|
| **BibTeX** (`.bib`) | Universal in ML (NeurIPS/ICML/ICLR/ACL templates use natbib + BibTeX) | No native `doi` / `url` / `eprint` semantics (style-dependent); ASCII/LaTeX escaping; case protection with `{}` | **Primary export.** |
| **BibLaTeX** (`.bib`, biber) | Native `date`, `doi`, `eprint` + `eprinttype=arxiv` + `eprintclass`, `@online`, Unicode, `ids`/`related` | Not accepted by most ML conference templates | Optional export dialect |
| **CSL-JSON** | Canonical machine format used by Zotero, pandoc, citeproc; clean typed fields (`issued.date-parts`, `container-title`, `DOI`, `URL`, `number` for arXiv) | No citekey standard (`id` is free-form) | **Internal canonical record and interchange** |
| **RIS** | EndNote/Mendeley/Zotero import | Lossy, tag soup | Export only (cheap to generate) |

Recommendation: store **CSL-JSON plus extra fields** (arXiv id, DBLP key, OpenReview id, venue short name) as the internal record. Generate BibTeX and BibLaTeX from your own templates, not by passing through upstream BibTeX. Parse incoming `.bib` with **bibtexparser 2.1.0** (MIT, 2026-10-02, Python ≥3.10). pybtex 0.26.1 is an alternative.

### 3.2 Citekeys

- **Better BibTeX default:** `auth.lower + shorttitle(3,3) + year`, with a mandatory a/b/c postfix on clash. That is the first-author surname lowercased, the first three significant title words capitalised, and the year, e.g. roughly `vaswaniAttentionAllYou2017` — https://retorque.re/zotero-better-bibtex/citing/.
- **Google-Scholar style `vaswani2017attention`** (surname + year + first significant word) is what most ML authors type by hand and expect from agents. Make this the default, with the BBT formula as an option.
- **Rules:**
  - Use the **v1 / original year**, not the arXiv latest-version year (the `arxiv.org/bibtex` year=2023 quirk).
  - Keys are ASCII-only after unidecode, stable once assigned (persist them in the DB), and keep the clash postfix deterministic by insertion order.
  - Keep a `key_aliases` table so a key the user or LaTeX file already uses (e.g. `DBLP:conf/nips/...`) still resolves.

### 3.3 Producing clean BibTeX for ML papers

Templates per type:

- **NeurIPS:**
  ```bibtex
  @inproceedings{key,
    title     = {{Attention} Is All You Need},
    author    = {...},
    booktitle = {Advances in Neural Information Processing Systems},
    volume    = {30},
    year      = {2017},
    url       = {https://proceedings.neurips.cc/...}
  }
  ```
  Editors and pages come from DBLP. There is no DOI.
- **ICML:** `@inproceedings{…, booktitle={Proceedings of the 40th International Conference on Machine Learning}, series={Proceedings of Machine Learning Research}, volume={202}, pages={…}, publisher={PMLR}}`.
- **ICLR:** `@inproceedings{…, booktitle={International Conference on Learning Representations}, year={2022}, url={https://openreview.net/forum?id=…}}`.
- **ACL family:** take the Anthology `.bib` verbatim (it is already correct and includes the DOI).
- **arXiv-only:**
  ```bibtex
  @misc{key,
    title         = {...},
    author        = {...},
    year          = {<v1 year>},
    eprint        = {2106.09685},
    archivePrefix = {arXiv},
    primaryClass  = {cs.CL},
    url           = {https://arxiv.org/abs/2106.09685}
  }
  ```
  Alternatively `@article{…, journal={arXiv preprint arXiv:2106.09685}}` (Google Scholar style, which works with every natbib style). Make this configurable.

Hygiene:

- **Case protection:** wrap acronyms and proper nouns in `{}` (`{BERT}`, `{T}ransformers`). Detect them with a capital-letter heuristic plus a small dictionary.
- Escape `&`, `%`, `_`, `#`. Convert Unicode to LaTeX only for BibTeX (not BibLaTeX/biber).
- Strip `timestamp`, `biburl` and `bibsource` from DBLP output.
- Use full venue names, optionally with an abbreviation mode, as rebiber does.

**Upgrade pass ("rebiber-style"):** for every arXiv-only entry, look for a published version and swap it in, keeping the arXiv id in a note or `eprint` field. Check, in order:

1. S2 `publicationVenue` + `externalIds.DBLP`
2. DBLP CoRR→conf link (same authors and title, a `conf/` record)
3. OpenReview `venueid` (accepted)
4. ACL Anthology title index
5. Crossref `relation.is-preprint-of` (rare)

**rebiber** (https://github.com/yuchenlin/rebiber) does this offline from bundled DBLP/ACL bib snapshots. Its PyPI release is stale (1.1.3, 2021), with a GitHub update in July 2025 adding auto-download scripts. Re-implement the idea on your own DBLP and ACL indices rather than depending on it. **bibtexautocomplete** (1.4.3, MIT) fills missing fields from Crossref, DBLP, S2, OpenAlex, Unpaywall and others. It is useful as a reference implementation, but it will hit the same DBLP bot wall.

### 3.4 Citation-style rendering

| Option | Language | Notes |
|---|---|---|
| **citeproc-py** 0.11.1 (2026-09-01, BSD-2) | Python | Pure Python CSL processor. Takes CSL-JSON or BibTeX input. Ships with a few styles; fetch others from the CSL styles repo (APA, IEEE, ACM, Nature, …). Adequate for previews and Markdown reports. Some CSL 1.0.2 features are missing. |
| **pandoc --citeproc** | Haskell binary | Most complete CSL implementation. Single static `.exe`, so it works fine on Windows. Use it for document-level rendering when the user already has pandoc. |
| **citation-js** | Node | Excellent format conversion (BibTeX ↔ CSL ↔ RIS) and rendering. Only worth it if the MCP server gains a Node sidecar; skip for a Python tool. |
| **pybtex** 0.26.1 | Python | Renders with BibTeX `.bst` styles (plain, unsrt, alpha). Good for "what will natbib produce". |

Recommendation: render with **citeproc-py** and bundle 4–5 CSL styles (APA 7, IEEE, ACM-SIGCONF, Nature, Chicago author-date). Optionally use pandoc if it is detected.

---

## 4. PDF & full-text extraction (offline-capable, Windows, Python)

### 4.1 Prefer *not* to parse PDFs

For arXiv papers, the order of preference is:

1. **arXiv native HTML** (`/html/<id>vN`). It keeps section structure, MathML/LaTeX alt text and the bibliography (`ltx_bibitem`). It covers TeX submissions since Dec 2023, about 75% clean.
2. **ar5iv** for older papers.
3. **LaTeX source** (`/e-print/<id>`). Parse the `.tex` and `.bbl` files: the `.bbl` gives the exact resolved bibliography, which beats any PDF reference parser.
4. **PDF** last.

For bio, use **Europe PMC JATS XML**. For OA works generally, OpenAlex sells **GROBID-style TEI XML** at $0.01 per paper (100/day on the free key). That is a way to get structured references without running GROBID.

### 4.2 Tool comparison

| Tool (version, date) | Licence | Quality on ML papers | Speed (CPU) | Install weight | GPU | Windows | Notes |
|---|---|---|---|---|---|---|---|
| **pypdf** 6.19.0 (2026-09) | BSD-3 | Plain text; poor reading order on 2-column layouts; math garbled | Medium (pure Python) | Tiny | No | Yes | Current. Fine as an always-available fallback. |
| **pypdfium2** 5.14.0 (2026-10) | Apache-2/BSD-3 | Better text and order than pypdf (Chrome's PDFium); per-char boxes | **Very fast** | Small (about 5 MB wheel) | No | **Yes, wheels** | **Best permissive drop-in upgrade.** Build columns from char boxes or use `get_text_bounded`. |
| **pdfplumber** 0.11.10 | MIT | Good word/line boxes and tables on simple layouts | Slow | Small (pdfminer.six) | No | Yes | Use for tables only. |
| **PyMuPDF / pymupdf4llm** 1.28.2 (2026-08) | **AGPL-3.0** or commercial | Very good Markdown; now bundles **PyMuPDF Layout** (GNN, CPU, since Nov 2025) for headers, columns and tables | Very fast | About 20–40 MB | No | Yes | Best quality per CPU-second, **but AGPL**. Acceptable only if the MCP server is itself AGPL-compatible OSS or the user installs it as an optional extra. |
| **GROBID** (0.8.x; client `grobid-client-python` 0.2.0) | Apache-2 | **Best reference parsing** (about .87–.90 F1 on PMC/bioRxiv), TEI with header, sections and figures; weak on math | About 10 PDF/s on 16 cores (CRF image) | Docker image: CRF-only about 500 MB, full DL about 8 GB | Optional (full image) | **No native Windows** ("cannot ensure support for Windows"). Needs Docker Desktop/WSL2; no GPU in Docker on Windows. | Run as an optional local service, or use OpenAlex TEI instead. |
| **Docling** 2.134.0 (2026-10-06) | **MIT** | Very good layout (Heron), TableFormer tables, formula detection; optional GraniteDocling 258M VLM; exports Markdown and lossless JSON | About 1–3 s/page on CPU (standard pipeline) | Heavy: torch + models (about 1–2 GB) | Optional | **Yes** (x86_64/arm64) | **Best permissive "quality" option.** Make it an optional extra. |
| **Marker** 2.0.0 (2026-07) | Code **Apache-2.0** (since v2); **weights modified OpenRAIL-M** (free under $5M revenue/funding, non-competing) | Excellent Markdown with LaTeX equations and tables; `--use_llm` mode | Fast mode about 24 pages/s on CPU without OCR; GPU balanced mode about 2.9 pages/s | Heavy (torch, surya); v2 GPU path needs vLLM/Docker; llama.cpp on CPU | Optional / recommended | Partial (torch on Windows fine; vLLM path is Linux) | The weights licence makes it awkward as a default dependency. |
| **MinerU** 4.0.10 (2026-09) | **MinerU Open Source License** (Apache-2 plus extra conditions) | SOTA-class layout, formulas and tables; VLM backends | Pipeline runs on CPU (ONNX "basic" tier needs 2 GB RAM) | 0.8–2 GB models (+5 GB full) | Optional | **Yes**, explicitly supported | Python 3.10–3.14. Check the licence conditions before bundling. |
| **Nougat** 0.1.17 (**2023-10**) | MIT (code); CC-BY-NC weights | Was good for arXiv-style math | Slow; GPU needed in practice | Heavy | Yes | Poor | **Unmaintained. Do not adopt.** |
| arXiv HTML / LaTeX source | — | Best possible (author source) | Network-bound | None (BeautifulSoup/lxml; `pylatexenc`) | No | Yes | **Default for arXiv.** |

### 4.3 Minimal-dependency recommendation

**Core (always installed, pure-wheel, Windows-safe, permissive):** `pypdfium2` (replacing or alongside pypdf), `lxml` or `selectolax` for arXiv HTML and JATS, `pylatexenc` for `.tex`/`.bbl` cleanup.

Pipeline: arXiv HTML → LaTeX `.bbl` for references → Europe PMC JATS → pypdfium2 text with a simple two-column heuristic.

**Optional extras:**
- `[layout]` → Docling (MIT; CPU OK; tables, formulas and Markdown).
- `[grobid]` → `grobid-client-python` against a user-run Docker GROBID, only for reference extraction from non-arXiv PDFs. OpenAlex TEI downloads cover many of the same works.
- `[mupdf]` → pymupdf4llm, with an explicit AGPL notice.

Skip Marker and MinerU as defaults because of their licence conditions. Nougat is dead.

---

## 5. Local semantic search

### 5.1 Storage

- **SQLite FTS5** (current) handles BM25 over title, abstract, notes and full-text chunks. Use the `unicode61 remove_diacritics 2` tokenizer, or `trigram` for substring and fuzzy title matching.
- **sqlite-vec** 0.1.9 (2026-03-31; MIT/Apache-2; **Windows x86-64 wheel available**; pre-v1, "expect breaking changes"):
  - Stores float32 / int8 / binary vectors in `vec0` virtual tables.
  - Brute-force KNN is fast enough for ≤1M vectors. Int8 or binary quantisation cuts the cost further.
  - The repo contains IVF/DiskANN work, but treat ANN as experimental.
- **Windows caveat:** `sqlite3.Connection.enable_load_extension` must be available. It is in python.org and pyenv-win builds (the user's 3.10.5), but **not** in some embedded or Store builds. Check at startup and fall back to numpy brute-force over a BLOB column. Also avoid keeping the DB in OneDrive-synced folders, because of file locking and WAL issues.

### 5.2 Embedding model choice

| Option | Pros | Cons |
|---|---|---|
| **SPECTER2** (`allenai/specter2_base` + adapters: `proximity` for paper↔paper, `adhoc_query` for short queries) | Trained on 6M citation triplets across 23 fields; designed for paper similarity. **Precomputed vectors are available free from S2** (`embedding.specter_v2` field, or the datasets dump), so no local compute is needed for indexing. | 512-token, title+abstract only. Needs the `adapters` package + torch (~1 GB) to embed *queries* locally. Weaker than modern general models for natural-language question queries. A dense-only SPECTER2 is not always better than BM25 (hybrid wins — https://themoonlight.io/review/sparse-meets-dense-a-hybrid-approach-to-enhance-scientific-document-retrieval). |
| **General small embedders via fastembed** 0.8.1 (ONNX, CPU, no torch): `BAAI/bge-small-en-v1.5` (384-d), `nomic-embed-text-v1.5`, `snowflake-arctic-embed-s` | About 100–200 MB, fast on CPU, works on Windows, good for question-style queries and full-text chunks | Not citation-aware |
| **Large general embedders** (`Qwen3-Embedding-0.6B/4B/8B` — 8B was #1 on MTEB multilingual, June 2025; e5/bge-large) | Best quality | Torch + GPU or slow CPU; heavy for an MCP tool |

**Recommendation:**
- **Hybrid retrieval:** FTS5 BM25 (top 100) ∪ sqlite-vec KNN (top 100), fused with **Reciprocal Rank Fusion** (k=60). Optionally re-rank with a small cross-encoder later.
- **Default embedder:** fastembed `bge-small-en-v1.5` for chunks and queries (one model, symmetric, CPU, about 130 MB download, cached under `%LOCALAPPDATA%`).
- **Paper-level "more like this":** store the free **S2 SPECTER2 vectors** when S2 returns them (`fields=embedding.specter_v2`). Use them only for paper↔paper similarity, so no local SPECTER2 model is needed.
- Persist `model_name` and `dim` with each vector table so the model can be swapped.

---

## 6. Recommended provider stack for an ML-research agent tool

| Layer | Primary | Fallbacks (in order) | Notes |
|---|---|---|---|
| **Discovery: keyword/semantic search** | **S2** `/paper/search/bulk` + `/paper/search` + `/snippet/search` (key) | arXiv API `search_query` (throttled 3 s); OpenAlex `search` (budgeted at $0.001/call); DBLP SPARQL for venue-scoped queries ("ICLR 2025 papers with 'diffusion' in title") | Merge with RRF and dedup through §2. |
| **Discovery: "more like these"** | S2 Recommendations `POST /papers` (positive/negative) | Local SPECTER2 vectors (from S2) KNN; co-citation over the cached S2 graph | |
| **Discovery: new and trending** | arXiv RSS `rss.arxiv.org/rss/cs.LG+cs.CL+…` + OAI-PMH incremental | HF `/api/daily_papers?sort=trending`; HF `/api/papers/search` | HF adds the social signal and code/model links. |
| **Metadata of record** | Composite: **arXiv** (preprint fields) + **DBLP** (CS venue) / **Crossref** (DOI venues) / **ACL Anthology** (*CL), with **S2 as the ID cross-walk** | OpenAlex singleton (free); DataCite for 10.48550/Zenodo; PubMed/Europe PMC (bio); bioRxiv `pubs` | Keep provenance per field and run the trust flags in §2.4 step 7. |
| **Citation graph** | **S2** citations/references with contexts, intents, isInfluential | OpenAlex `referenced_works` / `filter=cites:`; OpenCitations (CC0, DOI-only); Europe PMC citations (bio) | Cache edges locally; S2 citation counts are the most complete for ML. |
| **Review / acceptance status** | OpenReview API v2 (**user account + token**) | arXiv `comment` / `journal_ref`; S2 `publicationVenue`; DBLP | Optional; credential-gated. |
| **OA full text** | arXiv HTML → arXiv LaTeX src (`.bbl`) → arXiv PDF | Europe PMC JATS (bio) → Unpaywall / OpenAlex `best_oa_location` PDF → S2 `openAccessPdf` → CORE → OpenAlex TEI download ($0.01) | Respect arXiv terms: cache locally, never re-serve. |
| **BibTeX** | Own generator from the canonical CSL-JSON record; venue fields from **DBLP** (CS) or the **ACL Anthology `.bib`** (*CL); Crossref content negotiation for journal DOIs | DBLP SPARQL or local dblp.xml when dblp.org is challenged; S2 `citationStyles` only as a last-resort seed | Always run the rebiber-style upgrade pass and brace protection. |
| **Code / artifacts** | HF `/api/arxiv/{id}/repos`, HF paper `githubRepo` | arXiv comment/abstract GitHub URL regex | Papers with Code is gone. |
| **Bio / neuro adapter** | PubMed E-utilities (key) + Europe PMC | bioRxiv/medRxiv API; new PMC AWS | |
| **Physics adapter (later)** | INSPIRE-HEP / NASA ADS | arXiv | |
| **Not recommended** | Google Scholar/SerpApi (ToS and litigation), scite (paid), Connected Papers (early-access API), ResearchRabbit/Litmaps (no API), Nougat (dead), Microsoft Academic (retired) | | |

### Configuration and keys the tool should support

`S2_API_KEY` (strongly recommended — effectively required), `OPENALEX_API_KEY` (free; needed for any list or search), `CONTACT_EMAIL` (Crossref `mailto`, Unpaywall `email`, DataCite UA, NCBI `email`), `NCBI_API_KEY` (optional), `OPENREVIEW_USERNAME`/token (optional, stored via `keyring`), `CORE_API_KEY` (optional), `OPENCITATIONS_TOKEN` (optional).

Never hard-code the user's email. Read it from config.

### Rate-limit governor (one per host, shared across tools)

| Host | Setting |
|---|---|
| `export.arxiv.org` | 1 request / 3 s, concurrency 1 |
| `api.semanticscholar.org` | 1 rps |
| `api.crossref.org` | 3 rps list, 10 rps single, concurrency ≤3 |
| `api.openalex.org` | Track `X-RateLimit-Remaining-USD`; prefer singleton calls |
| `api.datacite.org` | ≤3 rps |
| `api.opencitations.net` | 3 rps |
| NCBI | 3 rps (10 with key) |
| `dblp.org` | ≤1 rps; detect Anubis HTML, then switch to SPARQL or dump |
| OpenReview | ≤1 rps |

All hosts: exponential backoff on 429/503 that honours `Retry-After`, and a persistent HTTP cache (`hishel` or `requests-cache` on SQLite) keyed by URL with long TTLs for immutable records (versioned arXiv, OpenReview reviews).

### Windows 11 / Python venv notes

- **Python version:** the user's current interpreter is 3.10.5 (pyenv-win). `acl-anthology` needs >3.11 and `docling` needs ≥3.10. **Target 3.11+ (ideally 3.12)** for the server.
- **GROBID:** no native Windows. It needs Docker Desktop (WSL2 backend), CPU only. Keep it optional.
- **Torch extras (Docling, Marker, MinerU, SPECTER2):**
  - Large downloads.
  - CUDA wheels must match the driver.
  - The HF hub cache on Windows warns about symlinks unless Developer Mode is on (set `HF_HUB_DISABLE_SYMLINKS_WARNING=1`).
  - Long paths may need `LongPathsEnabled`.
  - Keep the core install torch-free (fastembed/ONNX).
- **sqlite-vec:** works through the Windows wheel, but verify that `enable_load_extension` exists in the interpreter.
- **Encoding:** set `PYTHONUTF8=1` or open files with `encoding="utf-8"` explicitly. BibTeX with Unicode author names breaks under cp1252 defaults.
- **Line endings:** write `.bib` with `newline="\n"` for LaTeX toolchains in git repos.
- **OneDrive/Dropbox folders:** keep the SQLite DB outside synced folders (WAL locking).
- **Long-running watch jobs (RSS/OAI):** run them from the MCP server process or Task Scheduler rather than cron.

---

## 7. Live probe log (2026-10-06, from the user's machine)

| Probe | Result |
|---|---|
| `GET api2.openreview.net/notes?...` (anonymous) | `403 ChallengeRequiredError` |
| `GET api.openalex.org/works/doi:10.48550/arXiv.1706.03762` | `404`. Canonical DOI is `10.65215/2q58a426`; arXiv is a secondary location. |
| `GET api.openalex.org/works/doi:10.48550/arxiv.2310.06825` | `200`, but `referenced_works_count = 0` |
| OpenAlex keyless headers | `X-RateLimit-Limit-USD: 0.1`; search cost `0.001` |
| `GET api.semanticscholar.org/graph/v1/paper/ARXIV:1706.03762` (keyless, 4 tries) | `429` every time |
| `GET api.semanticscholar.org/datasets/v1/release/latest` | `200`, release `2026-09-29`; a dataset download needs a key (`401`) |
| `GET dblp.org/search/publ/api…`, `/rec/….bib` (custom UA; also uni-trier and dagstuhl mirrors) | Anubis "Making sure you're not a bot!" HTML |
| `GET sparql.dblp.org/sparql?...` | `200` JSON, about 3 s |
| Crossref `query.bibliographic` with `mailto` | `x-rate-limit-limit: 3`, `x-concurrency-limit: 3`, `x-api-pool: polite-array`. Top 3 hits for "Attention is all you need Vaswani" are `10.65215/*` posted-content reposts. |
| `export.arxiv.org/api/query?id_list=1706.03762` | `200`, versioned id `v7` |
| `export.arxiv.org/oai2` | `301` → `oaipmh.arxiv.org/oai` |
| `rss.arxiv.org/rss/cs.LG` | `200` RSS 2.0 |
| `arxiv.org/html/1706.03762v7` | `200`, "LaTeXML oxide 0.7.6" |
| `arxiv.org/bibtex/1706.03762` | `@misc{vaswani2023attentionneed, … year={2023}}` |
| HF `/api/papers/1706.03762`, `/api/papers/search`, `/api/daily_papers`, `/api/arxiv/1706.03762/repos` | All `200` without a token |
| `api.datacite.org/dois/10.48550/arxiv.1706.03762` | `200`, with an arXiv alternateIdentifier |
| `api.opencitations.net/index/v2/citation-count/doi:10.1038/nature14539` | `73495` |
| `api.unpaywall.org/v2/10.1038/nature14539?email=…` | `200`, `best_oa_location` = HAL |
| Europe PMC search by DOI | `200`, PMID `26017442` |
| `api.biorxiv.org/details/biorxiv/10.1101/2020.03.22.002386` | `200` |

---

## 8. Sources

**OpenAlex**
- https://help.openalex.org/api-reference/authentication
- https://help.openalex.org/access/example-costs
- https://blog.openalex.org/category/feature
- https://blog.openalex.org/walden-rewrite-launch/
- https://blog.openalex.org/?p=3924
- https://blog.openalex.org/major-update-to-unpaywall-database
- https://docs.openalex.org/api-entities/works/work-object

**Abstract availability**
- https://aarontay.substack.com/p/the-petrol-tank-for-ai-discovery
- https://groups.google.com/g/openalex-users/c/ptFDD7qWvYw/m/kXWDG3o5BAAJ

**Semantic Scholar**
- https://www.semanticscholar.org/product/api
- https://api.semanticscholar.org/api-docs/graph
- https://www.semanticscholar.org/product/api/license
- https://status.api.semanticscholar.org

**Crossref**
- https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/
- https://community.crossref.org/t/updates-to-rest-api-rate-limits/14872

**arXiv**
- https://info.arxiv.org/help/api/tou.html
- https://info.arxiv.org/help/bulk_data.html
- https://groups.google.com/a/arxiv.org/g/api
- https://blog.arxiv.org/
- https://aiweekly.co/alerts/fair-moderation-equitable-access-and-ai-arxivs-updated-rate-limit-policy
- https://ar5iv.labs.arxiv.org/

**DataCite**
- https://support.datacite.org/docs/api
- https://support.datacite.org/docs/rate-limit

**OpenReview**
- https://docs.openreview.net/getting-started/using-the-api
- https://openreview-py.readthedocs.io/
- https://openreview.net/forum/user%7Cstatement_regarding_api_security_incident
- https://blog.iclr.cc/iclr-2026-response-to-security-incident/

**ACL Anthology**
- https://aclanthology.org/faq/api/
- https://acl-anthology.readthedocs.io

**PubMed / PMC / Europe PMC**
- https://www.ncbi.nlm.nih.gov/books/NBK25497/
- https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
- https://www.nlm.nih.gov/pubs/techbull/jf26/jf26_Changes_to_PMC_ArtDsDistribServs_2026.html
- https://europepmc.org/RestfulWebService

**Other providers**
- bioRxiv: https://api.biorxiv.org/
- CORE: https://core.ac.uk/services/api
- OpenCitations: https://api.opencitations.net/index/v2
- Zenodo: https://blog.zenodo.org/2025/11/25/2025-11-14-search-api-updates/

**Papers with Code / HF**
- https://hyper.ai/en/news/42900
- https://www.codesota.com/papers-with-code/shutdown

**Google Scholar / SerpApi**
- https://proxyway.com/news/google-sues-serpapi
- https://www.gigazine.net/gsc_news/en/20251222-google-sues-serpapi

**scite, Connected Papers, ResearchRabbit**
- https://costbench.com/software/ai-research-tools/scite
- https://pypi.org/project/connectedpapers-py/
- https://aarontay.substack.com/p/researchrabbits-2025-revamp-iterative

**Formats**
- Better BibTeX: https://retorque.re/zotero-better-bibtex/citing/
- rebiber: https://github.com/yuchenlin/rebiber

**PDF extraction**
- GROBID: https://github.com/kermitt2/grobid, https://grobid.readthedocs.io/en/latest/Grobid-docker/
- Docling: https://github.com/docling-project/docling
- Marker: https://github.com/datalab-to/marker
- MinerU: https://github.com/opendatalab/MinerU
- PyMuPDF: https://artifex.com/blog/introducing-the-new-pymupdf4llm-now-including-layout

**Local search**
- sqlite-vec: https://github.com/asg017/sqlite-vec, https://pypi.org/project/sqlite-vec/
- SPECTER2: https://huggingface.co/allenai/specter2_base, https://allenai.org/blog/specter2-adapting-scientific-document-embeddings-to-multiple-fields-and-task-formats-c95686c06567
- SciRepEval: https://arxiv.org/abs/2211.13308
- Qwen3 Embedding: https://arxiv.org/abs/2506.05176

**Package versions and licences** were taken from the PyPI JSON API on 2026-10-06 (bibtexparser, citeproc-py, pymupdf4llm, pypdf, pypdfium2, docling, marker-pdf, mineru, grobid-client-python, nougat-ocr, rebiber, sentence-transformers, fastembed, pybtex, habanero, pyalex, arxiv, semanticscholar, openreview-py, acl-anthology, sqlite-vec, pdfplumber, bibtexautocomplete).
