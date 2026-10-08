# Survey: traditional, human-geared bibliography search & management tools (2026-10-06)

Purpose: inform the design of a reusable literature-review / bibliography MCP server for coding agents
(Claude Code, Codex) doing ML research. This surveys what *human* tools do, what is table stakes, what is
worth stealing, how their data models work, and which interop formats matter. The last two sections
answer "what of this matters for an agent?" and "how do we interoperate with a human's Zotero library?".

Claims about the current state (2025–2026) were checked against the web on 2026-10-06; sources are inline.
Where something was not verified it is marked *(unverified)*.

---

## 0. Status snapshot: what changed in 2025–2026

| Product | Change | Source |
|---|---|---|
| **Zotero** | Moved to a rapid major-version cadence. **Zotero 8** (2026-01-22): unified citation dialog, annotations shown as rows in the items list, note tabs, reader themes, continuous file renaming. **Zotero 9** (2026-04-10): Read Aloud, "Recently Read" virtual collection, insert annotations as live citations, group-member attribution, browser-based login. **Zotero 10** (2026-08-17, now 10.0.5): redesigned advanced search (nested condition groups), batch editing, undo/redo, multi-collection selection, PDF Reading Mode, accent-insensitive search, SQLite FTS5 full-text rewrite, WAL mode, and **local API write support**. | [Z8 blog](https://www.zotero.org/blog/zotero-8/), [faster release cycle](https://digitalhumanitiesnow.org/2026/01/a-faster-release-cycle-for-zotero/), [9.0 changelog](https://www.zotero.org/support/9.0_changelog), [changelog](https://www.zotero.org/support/changelog), [Z10 for developers](https://www.zotero.org/support/dev/zotero_10_for_developers) |
| **Zotero citation keys** | Zotero 8 has a **native "Citation Key" field**; Better BibTeX migrates its keys into it (pinned keys move out of `Extra`). | [forum: citation key field](https://forums.zotero.org/discussion/129821/), [BBT](https://retorque.re/zotero-better-bibtex/) |
| **ZotFile** | Dead since Zotero 7. Replaced by **Attanger** / **ZotMoov** (linked-file move) + Zotero's native renaming. | [forum](https://forums.zotero.org/discussion/comment/498774) |
| **Papers with Code** | **Sunset by Meta on 2025-07-24** without notice. Hugging Face launched "Trending Papers" (with Meta) as a replacement, but benchmarks/leaderboards (≈9.3k) and SOTA tables are not served any more. | [hyper.ai](https://hyper.ai/en/news/42900), [codesota archive note](https://www.codesota.com/papers-with-code) |
| **ResearchRabbit** | **Acquired by Litmaps (2025-05-08)**. Relaunched 2025-10-20 on a 280M-record corpus (Semantic Scholar + Crossref + OpenAlex), collections decoupled from search; new paid **RR+** (~$10/mo annual, country-based pricing); free tier retained. | [IT Brief](https://itbrief.co.nz/story/litmaps-acquires-researchrabbit-raises-1-million-for-ai), [RR announcement](https://www.researchrabbit.ai/announcement-researchrabbit-release-2025), [Aaron Tay review](https://aarontay.substack.com/p/researchrabbits-2025-revamp-iterative) |
| **Google Scholar** | Still no API. **Scholar Labs** (AI multi-aspect search with per-paper "how this answers your question") launched 2025-11-18 to limited logged-in users. | [Google blog](https://blog.google/outreach-initiatives/education/google-scholar-labs/) |
| **scite.ai** (Research Solutions) | Launched an official **Scite MCP** server (2026-02-26; `api.scite.ai/mcp`) for Claude/ChatGPT/Cursor/Claude Code. Paid tiers (~$12–20 basic, ~$50 pro). | [press release](https://researchsolutions.investorroom.com/2026-02-26-Research-Solutions-Launches-Scite-MCP,-Connecting-ChatGPT,-Claude,-Other-AI-Tools-To-Scientific-Literature) |
| **EndNote** (Clarivate) | EndNote 2025 (Apr 2025): AI "Key Takeaway", Cite-from-PDF, Find a Journal, Web of Science citing/related records. **EndNote Research Assistant** (Sep 2025): chat with PDFs, translation. | [EndNote blog](https://endnote.com/blog/introducing-endnote-2025-ai-powered-reference-management/), [Research Assistant](https://endnote.com/blog/introducing-endnote-research-assistant-ai-powered-document-chat-and-translation/) |
| **Mendeley** (Elsevier) | Mendeley Desktop discontinued (downloads off 2022-09-01) → Mendeley Reference Manager. Social features (profiles, feed, public groups) retired 2020–21. API still exists for "integration partners". | [UOC](https://biblioteca.uoc.edu/en/news/news/Switch-to-Mendeley-Reference-Manager-the-desktop-version-that-is-replacing-Mendeley-Desktop), [Elsevier support](https://www.elsevier.support/mendeley/answer/mendeley-is-refocusing-on-whats-important-to-our-users-what-does-it-mean-for-you) |
| **Citavi** (Lumivero) | Still Windows-only desktop (7.3.1, Jun 2026) + reduced Citavi Web. Citavi Free gone; many German/Swiss universities cancelled campus licences (Basel 2025-02, ASH Berlin 2025-12, FH Dortmund 2026-08) citing price rises → migrating to Zotero. | [Wikipedia](https://en.wikipedia.org/wiki/Citavi), [Basel](https://ub-easyweb.ub.unibas.ch/en/news/details/no-longer-a-licence-for-the-citavi-reference-management-program/), [FH Dortmund](https://www.fh-dortmund.de/microsite/bibliothek/zotero-umstieg.php?loc=en-US) |
| **JabRef** | 6.0 in long alpha/beta (alpha 6 May 2026, beta.1 later); built-in AI summarize/chat-with-PDF with citations jumping to PDF pages. | [JabRef blog](https://blog.jabref.org/2026/05/14/JabRef6-0-alpha6/), [docs/ai](https://docs.jabref.org/ai) |
| **ASReview** | **LAB v2** (2025): crowd/real-time multi-screener, new default models (ELAS u4 lightweight; multilingual + heavier context-aware), dynamic stopping suggestions, explainability, tags. v2.2 Dec 2025. | [Utrecht](https://www.uu.nl/en/news/launch-asreviewlab-v2), [Zenodo v2.2](https://zenodo.org/records/17817658) |
| **Citation Gecko** | No longer supported. **Inciteful** moved under incitefulmed.com. **Local Citation Network** alive but maintainer-starved. | [casrai](https://casrai.org/guides/inciteful-citation-network-paper-discovery), [LCN GitHub](https://github.com/LocalCitationNetwork/LocalCitationNetwork.github.io) |
| **arxiv-sanity-lite** | Effectively unmaintained (last commit 2023-06). | [gittrend](https://gittrend.io/repo/karpathy/arxiv-sanity-lite) |
| **Obsidian Zotero Integration** | Moved to obsidian-community org; last release Aug 2024 (slow). Alternatives: ZotLit, Zotero Desktop Connector, Zotero Sync. | [GitHub](https://github.com/mgmeyers/obsidian-zotero-integration), [obsidianstats](https://www.obsidianstats.com/tags/zotero) |
| **BibDesk** | Actively maintained (1.9.12; 1.9.10 Jan 2026, 1.9.11 Mar 2026). | [BibDesk](https://bibdesk.sourceforge.io/) |
| **pubs** | Maintenance-only. | [PyPI](https://pypi.org/project/pubs/) |

Trend: every commercial manager (EndNote, Citavi, Mendeley, Paperpile, JabRef) bolted on "chat with PDF /
key takeaways" in 2024–25; the open, programmable centre of gravity has consolidated on **Zotero**, and
the agent-facing surface is moving to **MCP** (Scite MCP; many community Zotero MCP servers, e.g.
[54yyyu/zotero-mcp](https://mcpservers.org/ko/servers/54yyyu/zotero-mcp); a June 2026
[forum request for headless Zotero for agents](https://forums.zotero.org/discussion/132184/) has no dev reply yet).

---

## 1. Reference managers

### 1.1 Per-tool notes

**Zotero** (free/OSS, desktop + web + iOS/Android; 300 MB free sync storage, paid tiers, WebDAV allowed).
The reference point. Translators (hundreds of site scrapers + import formats) power the browser Connector
and "Add by identifier" (DOI, ISBN, PMID, arXiv ID, ADS bibcode). Built-in PDF/EPUB/snapshot reader with
annotations stored in the DB (not in the PDF). Retracted-item warnings (Retraction Watch data). Group
libraries. Feeds (RSS). Citation via CSL (10k+ styles) into Word/LibreOffice/Google Docs. Plugin ecosystem
(Zotero 7 bootstrapped plugins): **Better BibTeX** (citekeys, auto-export, pandoc/CAYW, JSON-RPC),
Attanger/ZotMoov (file management), Zotero OCR, Scite plugin (citation tallies in item list), Cita
(Wikidata citations), Actions & Tags (scripting), Better Notes, plus many AI/LLM plugins. Details in §2.

**Mendeley Reference Manager** (Elsevier, free 2 GB). Web + desktop + Mendeley Cite (Word). Notebook
collects highlights across papers. Weak BibTeX story (auto-sync .bib existed in old Desktop); private
groups only. Lock-in risk and feature attrition history (§0). API: OAuth2 REST, partner-oriented.

**EndNote** (Clarivate, paid ~one-off licence + EndNote Web). Institutional standard in biomed. Strengths:
huge style library, "Find full text", Cite While You Write, Web of Science integration, Smart Groups
(=saved searches), Manuscript Matcher. `.enl` library + `.Data` folder; exports RIS/EndNote XML/tagged.
2025 AI features (§0). Poor fit for LaTeX/ML workflows.

**JabRef** (OSS, Java). **BibTeX/BibLaTeX file *is* the database** — the `.bib` is canonical, metadata
(groups, settings) stored in `@Comment{jabref-meta: ...}`. Strengths: citekey patterns, integrity checks
(field validation, duplicate keys), fetchers (arXiv, Crossref, DBLP, IEEE, INSPIRE, Springer, MathSciNet,
Semantic Scholar...), groups can be explicit, keyword-based, or **search-expression based** (dynamic),
"Lookup → full text", citation relations tab (cites/cited-by via Semantic Scholar/OpenCitations). AI chat
in 6.0. Very relevant precedent: a plain-text, VCS-friendly canonical store.

**Paperpile** (paid ~$3–12/mo; web app; PDFs stored in user's Google Drive). Best Google Docs citing;
Word add-in; BibTeX export and **auto-updating BibTeX URL/Overleaf sync**; labels + folders; shared folders.
Clean dedup UX. Closed, no public API.

**Papers / ReadCube** (Digital Science). Papers repositioned as the individual/small-team consumer brand;
ReadCube as enterprise literature management + document delivery. Enhanced PDF reader (inline references,
supplements, Altmetric/Dimensions metrics), smart lists, recommendations. Closed; limited API.

**Citavi** (Lumivero, Windows only). Distinctive "knowledge organizer": quotations/ideas as first-class
objects (direct quote, indirect quote, summary, comment) linked to a reference *and* page range, organised
into categories that form an outline of the paper you're writing; task planner per reference.
That **"knowledge item" model** is worth stealing even though the product is declining.

**BibDesk** (OSS, macOS). `.bib` file canonical (like JabRef); smart groups (saved searches), static groups,
keyword groups, autofile of PDFs by citekey template, AppleScript, external file groups (search PubMed,
arXiv via web groups). Strongly LaTeX-centric.

**Papis** (OSS, Python CLI, git-like). **One folder per document with `info.yaml` + files**; library =
directory tree; pluggable "pickers" (fzf-like), `papis add --from doi/arxiv/url`, `papis bibtex`,
`papis export`, `papis explore` (query crossref/arxiv/dblp...), `papis serve` web UI, Zotero importer,
vim/emacs integration, hooks/plugins, citations db. Strong precedent for an agent-friendly, plaintext,
greppable store. [docs](https://papis.readthedocs.io/)

**pubs** (OSS, Python CLI). Separates `bib/` (BibTeX per entry) from `meta/` (tags, notes, doc paths) –
all plaintext, git-versionable; citekey is the primary key. Maintenance-only. [PyPI](https://pypi.org/project/pubs/)

**Calibre** (e-book manager). Relevant only as a pattern: rich custom columns, OPDS server,
metadata-source plugins, full-text search; not a citation tool.

**Obsidian / Logseq literature notes.** Pattern: reference manager is the source of truth for metadata
+ annotations; a note app holds one Markdown "literature note" per item (filename = citekey), generated by
template (Zotero Integration uses Nunjucks over BBT data, including annotations with colour → callout
mappings and `zotero://open-pdf/...?page=&annotation=` backlinks). Re-import appends new annotations
since last import. Citations plugin (older) reads a BBT CSL-JSON/BibLaTeX auto-export file rather than
talking to Zotero live. Logseq has a built-in Zotero (web API) integration. Pandoc `[@citekey]` syntax +
CSL renders citations. **Pattern to steal: citekey-named note files + deep links back into the PDF.**

**Notion setups.** Database with properties (authors, year, status = to-read/reading/read, tags, rating),
one page per paper; populated via Zotero→Notion sync tools (e.g. Notero plugin) or manual. Value is
*workflow state* (reading status, priority, project relevance) that reference managers model poorly.

### 1.2 Common feature set (table stakes)

1. **Capture**: browser extension/bookmarklet scraping page metadata; add by identifier (DOI, arXiv,
   ISBN, PMID); drag-drop PDF → metadata extraction (DOI sniffing / title lookup); import BibTeX/RIS.
2. **Metadata**: typed items with fields + ordered creators; "retrieve/update metadata" from Crossref etc.
3. **Dedup**: automatic duplicate detection on DOI/ISBN/title(+year, author) with manual merge.
4. **Organisation**: folders/collections (hierarchical), tags/labels (flat, often coloured), saved/smart
   searches (dynamic), "unfiled", "recently added", trash.
5. **Files**: attach PDFs (stored or linked), auto-rename by template, find available OA PDF
   (Unpaywall), full-text indexing & search.
6. **Reading**: built-in PDF reader with highlights, notes, comments; annotations exportable to notes.
7. **Notes**: per-item rich-text notes + standalone notes.
8. **Citing**: CSL styles; word-processor plugins (Word, Google Docs, LibreOffice); citekeys for LaTeX/pandoc.
9. **Export**: BibTeX/BibLaTeX, RIS, CSL-JSON, formatted bibliography.
10. **Sync & share**: multi-device cloud sync; group/shared libraries with permissions.
11. **Search**: quick search (title/creator/year), advanced boolean search over fields, full-text.

### 1.3 Distinctive features worth stealing

| Feature | From | Why it matters for agents |
|---|---|---|
| Stable, pinned **citekeys** with a generation formula (`auth.lower + year + shorttitle`), collision suffixes, pinning | BBT, JabRef, BibDesk | Agents write LaTeX/Markdown; citekeys are the human-readable join key across `.bib`, notes, papers. |
| **Auto-export**: a `.bib`/CSL-JSON file per collection kept continuously in sync | BBT, Paperpile (Overleaf), Citations plugin | Agent's repo gets a `references.bib` that never drifts. |
| `collection.scanAUX`: build a collection from a LaTeX `.aux` | BBT | "What does this paper actually cite?" → library state. |
| Dynamic groups by search expression | JabRef, BibDesk smart groups, Zotero saved searches, EndNote Smart Groups | Saved queries = reusable agent views. |
| Retraction warnings | Zotero (Retraction Watch) | Agents should not cite retracted work. |
| **Knowledge items** (quote/paraphrase/idea + page range + category outline) | Citavi | Claims with provenance as first-class objects. |
| Continuous file renaming from metadata | Zotero 8 | Deterministic filenames for files on disk. |
| Plaintext one-folder-per-doc with YAML | Papis | Greppable, git-diffable, agent-native. |
| Bib vs meta separation | pubs | Keep bibliographic truth apart from local workflow state. |
| Reading status / priority / rating | Notion setups, Papers | Triage queue for an agent ("read next"). |
| Undo / batch edit with audit | Zotero 10 | Agent edits to a human library must be reversible. |
| Group member attribution (who added/modified) | Zotero 9 | Distinguish agent vs human contributions. |
| "Recently Read" virtual collection | Zotero 9 | Track what the agent has actually read vs merely found. |
| Cite-from-PDF (quote + citation) | EndNote 2025, Zotero 9 (annotations as live citations) | Quote-with-locator as an atomic operation. |

### 1.4 Data-model notes

- **Item types.** Zotero ~38 types (journalArticle, conferencePaper, preprint, book, bookSection, thesis,
  report, dataset, standard, computerProgram, webpage, ...); CSL has a similar but different type list;
  BibTeX ~14 entry types, BibLaTeX ~30 (incl. `@online`, `@software`, `@dataset`). Mappings are lossy:
  e.g. arXiv preprints are `preprint` in Zotero (`Archive ID: arXiv:2401.01234`, repository = arXiv),
  `@misc`/`@online` with `eprint`/`eprinttype = arxiv`/`archivePrefix` in BibTeX/BibLaTeX, `article` with
  `number`/`archive` in CSL. NeurIPS/ICLR papers exist as *both* an arXiv preprint and a proceedings
  paper → the ML-specific dedup headache (version-of rather than duplicate-of).
- **Identifiers.** DOI, ISBN, ISSN, PMID/PMCID, arXiv ID (versioned `v2`), URL, plus graph IDs
  (Semantic Scholar corpusId, OpenAlex W-id, DBLP key, OpenReview forum id). Most managers have only DOI
  /ISBN as real fields; others are stuffed into `Extra` (Zotero) or custom fields.
- **Collections vs tags vs saved searches.** Collections = explicit membership, hierarchical, an item
  can be in many (they behave like labels, not folders — deleting a collection doesn't delete items).
  Tags = flat strings; Zotero distinguishes manual (type 0) vs automatic/imported (type 1) tags and
  allows a few **coloured tags** with keyboard shortcuts. Saved searches = stored condition lists
  (field/operator/value, match all/any, nested since Zotero 10) evaluated dynamically; they can include
  `collection is X` and `savedSearch is Y`. JabRef groups unify all three (explicit / keyword / search).
- **Annotations.** Zotero stores annotations as **child items of the attachment** with
  `annotationType` (highlight, underline, note, text, image, ink), `annotationText`, `annotationComment`,
  `annotationColor`, `annotationPageLabel`, `annotationSortIndex` and `annotationPosition` JSON —
  `{pageIndex, rects:[[x1,y1,x2,y2]...]}` for PDFs (geometric anchor), CFI / text selectors for EPUB and
  snapshots. Hypothesis uses W3C Web Annotation selectors (TextQuoteSelector exact+prefix+suffix,
  TextPositionSelector, RangeSelector) with fuzzy re-anchoring; most others (Mendeley, Paperpile) embed
  or store page-rect anchors. **Geometric anchors are useless to an agent; text-quote anchors are ideal.**
- **Dedup/merge.** Zotero compares title/DOI/ISBN then year (±1) and creators (one matching last name +
  first initial); merge picks a *master*, lets you choose per-field alternatives, moves attachments/notes
  to master, **unions collections and tags**, trashes the others and records `dc:replaces` relations so
  citations in documents keep resolving. Only within one library; no "not a duplicate" memory historically
  (cannot exclude false positives). [docs](https://www.zotero.org/support/duplicate_detection)
- **Relations.** Zotero "Related" (`dc:relation`, symmetric, manual), `dc:replaces` (merge),
  `owl:sameAs` (cross-library copies). No typed citation edges natively (plugins add them).
- **Overflow fields.** Zotero `Extra`: `Key: value` lines parsed as CSL variables (`Original Date:`,
  `tex.*` lines for BBT) — a de-facto escape hatch for missing fields.
- **Versions & identity.** Zotero objects have 8-char keys per library + monotonically increasing
  `version`; libraries are `users/<id>` or `groups/<id>`.

---

## 2. Zotero in depth (the library most ML researchers already have)

### 2.1 Programmatic access surfaces

| Surface | Read | Write | Needs | Notes |
|---|---|---|---|---|
| **Local API** `http://localhost:23119/api/` (Zotero 7+) | Yes (no auth) | **Zotero 10+**: POST/PUT/PATCH/DELETE for items, collections, searches, tags, full-text, file uploads; per-client key via `POST /api/local/authorize` (user approves in app) | Desktop app running; Settings → Advanced → "Allow other applications..." | Same JSON as web API v3; `users/0` = local user; not paginated by default; **`/searches/<key>/items` returns saved-search results** (web API can't); offline, no rate limits; Host header must be localhost; `Zotero-Server-ID` header required on writes; versions are *local* versions in Z10. [docs](https://www.zotero.org/support/dev/web_api/v3/local_api), [Z10 dev](https://www.zotero.org/support/dev/zotero_10_for_developers) |
| **Web API v3** `https://api.zotero.org` | Yes | Yes (batches of 50, optimistic concurrency via `If-Unmodified-Since-Version` → 412) | API key; library must sync; files need Zotero Storage/WebDAV | Works headless/remote and for group libraries; `since=` + `/deleted` for incremental sync; `format=bibtex|biblatex|csljson|ris|keys|versions`, `include=bib,citation,data` with any CSL `style`; `limit` ≤ 100; `Backoff` / 429 `Retry-After`. [basics](https://www.zotero.org/support/dev/web_api/v3/basics), [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing) |
| **Better BibTeX JSON-RPC** `POST /better-bibtex/json-rpc` | Yes | Limited (`autoexport.add`, `collection.scanAUX`, `item.regenerate_key`) | BBT installed, app running | Citekey-centric: `item.search`, `item.citationkey`, `item.export(citekeys, translator)`, `item.bibliography`, `item.attachments`, `item.notes`, `item.collections`, `user.groups`, `api.ready`. [docs](https://retorque.re/zotero-better-bibtex/exporting/json-rpc/) |
| **BBT pull export / CAYW** | Yes | No | BBT | e.g. `/better-bibtex/export/collection?/1/MyColl.biblatex`, `/better-bibtex/cayw?format=pandoc` *(URL forms from memory)*. |
| **BBT auto-export files** | Yes (file) | No | BBT, configured once | Zero-coupling: agent reads a `.bib`/CSL-JSON file in the repo that BBT keeps current. |
| **`zotero.sqlite` read-only** | Yes | **Never** | File access | Officially "externally readable"; writes corrupt. Zotero historically holds an exclusive lock while running → copy the DB (or `zotero.sqlite.bak`) or open read-only/immutable; schema changes between releases (Z10: FTS5, WAL, shadow columns). [docs](https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access) |
| **Connector server** `/connector/*` | — | Save items into the *currently selected* collection | App running | What the browser extension uses; undocumented-ish, UI-coupled. |
| **Translation server** (`zotero/translation-server`, Node/Docker) | — | — | Self-host | Headless Zotero translators: `/web` (URL→metadata), `/search` (DOI/ISBN/PMID/arXiv→metadata), `/export`. Reuses the best metadata-scraping corpus without a user library. |
| **Plugin (privileged JS)** | Everything | Everything | Write and install a plugin | Max power, max coupling. |

### 2.2 Notable Zotero behaviours an integrator must respect
- Item keys are per library; the same paper in two libraries has two keys (`owl:sameAs`).
- Child items: attachments (`imported_file`, `imported_url`, `linked_file`, `linked_url`), notes (HTML),
  annotations (children of attachments). Full text is indexed per attachment (`/items/<key>/fulltext`).
- Citekeys: native field since Zotero 8; BBT still generates/pins them.
- Users care deeply about their library: unrequested tags, collections, or "fixed" metadata from an
  agent are vandalism. Zotero 10's undo + Zotero 9's attribution help, but a design should write into a
  clearly marked staging collection/tag by default.

---

## 3. Discovery and citation-graph tools

### 3.1 Per-tool notes

**Google Scholar.** Broadest coverage (incl. grey literature, theses, books), version clustering ("All N
versions"), "Cited by" with search-within-citing, "Related articles", author profiles with metrics, email
alerts (query, new citations to an article, new articles by an author), My Library with labels, BibTeX
/RIS per result, library links (OpenURL). **No API; aggressive anti-scraping/CAPTCHA**; ToS forbids
automated queries. Scholar Labs (Nov 2025) adds LLM multi-aspect search with per-paper justification.

**Semantic Scholar** (AI2). Web UI: library folders, **Research Feeds** per folder trained by
thumbs-up/down, alerts (paper citations, author, topic, feed) daily/weekly, TLDRs, "Highly Influential
Citations", citation intents (background/method/result), Semantic Reader (inline citation cards). API:
Academic Graph (papers/authors/citations/references, SPECTER2 embeddings), Recommendations (single-seed
and positive/negative lists), Datasets (monthly bulk), snippet search. Unauthenticated shared pool; keys
start at 1 RPS; practical limits are flakier than documented (500s as well as 429s).
[API](https://semanticscholar.org/product/api), [alerts FAQ](https://webflow.semanticscholar.org/faq/alert-types)

**Connected Papers.** One seed → graph of ~40 papers by **similarity (co-citation + bibliographic
coupling)**, not direct citation; node size = citations, colour = year; "Prior works" and "Derivative
works" lists; multi-origin graphs. Free 5 graphs/month; Academic ~$6/mo.
[pricing](https://www.costbench.com/software/ai-research-tools/connected-papers/)

**ResearchRabbit** (now Litmaps). Collections of seeds → "similar work", "earlier work", "later work",
author networks; iterative chaining; Zotero collection sync; email recommendations per collection.

**Litmaps.** Seed articles → citation map (x = time, y = citations); **Monitor**: reruns a saved
search and emails new related articles; Zotero sync (Pro); tags; shareable maps.

**Inciteful.** Seed set → graph of citations within 2 hops; ranks with PageRank, lists "most important
papers", "review papers", "top authors/venues"; **"Literature Connector"** finds paths between two
papers; filters via SQL-ish query; data from OpenAlex/S2/Crossref.

**Local Citation Network.** Input set (DOIs/Zotero export/ORCID) → local graph; ranks *missing* papers by
how many input papers cite them (local in-degree) and how many cite the input (out-degree) — a direct,
explainable **snowballing** recommender. OSS (GPL-3), OpenAlex/S2/Crossref backends.

**CitationGecko** — discontinued; idea: seed set + "papers cited by many seeds" / "papers citing many seeds".

**scite.ai.** "Smart Citations": every citation statement (the sentence context) classified as
**supporting / contrasting / mentioning**; per-paper tallies, section of citing text, editorial notices
(retractions, corrections); dashboards & alerts; Zotero plugin; reference check of a manuscript; now an
official MCP server. Paid.

**alphaXiv.** Overlay on arXiv (swap `arxiv`→`alphaxiv` in URL): line/figure-anchored public comment
threads, private notes, Ask-AI grounded in the paper, generated blog summaries, trending feeds.
[blog](https://alphaxiv.org/blog/one-year)

**arxiv-sanity / arxiv-sanity-lite.** Tag papers → per-tag **SVM over TF-IDF of abstracts** ranks new
arXiv papers; daily email per tag. Unmaintained, but the "a personal classifier per interest" pattern is
cheap and excellent.

**Papers with Code** — gone (§0). Its unique value was paper↔repo links, task/dataset/method taxonomy
and **SOTA leaderboards**; HF Trending Papers covers the repo link + popularity but not leaderboards.

**Hugging Face Papers.** Daily curated arXiv papers ranked by upvotes, comments, linked
models/datasets/Spaces/GitHub; Trending; paper pages; API (`/api/daily_papers`, `/api/papers/{arxiv_id}`,
search) and `hf papers` CLI. Free, unauthenticated reads.

**OpenReview.** Submissions + reviews, scores, rebuttals, meta-reviews, decisions for ICLR/NeurIPS/
TMLR/COLM etc.; API v2 (`openreview-py`: `get_all_notes(invitation=...)`, `get_attachment`,
`get_all_venues`). Quality and controversy signal unavailable elsewhere; also the canonical version for
accepted papers. [docs](https://openreview-py.readthedocs.io/)

### 3.2 Common feature set
Seed paper(s) → related set; forward (cited-by) and backward (references) expansion; similarity or
co-citation recommendations; filters (year, venue, citations, OA); save to collection; export
(BibTeX/RIS) or push to Zotero; alerts on new citations / new papers by author / saved query / feed;
author pages; metrics (citation counts, influential citations).

### 3.3 Distinctive features worth stealing
- **Explainable ranking of missing papers** relative to a seed set (LCN in/out-degree, Inciteful
  PageRank, CitationGecko). Better than opaque similarity for an agent that must justify inclusion.
- **Path between two papers** (Inciteful Literature Connector).
- **Prior vs derivative works** split (Connected Papers) — foundations vs follow-ups.
- **Citation statement + stance** (scite) and **citation intent / influential flag** (S2) — lets an
  agent ask "who contradicts this result?" rather than "who cites this?".
- **Per-folder learned feeds** from positive/negative labels (S2 Research Feeds, arxiv-sanity) →
  `recommend(positives, negatives)`.
- **Monitors** that rerun a saved query and diff (Litmaps, Scholar alerts).
- **Community signals**: HF upvotes, alphaXiv threads, OpenReview reviews/decisions.
- **Version clustering** (Scholar "all versions") — arXiv v1..vN + proceedings + journal as one work.

### 3.4 Data-model notes
Nodes = works (keyed by DOI / S2 id / OpenAlex id), edges = citations (directed, sometimes with
contexts/intents/stance), derived similarity edges (co-citation, coupling, embedding cosine). Seed sets
= small collections. Alerts = saved query + cadence + last-seen watermark. Most tools' corpora are now
the same three open sources (**OpenAlex, Semantic Scholar, Crossref**) — the differentiation is ranking
and UX, not data.

---

## 4. Systematic review tools

### 4.1 Per-tool notes
**Covidence** (Cochrane-endorsed; ~$339/yr per review, institutional licences). Pipeline: import (RIS/
EndNote XML/PubMed) → auto dedup → title/abstract screening (dual, blinded, conflicts resolution) →
full-text screening with **mandatory exclusion reasons** → extraction (templates) → risk of bias
(Cochrane RoB) → export; auto **PRISMA** diagram. ML: relevance sorting, Cochrane **RCT classifier**
(>99.5% sensitivity, can auto-exclude non-RCTs), LLM extraction suggestions from PDFs.
[automation overview](https://support.covidence.org/help/overview-of-all-automation-ai-features-available-in-covidence)

**Rayyan** (freemium, published pricing). Fast keyboard screening, include/exclude/maybe, labels and
exclusion reasons, blind mode, 5-star relevance predictions after ~50 decisions, duplicate detection
with similarity %, PICO highlighting, PRISMA in paid tiers, AI extraction in higher tiers.
[pricing summary](https://ponder.ing/blog/rayyan-alternatives)

**ASReview LAB** (OSS, Utrecht; Python, local). **Active learning screening**: prior knowledge (≥1
relevant, ≥1 irrelevant) → model ranks unseen records → user labels top record → retrain → repeat.
Pluggable feature extractor + classifier + query strategy + balance strategy; v2 adds ELAS presets,
multilingual & heavier models, crowd screening, stopping suggestions, tags, explainability; simulation
mode for benchmarking on labelled datasets (SYNERGY). `.asreview` project files (zip with SQLite).
[Utrecht](https://www.uu.nl/en/news/launch-asreviewlab-v2)

**DistillerSR** (enterprise, pharma/regulatory). Configurable forms per level, audit trail, **continuous
AI reprioritisation**, **AI Audit** (flags likely false excludes), AI as second screener, LLM-based
agentic extraction. [DistillerSR](https://www.distillersr.com/?p=11470)

**PRISMA 2020** (reporting standard, not software): flow diagram counts — records identified per
source, duplicates removed, records screened, excluded, reports sought, not retrieved, assessed for
eligibility, excluded with reasons (by reason), studies included; PRISMA-S for search reporting
(exact query strings per database, date run, limits). Tools: PRISMA2020 Shiny app / R package.

### 4.2 Common feature set
Multi-source import (RIS/NBIB/EndNote XML/CSV), dedup with review, staged screening (TA → FT) by ≥2
blinded reviewers with conflict resolution, inclusion/exclusion criteria and **coded exclusion reasons**,
notes/labels per record, ML prioritisation, full-text retrieval, extraction forms, quality/RoB
assessment, **PRISMA counts generated from decision logs**, inter-rater agreement (kappa), audit trail,
export of decisions.

### 4.3 Distinctive / worth stealing
- **Decision log as the source of truth** → PRISMA counts are a *query*, not hand-maintained numbers.
- **Protocol-first**: criteria fixed before screening; query strings recorded verbatim with date
  (PRISMA-S) → reproducible searches.
- **Stopping rules** for active learning (ASReview: N consecutive irrelevant; SAFE-like heuristics;
  DistillerSR AI audit) → an agent needs an explicit "done screening" criterion.
- **Simulation mode** (ASReview): evaluate a screener on a labelled set — exactly how to evaluate an
  LLM screener.
- **Dual screening + disagreement surfacing** → agent as one screener, human as the other.
- **AI Audit of excludes** → second-pass check on what the agent threw away.

### 4.4 Data model
Review (protocol, criteria) → search runs (source, query, date, count) → records (imported, with
source run) → dedup clusters → screening decisions (record × stage × reviewer → include/exclude/maybe +
reason + timestamp) → consensus decision → full-text report(s) → extraction rows (study-level, often
many reports per study) → RoB judgements. Note the **record ≠ report ≠ study** distinction (one study,
several papers; one paper, several versions).

---

## 5. Reading and annotation tools (patterns only)

- **Zotero reader**: PDF/EPUB/snapshot; highlight/underline/note/text/image/ink; colours carry
  user-defined semantics; annotations → note with citations back to page; Reading Mode (reflowed text,
  Z10), Read Aloud (Z9). Stored in DB, exportable into the PDF on demand.
- **Hypothesis**: web/PDF annotations on any URL; W3C Web Annotation data model; robust text-quote
  anchoring with re-anchoring when documents change; PDF fingerprint + URL for document identity;
  public/private/group layers; REST API.
- **Highlights (macOS), PDF Expert, Skim**: annotations written into the PDF; export to Markdown/
  Obsidian with page links.
- **Readwise / Reader**: aggregates highlights from everywhere (Kindle, web, PDFs; Zotero via
  `zotero2readwise`), spaced-repetition resurfacing, tags, export API and Markdown sync.
- **Kami etc.**: classroom PDF annotation; irrelevant.
- **alphaXiv / Semantic Reader**: annotation layer *keyed to the canonical paper*, shared, with inline
  reference cards.

Common pattern: annotation = (document identity, anchor, quoted text, comment, colour/type, tags,
author, time). The anchor is either geometric (page + rects) or textual (quote + context). Exports
flatten to Markdown with a deep link back.

---

## 6. Interop formats

| Format | Role | Strengths | Weaknesses | Status |
|---|---|---|---|---|
| **BibTeX** | LaTeX citation | Universal in ML/CS; citekeys; trivially diffable | Loose schema, LaTeX-escaped strings, weak types, no unicode guarantee, no structured names/dates | **De facto standard for ML papers** (arXiv, Overleaf, NeurIPS/ICML templates) |
| **BibLaTeX** | Modern LaTeX (biber) | Unicode, ISO dates, `eprint`/`eprinttype`, `doi`, `@online/@software/@dataset`, related entries | Not supported by many venue templates (natbib/BibTeX required) | Preferred superset; export both |
| **CSL-JSON** | Citation Style Language data | Well-specified JSON, structured names/dates, used by pandoc, Zotero (citeproc), Mendeley, Manubot | No attachments/notes/collections; `id` not necessarily a citekey | **De facto programmatic interchange** |
| CSL-YAML | Same, YAML | pandoc front-matter | — | niche |
| **RIS** | Legacy tagged format | Every database/SR tool imports it (PubMed, Scopus, WoS, Covidence, Rayyan) | Ambiguous tags, vendor dialects | **De facto for SR tooling and DB exports** |
| **Zotero RDF** | Zotero full export | Includes notes, attachments (with files), collections, tags, relations | Zotero-specific | Lossless Zotero migration |
| Zotero web API JSON | Zotero native | Complete item data incl. annotations, versions | Zotero-specific | Use when talking to Zotero |
| EndNote XML / `.enw` | EndNote | Complete EndNote data | Vendor-specific | needed for EndNote users |
| MODS / Dublin Core / MARC | Library world | Rich | Heavy | irrelevant for ML |
| Hayagriva (YAML) | Typst | Modern, readable | Young | watch |
| NBIB/MEDLINE | PubMed | — | — | biomed only |
| W3C Web Annotation (JSON-LD) | Annotations | Standard selectors | Few tools besides Hypothesis | Use its selector vocabulary |

Recommendation: canonical internal JSON close to **CSL-JSON + identifiers + provenance**, with
first-class **BibTeX/BibLaTeX export using stable citekeys**, CSL-JSON import/export, RIS import (for SR
and database exports), and Zotero web-API JSON as the bridge format.

---

## 7. Feature matrices

Legend: ● full / native · ◐ partial / via plugin / paid tier · — none.

### 7.1 Reference managers

| Feature | Zotero | Mendeley RM | EndNote | JabRef | Paperpile | Papers | Citavi | BibDesk | Papis | pubs |
|---|---|---|---|---|---|---|---|---|---|---|
| Open source / free | ●/● | —/● | —/— | ●/● | —/— | —/— | —/— | ●/● | ●/● | ●/● |
| Add by DOI | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Add by arXiv ID | ● | ◐ | ◐ | ● | ● | ● | ◐ | ◐ | ● | ● |
| Add by ISBN / PMID | ●/● | ◐ | ●/● | ●/● | ●/● | ●/● | ●/● | ◐ | ●/◐ | ●/— |
| Browser capture | ● | ● | ● | ◐ | ● | ● | ● | — | ◐ | — |
| Dedup + merge | ● | ● | ● | ● | ● | ● | ● | ◐ | ◐ | ◐ |
| Stable citekeys | ● (native Z8 + BBT) | ◐ | ◐ | ● | ● | ◐ | ● | ● | ● | ● |
| BibTeX/BibLaTeX export | ● (BBT best) | ● | ● | ● (native file) | ● | ● | ● | ● (native file) | ● | ● (native) |
| CSL-JSON | ● | ◐ | — | ◐ | — | ◐ | ◐ | — | ◐ | — |
| RIS | ● | ● | ● | ● | ● | ● | ● | ● | ◐ | — |
| Auto-synced .bib file | ◐ (BBT) | ◐ | — | ● | ● | ◐ | ◐ | ● | ● | ● |
| Collections (hier.) | ● | ● | ● | ● | ● | ● | ● | ◐ | ◐ | — |
| Tags | ● | ● | ◐ | ● | ● | ● | ● | ● | ● | ● |
| Saved / smart searches | ● | — | ● | ● | ◐ | ● | ● | ● | ◐ | — |
| PDF attach + rename | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Full-text search | ● | ● | ● | ◐ | ● | ● | ● | ◐ | ◐ | — |
| Built-in annotation | ● | ● | ● | ◐ | ● | ● | ● | ◐ | — | — |
| Structured quotes/ideas | ◐ | ◐ | — | — | — | — | ● | — | — | — |
| Cloud sync | ● | ● | ● | ◐ (file sync) | ● | ● | ◐ | ◐ | ◐ (git) | ◐ (git) |
| Groups / sharing | ● | ◐ | ● | ◐ | ● | ● | ● | — | — | — |
| Word/GDocs citing | ●/● | ●/— | ●/◐ | ◐ | ●/● | ●/● | ●/— | — | — | — |
| Retraction alerts | ● | — | ◐ | — | — | ◐ | ◐ | — | — | — |
| Public read API | ● local + web | ◐ partner | — | — | — | — | — | ◐ AppleScript | ● Python/CLI | ● CLI |
| Write API | ● (web; local in Z10) | ◐ | — | — | — | — | — | ◐ | ● | ● |
| AI chat/summary | ◐ (plugins) | ● | ● | ● (6.0) | ● | ● | ● | — | — | — |
| Plaintext / VCS-friendly store | — | — | — | ● | — | — | — | ● | ● | ● |

### 7.2 Discovery tools

| Feature | G. Scholar | Sem. Scholar | Conn. Papers | ResearchRabbit | Litmaps | Inciteful | LCN | scite | HF Papers | alphaXiv | OpenReview |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Free core | ● | ● | ◐ | ● | ◐ | ● | ● | ◐ | ● | ● | ● |
| Official API | — | ● | — | — | — | — | — (OSS) | ● (+MCP) | ● | ◐ | ● |
| Keyword search | ● | ● | ◐ | ● | ● | ◐ | — | ● | ● | ● | ◐ |
| Cited-by / references | ● | ● | ◐ | ● | ● | ● | ● | ● | — | — | — |
| Seed-set expansion | — | ● (recs API) | ◐ | ● | ● | ● | ● | — | — | — | — |
| Explainable ranking | — | ◐ | ◐ | ◐ | ◐ | ● | ● | ● | ◐ (votes) | — | ● (scores) |
| Citation context / stance | ◐ | ● (intent) | — | — | — | — | — | ● | — | — | — |
| Alerts / monitors | ● | ● | — | ● | ● | — | — | ● | ◐ | ◐ | ◐ |
| Code / model links | — | — | — | — | — | — | — | — | ● | ◐ | ◐ |
| Peer review signal | — | — | — | — | — | — | — | — | — | ◐ | ● |
| Zotero integration | ◐ (export) | ◐ (export) | ◐ | ● | ● | ◐ | ● (import) | ● (plugin) | — | — | — |

### 7.3 Systematic-review tools

| Feature | Covidence | Rayyan | ASReview | DistillerSR |
|---|---|---|---|---|
| Open source | — | — | ● | — |
| Dedup | ● | ● | ◐ (datatools) | ● |
| Dual blinded screening + conflicts | ● | ● | ◐ (crowd) | ● |
| ML prioritisation | ● | ● | ● (core) | ● |
| Stopping guidance | — | — | ● | ◐ (AI audit) |
| Exclusion reasons | ● | ● | ◐ (tags) | ● |
| PRISMA diagram | ● | ◐ (paid) | — | ● |
| Extraction / RoB | ●/● | ◐ | — | ●/● |
| LLM extraction | ● | ◐ (paid) | — | ● |
| Simulation/benchmark | — | — | ● | — |
| Local / offline | — | — | ● | — |

---

## 8. What of this matters for an AI agent user?

### 8.1 Translates directly (build these)
1. **Identifier resolution & normalisation**: DOI, arXiv (with version), S2 id, OpenAlex id, DBLP key,
   OpenReview forum id, ISBN, PMID, URL → one canonical *work* with all known IDs. Import "by
   identifier" is the agent's primary capture path (there is no browser).
2. **Dedup with merge provenance**: identifier-first, then fuzzy title+year+author; **work vs
   version** (arXiv vN, workshop, proceedings, journal) modelled explicitly; keep `replaces`
   records so old references resolve; remember "not a duplicate" decisions.
3. **Stable citekeys + BibTeX/BibLaTeX export + auto-exported `.bib` per project/collection**.
4. **Collections, tags, saved searches** — collections as projects/review scopes, tags for workflow
   state, saved searches as named reusable queries.
5. **Full-text storage and search** over attached PDFs/HTML (FTS + optionally embeddings), with
   chunk-level, *text-anchored* hits.
6. **Annotations/notes with provenance** — quote + locator (page, section, char offsets) + comment +
   author (agent/human) + verification status.
7. **Citation graph expansion** (backward/forward snowballing) and **seed-set ranking** with
   explanations (LCN/Inciteful style).
8. **Alerts → incremental queries**: saved query + watermark (`since`), returning only new results.
9. **Screening log**: per-record decisions with criteria, reasons, stage, actor, timestamp → PRISMA
   counts computed on demand; search runs recorded verbatim (PRISMA-S).
10. **Retraction / editorial-notice checks** before citing.
11. **Quality signals**: venue, OpenReview decision/scores, citation counts, influential citations,
    scite stance tallies, HF/code links.

### 8.2 Irrelevant or near-irrelevant for agents
Graph *visualisations* (Connected Papers/Litmaps canvases), reader themes, Read Aloud, mobile apps,
drag-and-drop, coloured-tag keyboard shortcuts, Word/Google Docs citation plugins and CSL style zoo
(agents write LaTeX/Markdown; one or two formatted styles suffice), journal finders, social/profile
features, spaced-repetition resurfacing, browser-capture UX, PDF geometric highlight rectangles.

### 8.3 Needs an agent-specific redesign
| Human workflow | Why it breaks for agents | Redesign |
|---|---|---|
| Visual graph exploration | Agents can't "see" clusters; context budget | Ranked lists with *reasons* (shared refs, co-citation counts, path), paged, with dedup against library ("already have / new") |
| Reading a PDF in a viewer | Token cost; hallucination risk on long docs | Section-aware chunked full text, retrievable by locator; quote-verification tool ("does this exact quote appear on p. N?") |
| Highlights with colours | Colour semantics are implicit | Typed notes: `claim`, `method`, `result/number`, `limitation`, `baseline`, `dataset`, each with quote + locator |
| Email alerts | Agent has no inbox, sessions are ephemeral | Pull-based `what's new since <watermark>` per saved query/seed set; optional scheduled job writes a digest |
| Screening by clicking | Agent screens fast but may be overconfident | LLM screener with explicit criteria, reason codes, confidence; human audit queue for low-confidence + random sample; stopping rule; simulation on labelled sets |
| Manual dedup/merge review | Agent may merge wrongly | Deterministic rules + merge proposals; merges reversible; preserve all IDs |
| "My library" as single truth | Agents run in many repos, in parallel | Per-project scopes + shared global store; concurrency control (versions / If-Unmodified-Since); idempotent upserts |
| Trusting metadata from scrapers | Agents confabulate citations | Every field carries source (Crossref/S2/OpenAlex/arXiv/human) + fetched-at; "verified from primary text" flag; print gate for citations |
| Literature notes in Obsidian | Free-form | Structured, queryable claims/evidence linked to works, exportable as Markdown notes named by citekey |
| Rate-limited discovery APIs | Agents hammer APIs | Central cache, backoff, per-source budgets, batch endpoints |

### 8.4 Interoperating with a human's existing Zotero library

| Option | Pros | Cons | Use for |
|---|---|---|---|
| **Local API (read)** | No key, no network, no rate limit, full item JSON incl. annotations, full text, saved-search results; same schema as web API | Zotero desktop must be running (no headless mode); localhost only; Zotero 7+ | Default read path on the user's machine |
| **Local API (write, Zotero 10+)** | Writes land instantly, undoable, attributable; no sync dependency | Requires user approval per client key; only Zotero 10+; still needs app running; risk of polluting a curated library | Opt-in writes to a dedicated `_agent` collection/tag |
| **Web API** | Headless, remote (CI, cloud agents), group libraries, mature optimistic concurrency, `since` incremental sync | Needs API key (secret handling), sync must be on, rate limits, files only if stored on Zotero/WebDAV | Cloud/remote agents; background sync of a mirror |
| **BBT JSON-RPC** | Citekey-native search & export, `scanAUX`, bibliography; ideal for LaTeX projects | Requires BBT plugin; app running; API surface is BBT-specific | Citekey lookup/export, AUX→collection |
| **BBT auto-export file** | Zero runtime coupling; works when Zotero closed; perfect for `references.bib` | One-way, only what's exported, no annotations | Reading the user's bib in a repo |
| **`zotero.sqlite` (copy, read-only)** | Works with Zotero closed; complete data incl. annotations; fast bulk reads | Schema unstable across versions (Z10 changed FTS/WAL); lock while running; never write; attachments resolved via storage dir | Fallback bulk import / mirror |
| **Translation server** | Best-in-class metadata extraction from URLs/identifiers without a library | Self-hosted Node service | Metadata resolution inside the agent tool |

Recommended posture: **mirror, don't mutate.** Read from the local API (fallback: web API, then SQLite
copy) into the agent's own store keyed by Zotero `libraryID/itemKey` + DOI/arXiv/citekey, re-syncing
incrementally by version. Answer "is it already in the user's library?" before adding. Write back only on
explicit opt-in, into a staging collection with an `agent` tag and a note recording provenance, via the
local API (Z10+) or web API with version preconditions.

---

## 9. Feature checklist for the MCP server (derived)

Core
- [ ] Canonical work model: typed item (CSL-like types), ordered creators, dates, venue, abstract
- [ ] Multi-identifier map (DOI, arXiv+version, S2, OpenAlex, DBLP, OpenReview, ISBN, PMID, URL, Zotero key)
- [ ] Work ↔ versions (preprint/proceedings/journal) and merge provenance (`replaces`), "not-duplicate" memory
- [ ] Add-by-identifier resolver (Crossref, arXiv, S2, OpenAlex, DBLP; optional Zotero translation-server)
- [ ] Stable citekeys (formula + collision suffix + pinning); BibTeX & BibLaTeX export; CSL-JSON in/out; RIS import
- [ ] Auto-exported `.bib` per project/collection
- [ ] Collections (projects/scopes), tags (workflow state), saved searches with `since` watermarks
- [ ] Attachments: fetch OA PDF/HTML, store, full-text index (FTS + optional embeddings), chunk locators
- [ ] Notes/annotations: quote + locator + comment + type + actor + verification status; quote verification
- [ ] Field-level provenance and fetched-at; retraction/editorial-notice check

Discovery
- [ ] Keyword search across S2/OpenAlex/arXiv/Crossref (+ HF Papers, OpenReview) with dedup vs library
- [ ] Backward/forward snowballing; seed-set ranking with explanations (co-citation, coupling, in/out-degree)
- [ ] Recommendations from positive/negative sets; "what's new since" monitors
- [ ] Quality signals: venue, OpenReview decision/scores, citation counts/influential, code links, stance (if scite)

Review workflow
- [ ] Protocol (criteria) + recorded search runs (query, source, date, count)
- [ ] Screening decisions per record × stage × actor with reason codes and confidence; human audit queue
- [ ] PRISMA counts computed from the log; stopping rule

Interop & safety
- [ ] Zotero bridge: local API read (→ web API → SQLite copy fallback); BBT JSON-RPC for citekeys; opt-in writes to a staging collection
- [ ] Optimistic concurrency, idempotent upserts, caching + per-source rate budgets
- [ ] Stored text treated as untrusted data
