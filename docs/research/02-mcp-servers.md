# 02 — Existing MCP servers for literature search, paper reading and bibliography management

*Survey date: 2026-10-06. Star counts and last-push dates come from the GitHub REST API on that date. Tool lists come from each project's README, docs or source. Where a fact comes from a third-party directory and was not confirmed upstream, it is marked (unverified).*

---

## 0. TL;DR

- **The ecosystem is large but shallow.** There are dozens of arXiv, Semantic Scholar, PubMed, OpenAlex and Zotero servers. Nearly all of them are **stateless API wrappers** with the same five verbs: *search, get-by-id, citations/references, download/read full text, export citation*.
- **The main trend since 2025 is vendor-hosted remote servers using OAuth.** These include the official OpenAlex server (`mcp.openalex.org`), Ai2 Asta (Semantic Scholar), alphaXiv, Consensus, scite, Undermind, Elicit (public July 2026), Wiley Scholar Gateway, and Anthropic's own PubMed and bioRxiv connectors. Claude Science (beta since 2026-06-30) adds a built-in "Literature Graph" (OpenAlex + arXiv), a full-text access chain and a literature-review skill.
- **The 2026 "second-generation" open-source servers** add bounded or paginated outputs, toolset profiles, provenance fields, local libraries, and safety around writes. The most relevant to us are blazickjp/arxiv-mcp-server, Liyux3/scholar-mcp, cyanheads/*, oscardvs/zoteus, OrgMentem/zotio, szeider/mcp-dblp and Hylouis233/bibverify.
- **Nobody ships a local, project-scoped, provenance-tracked evidence store** that links claims to passages to papers, records which agent and session made each judgement, tracks snowballing state and screening decisions, and emits a LaTeX-ready `.bib`. Pieces exist in at least six different servers. That gap is our opportunity.

---

## 1. First-party and vendor-hosted scholarly connectors

| Provider | Connector | Tools (as listed) | Auth / cost | Notes |
|---|---|---|---|---|
| **Anthropic** (developer = Anthropic) | **PubMed** (added Nov 2025) | `search_articles`, `get_article_metadata`, `find_related_articles`, `lookup_article_by_citation`, `convert_article_ids`, `get_full_text_article`, `get_copyright_status` | none | Full text only from PMC. [claude.com/connectors/pubmed](https://claude.com/marketplace/connectors/pubmed) |
| Anthropic | **bioRxiv/medRxiv** (Jan 2026) | `search_biorxiv_publications`, `search_by_funder`, `get_categories`, `get_preprint`, `search_published_articles`, `search_publisher_articles`, `search_preprints`, `get_content_statistics` | none | [claude.com/connectors/biorxiv](https://claude.com/marketplace/connectors/biorxiv). Also Clinical Trials and ChEMBL directory connectors. |
| Anthropic | **Claude Science** (beta 2026-06-30) "Literature Graph" featured connector | OpenAlex + arXiv (needs a free OpenAlex key) | Pro/Max/Team/Ent | Full-text chain: Unpaywall → Semantic Scholar → PMC → Crossref TDM links → publisher keys (Elsevier, Springer) → EZproxy → publisher page. Paced at 1 req/s per provider. Retrieved files are **saved into the session**. Ships a "literature review" skill. [docs](https://claude.com/docs/claude-science/literature-access), [connectors](https://claude.com/docs/claude-science/connectors-and-skills) |
| **Wiley** | **Scholar Gateway** (Nov 2025) | `search_wiley_fulltext`, `getUsageLimit` | sign-in, paid | Wiley content only. Returns passages with DOI links. [link](https://claude.com/marketplace/connectors/scholar-gateway) |
| **scite** (Research Solutions) | scite MCP `https://api.scite.ai/mcp` (in Claude directory Apr 2026; also ChatGPT, Copilot) | `search_literature` (directory listing) | OAuth; paid subscription or trial | Smart Citations (1.6B+ classified citation statements: supporting, contrasting, mentioning). This is the closest thing to claim-level evidence. [scite.ai/mcp](https://www.scite.ai/mcp) |
| **Consensus** | `https://mcp.consensus.app/mcp` (Claude; ChatGPT app store from 2026-04-06) | `search` (filters: `year_min/max`, `human`, `sample_size_min`, `sjr_max`) | OAuth; ~1000 searches/month, 20 papers per search | Returns title, abstract, authors, journal, url, citation_count. [SMU write-up](https://library.smu.edu.sg/topics-insights/what-if-claude-or-chatgpt-could-search-academic-databases-you-and-then-do-something), [apigene](https://apigene.ai/mcp/official/consensus) |
| **Elicit** | API + MCP (preview earlier; **public 2026-07-15**) | Search API (138M papers + ClinicalTrials.gov), Reports API, Systematic Review API (async; CSV/XLSX export at every stage with an audit trail) | API key; Pro+ tiers | Tool names not public. It is a "research engine as a tool" rather than a primitive. [blog](https://elicit.com/blog/the-elicit-api-and-mcp-powering-autonomous-research-engines) |
| **OurResearch / OpenAlex** | **Official OpenAlex MCP** `https://mcp.openalex.org/mcp` (registry `org.openalex/openalex` v0.5.0, Sep 2026) | 11 data tools: `search_works`, `search_entities`, `resolve_references` (≤25 citations per call), `list_citations`, `find_keywords`, `keyword_search`, `group_works`, `analyze_works`, `get_work`, `get_entity`, `read_docs`. Plus 5 profile-curation tools. | OAuth with a free account; per-user daily budget | **Every result includes the exact OQL query and a link to rerun it.** No full text, no bulk export. [help.openalex.org/access/connector](https://help.openalex.org/access/connector/) |
| **Ai2** | **Asta Scientific Corpus Tool** `https://asta-tools.allen.ai/mcp/v1` | `search_papers_by_relevance`, `search_paper_by_title`, `get_paper`, `get_paper_batch`, `get_citations`, `search_authors_by_name`, `get_author_papers`, `snippet_search` | `x-api-key` (request form) | The de facto official Semantic Scholar MCP. `snippet_search` covers ~285M passages from 12M full-text papers. A community "asta-skill" teaches tool routing. [asta-skill](https://sharedcontext.ai/skills/external/Agents365-ai/asta-skill) |
| **alphaXiv** | `https://api.alphaxiv.org/mcp/v1` (Anthropic directory partner) | Public read tools: `embedding_similarity_search`, `full_text_papers_search`, `agentic_paper_retrieval`, `get_paper_content`, `answer_pdf_queries`, `read_files_from_github`. Reportedly 18 tools in total, 8 of which write or delete (library). | OAuth | arXiv only. Native clients only, because of CORS (unverified). [agentman listing](https://agentman.ai/agentskills/connections/mcp-server/alphaxiv) |
| **Undermind** | `https://mcp.undermind.ai/mcp` (Claude directory, ChatGPT app, Codex) | 30+ tools: `launch_deep_search`, `search_papers`, `find_papers_by_author`, `lookup_papers_by_metadata`, `read_pdfs`, `get_paper_info`, `get_pdf_download_links`, `star_papers`, folders, workspaces, `get_orientation`, ... | OAuth (client-ID per agent) | **Async deep search averages ~2.9 min.** Persistent workspaces. Renders MCP Apps cards. [undermind.ai/mcp](https://www.undermind.ai/mcp) |
| **Hugging Face** | HF MCP server (`huggingface.co/mcp`) | Historically a built-in "Papers Semantic Search". It now centres on the `hf_fs` tool plus optional Spaces (e.g. an `hf-papers` Space). | HF token | Paper search is a side feature, not a bibliography tool. [docs](https://huggingface.co/docs/hub/hf-mcp-server) |
| **OpenAI** | No first-party scholarly MCP | — | — | **Prism** (Jan 2026) is a LaTeX workspace with built-in arXiv literature search and citation insertion, but it is not MCP. ChatGPT's app directory carries Consensus, scite, Undermind and Elicit. ChatGPT deep research or connectors historically need a `search` + `fetch` tool pair (54yyyu/zotero-mcp ships a `chatgpt-connector` toolset for this). [Prism](https://openai.com/index/introducing-prism/) |
| **Google** | None found | — | — | Google Scholar has no public API and no official MCP (stated in HasData's FAQ). Gemini CLI is only an MCP client. |

**Takeaway:** the first-party offerings are *search front-ends into proprietary indexes*. None of them holds the agent's own project state, apart from Undermind and alphaXiv folders and Elicit's systematic-review stages, all of which live in the cloud. The OpenAlex connector's habit of returning a reproducible query string and link is the best provenance pattern in this tier.

---

## 2. Open-source servers by category

### 2.1 arXiv and preprint servers

#### blazickjp/arxiv-mcp-server — ★3.2k, Python, pushed 2026-10-06
- **Transport:** stdio (default), HTTP, streamable-HTTP. Binds to 127.0.0.1 with DNS-rebinding protection. Listed in the official registry.
- **19 tools:** `search_papers`, `get_abstract`, `download_paper`, `list_papers`, `read_paper`, `get_paper_outline`, `read_paper_section`, `search_paper_text`, `get_paper_latex`, `list_paper_latex_sections`, `get_paper_latex_section`, `citation_graph` (Semantic Scholar, cached on disk), `export_citations` (BibTeX from arXiv metadata), `watch_topic`, `list_watches`, `check_alerts`, `unwatch_topic`, `semantic_search` (local embeddings, `[pro]` extra), `reindex`.
- **7 MCP prompts:** `research-discovery`, `deep-paper-analysis`, `summarize_paper`, `compare_papers`, `literature_review`, `literature-synthesis`, `research-question`.
- **Local state:** `~/.arxiv-mcp-server/papers`. This holds papers (HTML→Markdown, with a PDF fallback via `pymupdf4llm`), LaTeX caches, topic watches and the semantic index.
- **Output control:** responses are **bounded to 12,000 chars by default** (this was a breaking change from "full paper"). They include `content_length`, `returned_chars`, `next_start`, `is_truncated` and `next_retrieval` instructions. The intended loop is "paper ID → outline → one section → citations".
- **Rate limits:** a ~3 s arXiv delay, configurable timeouts, 2 retries with backoff, and special handling for HTTP 406.
- **Notable:** the README explicitly warns that paper text is **untrusted input (prompt injection)**. It reads from LaTeX source so equations come through intact.
- **Weaknesses:** arXiv only. The citation graph depends on Semantic Scholar quota. There is no notes or claims layer. Its "library" is a cache, not a curated bibliography.
- [github.com/blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server)

#### takashiishida/arxiv-latex-mcp — ★146, Python
- **Tools:** `get_paper_prompt` (flattened LaTeX), `get_paper_abstract`, `list_paper_sections`, `get_paper_section`.
- Stateless. A minimal, well-scoped "read the math correctly" tool. [repo](https://github.com/takashiishida/arxiv-latex-mcp)

#### andybrandt/mcp-simple-arxiv — ★201
- Search and read arXiv. Minimal and stateless. There are about ten more arXiv servers in the official registry (cyanheads, iowarp, pipeworx, ...).

#### bioRxiv / medRxiv
- Anthropic's connector (above) is the maintained option. The JackKuo666 bioRxiv/PubMed/GROBID server family is from early 2025 and has not been pushed since March 2025.

### 2.2 Multi-source aggregators

#### openags/paper-search-mcp — ★2.8k, Python, pushed 2026-10-02
- **Transport:** stdio, SSE, streamable-HTTP (with optional OAuth).
- **74 `@mcp.tool` registrations**, counted in `server.py`. There are 4 unified tools: `search_papers` (concurrent multi-source search with dedup), `download_with_fallback` (source-native → OpenAIRE/CORE/EuropePMC/PMC → Unpaywall → *opt-in* Sci-Hub), `extract_sections` and `get_references`/`get_citing_papers` (OpenAlex). The remaining ~70 are per-source `search_<src>` / `download_<src>` / `read_<src>_paper` triples across ~25 sources: arXiv, PubMed, bioRxiv, medRxiv, IACR, Semantic Scholar, Crossref, OpenAlex, PMC, CORE, Europe PMC, dblp, CiteSeerX, DOAJ, BASE, Zenodo, HAL, SSRN, OpenReview, OpenAIRE, Unpaywall, Google Scholar, IEEE, ACM, WoS and Scopus.
- **Local state:** downloaded PDFs plus an optional search cache (`PAPER_SEARCH_MCP_SEARCH_CACHE_ENABLED`). There is no library.
- **Output:** a uniform `Paper` dict (`title, authors, abstract, year, doi, url, pdf_url, source, raw_metadata`). PDF downloads are capped at 100 MiB, validated, and written atomically. Image-only PDFs are reported as errors (no OCR).
- **Rate limits:** retry with backoff. Google Scholar uses a 60 s → 15 min cooldown after a CAPTCHA. Per-source timeouts.
- **Weaknesses:** severe **tool-surface bloat** (74 schemas in context). `raw_metadata` makes responses verbose. The Google Scholar scraping is fragile. Sci-Hub support is a legal liability even as opt-in.
- [github.com/openags/paper-search-mcp](https://github.com/openags/paper-search-mcp)

#### Liyux3/scholar-mcp — ★3 (new), Python, pushed 2026-09-19. **The closest design to our goals.**
- **Transport:** stdio and streamable HTTP. In the official registry. Ships as a Claude Code and Codex plugin with a bundled "Deep Research" skill.
- **Core profile, 6 tools:** `search_papers`, `paper_info` (detail + citations + references in one selective call), `recommend_papers`, `search_authors`, `read_paper`, `download_paper`. **Research profile adds** (`SCHOLAR_MCP_EXTENSIONS=research`): `build_paper_graph` (a bounded citation graph with PageRank, bridges and Mermaid output) and `paper_library` (collections, FTS search, notes, tags, PDFs, Markdown vault export). A **resource `scholar://status`** reports source health "without occupying the tool surface".
- **Retrieval:** a federated semantic plus keyword fan-out across OpenAlex, arxiv.gg, Exa, S2 snippets, Crossref, arXiv, OpenReview, PubMed, EuropePMC, DBLP, INSPIRE, CORE and others. Each source gets a time budget (`SCHOLAR_SOURCE_BUDGET_S`, 8 s). **Results are canonicalized across DOI/arXiv/S2/OpenAlex/PubMed/OpenReview IDs**, so duplicates merge metadata and keep independent source evidence. Reranking uses Qwen or local FlashRank. It **publishes a LitSearch benchmark** (R@5 0.62 vs Exa 0.52).
- **Local state:** `~/.scholar-mcp/`, containing a SQLite (WAL + FTS5) authority, PDFs, JSONL snapshots and an Obsidian vault projection. It has dry-run-by-default sync to Zotero and Notion.
- **Output:** concise YAML text plus MCP `structuredContent`. `debug=true` adds per-source yield and latency. Degradation is reported explicitly. Credentials are redacted from errors. `read_paper` reads pages 1–10 by default, with `pages=` for continuation and `visual="Figure 3"` for page crops.
- **Weaknesses:** a tiny user base. The library is paper-level (notes and tags only), with no claim or evidence objects and no agent attribution. Reranking quality depends on a paid key.
- [github.com/Liyux3/scholar-mcp](https://github.com/Liyux3/scholar-mcp)

#### YGao2005/scholar-feed-mcp — ★12, TypeScript (hosted backend)
- **27 tools** over a hosted index of 600k CS/AI papers with 22M citation edges. Examples: `search_papers` (with `anchor_paper_id`, `scope_to_citations_of`, `novelty_min`), `get_paper` (batch + BibTeX), `get_citations`, `fetch_fulltext` (by named section, up to 8 papers per call), `get_foundational_lineage`, `check_drift` ("is my method superseded?"), `find_gaps`, `ask_library`, `save_paper`, `annotate_paper` ("record your verdict … what a later session reads instead of re-deriving"), collections and watches.
- Keyless use is limited to 200 calls/month. The library is stored in the cloud.
- **Notable:** it explicitly designs for **cross-session agent memory of verdicts**. That memory is SaaS-bound. [repo](https://github.com/YGao2005/scholar-feed-mcp)

#### genomoncology/biomcp — ★647, Rust, very active
- A single CLI and MCP "entity grammar" over about 70 biomedical sources. `search article` deduplicates across PubMed, PubTator3 and Europe PMC. Output is JSON with `_meta.next_commands` (suggested next calls) and `_meta.section_sources` (provenance).
- This is a good example of **one grammar instead of N tools**. [repo](https://github.com/genomoncology/biomcp)

### 2.3 Semantic Scholar / OpenAlex / Crossref / DBLP / OpenReview / Google Scholar

| Server | ★ / lang / last push | Tools | Notes |
|---|---|---|---|
| **zongmin-yu/semantic-scholar-fastmcp-mcp-server** | 165 / Py / 2026-03 | 16: relevance/bulk/title search, details, batch (≤1000), authors, autocomplete, snippet search, citations, references, author search/details/papers, single- and multi-paper recommendations | `fields` parameter. Adaptive rate limiting: 1 rps search/batch with a key, 100 req/5 min without. [repo](https://github.com/zongmin-yu/semantic-scholar-fastmcp-mcp-server) |
| **smaniches/semantic-scholar-mcp** | 23 / Py / 2026-10-05 | 14, namespaced `semantic_scholar_*`: `search_papers`, `get_paper`, `search_authors`, `get_author`, `recommendations`, `multi_recommend`, `bulk_papers`, `bulk_search`, `export_citation`, `match_paper`, `paper_authors`, `author_batch`, `snippet_search`, `status` | `response_format` (markdown or JSON). Four tiered field sets (`…_LITE` etc.). Pre-flight ID validation across 7 ID types. 5-min in-memory TTL cache. Jittered retry that honours `Retry-After`. Typed error hierarchy. SLSA/SBOM supply-chain provenance. [repo](https://github.com/smaniches/semantic-scholar-mcp) |
| JackKuo666/semanticscholar-MCP-Server | 86 / Py / 2025-03 (stale) | search, details, citations/references | Early-generation and unmaintained. |
| **cyanheads/openalex-mcp-server** | 16 / TS / 2026-09-30 | 5: `openalex_search_entities`, `openalex_analyze_trends` (group-by), `openalex_resolve_name` (DOI/ORCID/ROR/PMID/ISSN → ID), `openalex_get_citation_graph` (one hop), `openalex_describe_fields` | **64 KB response budget with an `over_budget` flag.** Curated default `select` fields. Cursor pagination. HTTP→MCP error mapping. Provenance reports cost and remaining budget. Pluggable storage (in-memory, filesystem, Supabase, Cloudflare). [repo](https://github.com/cyanheads/openalex-mcp-server) |
| **cyanheads/crossref-mcp-server** | 3 / TS / 2026-09-24 | 7: `crossref_get_work`, `crossref_search_works`, `crossref_get_references`, `crossref_search_journals`, `crossref_search_funders`, `crossref_get_member`, `crossref_get_prefix` | Exposes `updatedBy` (retractions, corrections, via Retraction Watch) and `relations` (**preprint ↔ published** version links). `authorLimit` with `authorCount`. `fields` whitelist. Polite-pool `mailto`. Hosted instance available. [repo](https://github.com/cyanheads/crossref-mcp-server) |
| **szeider/mcp-dblp** | 45 / Py / 2026-09-23 | 7: `search`, `fuzzy_title_search`, `get_author_publications`, `get_venue_info`, `add_bibtex_entry`, `export_bibtex`, `set_dblp_mirror` | **"The server renders every entry from DBLP's data; the model chooses only the citation key and never edits the entry."** The BibTeX collection is held *in session* and then written to a `.bib` file. Accompanied by a paper at AI4SC @ AAAI-26. [repo](https://github.com/szeider/mcp-dblp) |
| **OpenCodice-Research/openreview-mcp** | 2 / Py / 2026-05 | 11: `openreview_list_venues`, `_venue_stats`, `_search_submissions`, `_get_submission`, `_search_by_author`, `_get_reviews`, `_get_meta_review`, `_get_rebuttal`, `_get_decision`, `_get_profile`, `_aggregate_weaknesses` | Optional OpenReview login. Exposes peer-review signal (scores, weaknesses, decisions), which is valuable for ML. [glama](https://glama.ai/mcp/servers/OpenCodice-Research/openreview-mcp) |
| anyakors/openreview-mcp-server | 13 / Py / 2026-01 | `search_user`, `get_user_papers`, `get_conference_papers`, `search_papers`, `export_papers` (JSON + PDF download to `./openreview_exports`) | `format: summary|detailed`. Keyword match scoring. [repo](https://github.com/anyakors/openreview-mcp-server) |
| JackKuo666/Google-Scholar-MCP-Server | 412 / Py / **2025-03 (stale)** | `search_google_scholar_key_words`, `search_google_scholar_advanced`, `get_author_info` | Scrapes HTML. CAPTCHA-prone and against ToS. |
| HasData/google-scholar-mcp | 10 / JS / active | 3 very long names, e.g. `hasdata_google_scholar_scholar_getScholarSearchResults` | Paid scraping API. Supports cited-by (`cites`), versions (`cluster`) and citation formats including a BibTeX link. |

### 2.4 Biomedical (PubMed / Europe PMC)

#### cyanheads/pubmed-mcp-server — ★154, TypeScript, pushed 2026-10-04. **A best-in-class "single-source" design.**
- **11 namespaced tools:** `pubmed_search_articles` (full query syntax, ≤1000 results, optional `summaryCount` abstracts), `pubmed_fetch_articles` (≤200 PMIDs), `pubmed_fetch_fulltext` (PMC → Europe PMC JATS → Unpaywall chain), `pubmed_europepmc_search` (cursor), `pubmed_europepmc_fetch`, `pubmed_format_citations` (APA, MLA, **BibTeX**, RIS, Vancouver; ≤50), `pubmed_find_related` (similar / cited-by / references, with EPMC and OpenAlex fallback), `pubmed_spell_check`, `pubmed_lookup_mesh`, `pubmed_lookup_citation` (partial citation → PMID), `pubmed_convert_ids`.
- **Resource** `pubmed://database/info`. **Prompt** `research_plan`.
- **Output control:** `maxResponseCharacters` **defers overflow to `deferred.ids`/`deferred.records`** instead of truncating silently. `maxCharacters`, `sections` and `overflowMode` control full text. A `truncation` field reports what was cut.
- **Rate limits:** a queue-paced 3 or 10 rps depending on whether an NCBI key is set. Max concurrency 8, 6 retries, and a 60 s total deadline.
- **Design:** graceful per-item failure in batches. Forgiving identifiers (zero-padded PMIDs, case-insensitive DOIs). **Every response carries source labels and the effective query.**
- [github.com/cyanheads/pubmed-mcp-server](https://github.com/cyanheads/pubmed-mcp-server). Hosted at `pubmed.caseyjhand.com/mcp`.

Others: andybrandt/mcp-simple-pubmed, vitorpavinato/ncbi-mcp-server (caching, MeSH), gqy20/article-mcp (Europe PMC + others), a Europe PMC server inside a "Bio MCP monorepo", and the Pipeworx gateway servers.

### 2.5 Reference managers

#### 54yyyu/zotero-mcp — ★5.3k, Python, pushed 2026-10-04. The most popular literature MCP overall.
- **38 tools in the default profile**, plus optional toolsets via `ZOTERO_MCP_TOOLSETS`: `scite`, `duplicates`, `discovery`, `feeds`, `relations`, `libraries`, `search-admin`, `pdf-geometry`, `chatgpt-connector`.
- **Tools include:** `zotero_search_items`, `_advanced_search`, `_semantic_search`, `_search_by_tag`, `_search_by_citation_key`, `_get_item_metadata`, `_get_item_fulltext`, `_read_pdf_pages`, `_get_pdf_outline`, `_get_annotations`, `_create_annotation`, `_synthesize_annotations`, `_manage_note`, `_add_by_doi/_url/_isbn/_bibtex/_csl_json/_from_file`, `_attach_file`, `_find_duplicates`, `_merge_duplicates`, `_update_item`, `_manage_collections`, `_add_item_relation`, `_write_capabilities`, ...
- **Local vs web:** reads directly from `zotero.sqlite` or the local API. Writes go through a running Zotero 10+ (local) or the Web API (`ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID`).
- **Semantic search:** ChromaDB with local sentence-transformers, OpenAI, Gemini or Ollama embeddings. Stored locally.
- **Key design data point:** the README measures **the MCP default profile at 13,448 tokens on every request, versus 98 tokens for an equivalent agent skill + `zotero-cli` (1,368 once loaded)**. It recommends the skill/CLI path for coding agents that have a shell.
- [github.com/54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp)

#### kujenga/zotero-mcp — ★161, Python
- 3 tools: `zotero_search_items`, `zotero_item_metadata`, `zotero_item_fulltext`. Local or Web API. Returns formatted text. A useful minimal baseline. [repo](https://github.com/kujenga/zotero-mcp)

#### cookjohn/zotero-mcp — ★1.2k, TypeScript
- A **Zotero plugin with an embedded streamable-HTTP MCP server** (no separate process). 29 tools: search, annotations, collections, semantic search (3), cached full-text DB, and writes (notes, tags, metadata, create, import by identifier, trash). [repo](https://github.com/cookjohn/zotero-mcp)

#### oscardvs/zoteus — ★54, TypeScript, pushed 2026-10-01
- **34 tools** (`zotero_*`) with structured outputs, MCP resources and prompts, and a generated tool tree for the code-execution-with-MCP pattern.
- **Hybrid BM25 + vector search** over SQLite FTS5. Embeddings are local (transformers.js), Ollama, OpenAI or Gemini. Indexing of your own notes and annotations is on by default; full-text indexing is opt-in.
- **`zotero_get_fulltext` returns passages with character offsets, the nearest heading and a page locator.**
- `zotero_annotate` anchors highlights by quoted text. `zotero_scholar` covers OpenAlex/Crossref references, citers and related works, and flags retractions and which results are already in the library.
- **`zotero_evidence_table` renders quotations that "cannot drift from the passage they cite."**
- CSL formatting via citeproc-js. Imports BibTeX, RIS and CSL-JSON with duplicate checks.
- Writes are versioned with optimistic locking. Deletion is gated (`ZOTEUS_ALLOW_DELETE`).
- Ships 4 skills: quote with pages, evidence tables, cite, tidy. A hosted OAuth remote is also offered. [repo](https://github.com/oscardvs/zoteus)

#### OrgMentem/zotio — ★2, Go, pushed 2026-10-05. The best "trust layer" ideas, despite few stars.
- A CLI (140 commands) plus an agent skill plus `zotio-mcp`. The MCP side is a **command-orchestration facade: just `command_search` and `command_run`**, not one tool per endpoint (see ADR 0001).
- Reads are local and free. **Writes are preview-first** with a plan/result envelope, `--max-changes` gates (50 in agent mode) and `--allow-destructive`. **An append-only journal supports `journal undo <run-id>`.**
- **Every result is provenance- and freshness-tagged.** "zotio **never calls an LLM**."
- `library health --for citation|systematic-review|vault` checks citekey conflicts, duplicates, missing fields and retractions (Retraction Watch via Crossref). **`items bibcheck thesis.tex`** verifies every `\cite{}` resolves. CI exit codes (11 = quality gate failed, 12 = stale mirror).
- A `capabilities` registry tags each command with `operation`, `data_sources`, `write_target`, `destructive` and `requires`.
- [repo](https://github.com/OrgMentem/zotio)

Other Zotero servers: dvdsosa/zotero-native-mcp (local API, BibTeX and CSL export, saved searches), herbertkokholm/cite-caddy (multi-tenant), kaliaboi/mcp-zotero (cloud), menyoung/paperqa-mcp-server (PaperQA2 over a Zotero storage folder, using OpenAI internally), and dengls24/annota (writes structured reading notes into Zotero).

#### pallaprolus/mendeley-mcp — ★40, Python, pushed 2026-09-28
- 18 tools (`mendeley_*`): library search, get, list, folders, catalog search, `get_by_doi`, add/update/delete documents, folder CRUD, `get_annotations`, **`export_bibtex`**, `get_file_content`, `get_document_text`.
- **Resources** `mendeley://library/recent` and `mendeley://library/folders`. OAuth2 tokens are kept in the OS keyring. [repo](https://github.com/pallaprolus/mendeley-mcp)

### 2.6 Bibliography integrity: BibTeX, verification, retractions

| Server | ★ | Tools | Design highlights |
|---|---|---|---|
| **Hylouis233/bibverify** | 99 / Py / pushed today | DOI→BibTeX, verify a `.bib`, reports, non-destructive merge | Checks 9 providers (Crossref, OpenAlex, S2, PubMed, EPMC, CORE, DBLP, arXiv, bioRxiv). Identifier-first lookup, then multi-signal scoring (title, authors, year, venue, pages) with thresholds of 0.86 for a match and 0.68 for ambiguous. **A DOI that resolves to a different title becomes `identifier_conflict`.** Distinct statuses: `matched`, `no_match`, `ambiguous`, `rate_limited`, `auth_error`, ... Byte-preserving backups. SQLite cache of successes only. Honours `Retry-After`. [repo](https://github.com/Hylouis233/bibverify) |
| **mlava/scholar-sidekick-mcp** | 10 / TS | 7: `verifyCitation`, `auditBibliography`, `resolveIdentifier`, `formatCitation` (10k+ CSL styles), `exportCitation` (BibTeX, RIS, CSL-JSON, ...), `checkRetraction`, `checkOpenAccess` | Detects the "real DOI + invented title" fabrication pattern. All tools are `readOnlyHint: true`. **A provenance block (`requestId`, `formatter`, `styleUsed`, `warnings`) is attached to every response.** Hosted API with a free tier. [repo](https://github.com/mlava/scholar-sidekick-mcp) |
| Vashistht/harcx-mcp | 1 | `verify_citations` (a `.bib` path or inline), `verify_urls` | Checks against S2 and DBLP with fuzzy author matching. |
| wedo911/citeguard | 1 | Retraction, correction and expression-of-concern check via Crossref `updated-by` | Also packaged as a CLI and a GitHub Action. Persistent cache. |
| xiaofei03/cite-rag-mcp | 1 | `run_reference_workflow` with **4 isolated modes** (`retrieve_only`, `import_only`, `export_only`, `full_pipeline`), `export_item_citekey` (Better BibTeX), `audit_zotero_metadata_by_citekeys`, `build_citekey_generation_bundle`, ... | "**Citekeys must come from Zotero, not from the model.**" Tracks a verification level per item: metadata-only, abstract-verified or fulltext-verified. |
| Official **OpenAlex** `resolve_references` | — | ≤25 citations per call | Vendor-backed reference resolution. |

### 2.7 Paper reading and parsing (PDF → Markdown)

| Server | ★ | Tools / behaviour | Notes |
|---|---|---|---|
| **docling-project/docling-mcp** | 768 / Py | Convert PDF → DoclingDocument JSON or Markdown. Document generation. LlamaIndex/LlamaStack/Milvus RAG tools. | stdio, SSE and HTTP. **LRU in-memory cache of 10 docs.** Image modes (placeholder, embedded, referenced) to control output size. [repo](https://github.com/docling-project/docling-mcp) |
| microsoft/markitdown `markitdown-mcp` | (markitdown 189k) | **One tool:** `convert_to_markdown(uri)` (http, https, file, data URIs) | The simplest possible surface. No size control. [pkg](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp) |
| marker-pdf / MinerU MCPs | various | e.g. the official MinerU Open MCP, linxule/mineru-mcp, mffrydman/doc-reading-mcp (marker + pandoc) | Better equations and tables. Heavy GPU/ML dependencies or a paid API. |
| JackKuo666 GROBID MCP | small, stale | GROBID header, reference and fulltext TEI extraction | **The only GROBID-based MCP found.** Nobody exposes GROBID's *reference parsing* as a dedup or linking primitive. |
| **notwhiteblank/scholar-rag-mcp** | 4 / Py | 11 tools: `list_kbs`, `create_kb` (async job), `delete_kb` (**two-phase**), `add_document` (async), `remove_document`, `get_document` (outline first), `get_document_text` (**paginated, hard caps**), `list_documents`, `search_documents`, `search_chunks`, `get_job` | MinerU parsing, Qdrant + reranker, local OpenAI-compatible models. A good async-job pattern. [repo](https://github.com/notwhiteblank/scholar-rag-mcp) |
| ElliotPadfield/unpaywall-mcp | 12 / TS | `unpaywall_get_by_doi`, `unpaywall_search_titles`, `unpaywall_get_fulltext_links`, `unpaywall_fetch_pdf_text` (configurable truncation) | Needs `UNPAYWALL_EMAIL`. Stateless. |

### 2.8 Workspaces, "research memory" and deep research

- **WenyuChiou/research-hub** (★62, Py, active). Zotero + Obsidian + NotebookLM orchestration exposed through a CLI, MCP, REST and a dashboard. It has "clusters" (topic-scoped projects), "crystals" (cached canonical answers as Markdown), and `memory emit` producing **structured memory: entities, claims, methods**. Resumable HITL workflow tools (`workflow_*`), cascade-delete preview, and an LLM-CLI "fit check" to filter off-topic papers. Single-user and heavyweight (browser automation for NotebookLM). [repo](https://github.com/WenyuChiou/research-hub)
- **modelcontextprotocol/servers `memory`**: the reference knowledge-graph memory (entities, relations, observations in a JSONL file). It is generic, and people reuse it for literature notes, but it has no paper or citation schema and no provenance. [link](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)
- **pminervini/deep-research-mcp** (★112). A unified MCP over OpenAI Deep Research, Gemini Deep Research, Ai2 DR-Tulu, HF Open Deep Research and Tavily. Runs take hours (it recommends `tool_timeout_sec` = 14400). It returns a report, not structured evidence. [repo](https://github.com/pminervini/deep-research-mcp)
- **GPT Researcher MCP**, plus assorted "deep-research" servers (Ozamatash, reading-plus-ai, ...). These are web-centric and generate reports.
- **Vendor research engines as tools:** Undermind `launch_deep_search`, Elicit Reports and Systematic Review, Consensus. All are async or quota-bound and cloud-stored.

---

## 3. Comparison table (notable servers)

Legend for *State*: **none** = stateless. **cache** = local cache of downloads or responses only. **lib** = curated persistent library. **cloud** = state lives in the vendor account.

| Server | Lang | Transport | # tools | Sources | State | Auth | Full text | Output / size control | ★ / activity |
|---|---|---|---|---|---|---|---|---|---|
| [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | Py | stdio/HTTP | 19 + 7 prompts | arXiv, S2 (graph) | cache + watches + local vector index | opt. S2 key | HTML→MD, LaTeX sections, PDF fallback | 12k-char chunks, `next_start`, `is_truncated` | 3.2k / daily |
| [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) | Py | stdio/SSE/HTTP | **74** | ~25 sources | cache (PDFs, search cache) | many optional keys | download + text extraction, OA fallback chain | per-source `max_results`; verbose `raw_metadata` | 2.8k / active |
| [Liyux3/scholar-mcp](https://github.com/Liyux3/scholar-mcp) | Py | stdio/HTTP | 6 core + 2 research + 1 resource | ~15 sources, canonical merge | **lib** (SQLite + FTS5 + vault) | all optional | page-ranged PDF → MD, figure crops | YAML text + structuredContent; `debug` | 3 / active |
| [54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp) | Py | stdio | 38 default (+ toolsets) | Zotero, scite | **lib** (Zotero) + Chroma index | local or Web API key | PDF text, page images, annotations | toolset profiles; CLI `--json` | 5.3k / daily |
| [cookjohn/zotero-mcp](https://github.com/cookjohn/zotero-mcp) | TS | HTTP (in-Zotero plugin) | 29 | Zotero | **lib** | none (local) | cached full text | — | 1.2k / active |
| [oscardvs/zoteus](https://github.com/oscardvs/zoteus) | TS | stdio/HTTP+OAuth | 34 + prompts + resources | Zotero, OpenAlex, Crossref | **lib** + SQLite FTS / vector index | optional key | passages with page and char offsets, OCR, page images | structured outputs | 54 / active |
| [OrgMentem/zotio](https://github.com/OrgMentem/zotio) | Go | stdio | **2** (facade over 140 cmds) | Zotero, Crossref, OpenAlex, S2, Unpaywall, OpenCitations | **lib** mirror + write journal | key for writes | via Zotero | `--select` fields, `--compact`, provenance + freshness | 2 / active |
| [kujenga/zotero-mcp](https://github.com/kujenga/zotero-mcp) | Py | stdio | 3 | Zotero | lib (Zotero) | local or Web | PDF text | plain text | 161 |
| [pallaprolus/mendeley-mcp](https://github.com/pallaprolus/mendeley-mcp) | Py | stdio | 18 + 2 resources | Mendeley | lib (cloud) | OAuth2 (keyring) | attached PDF text | — | 40 |
| [cyanheads/pubmed-mcp-server](https://github.com/cyanheads/pubmed-mcp-server) | TS | stdio/HTTP | 11 + resource + prompt | PubMed, EPMC, Unpaywall, OpenAlex | none (pluggable store) | opt. NCBI key | PMC/EPMC JATS, Unpaywall | `maxResponseCharacters` with deferred IDs, `truncation` report | 154 / daily |
| [cyanheads/openalex-mcp-server](https://github.com/cyanheads/openalex-mcp-server) | TS | stdio/HTTP | 5 | OpenAlex | none | opt. key | — | 64 KB budget, `over_budget`, `select`, cursor | 16 |
| Official OpenAlex (`mcp.openalex.org`) | — | HTTP | 11 + 5 | OpenAlex | cloud (budget) | OAuth | none | OQL + rerun link | Sep 2026 |
| [cyanheads/crossref-mcp-server](https://github.com/cyanheads/crossref-mcp-server) | TS | stdio/HTTP | 7 | Crossref | none | `mailto` | — | `fields`, `authorLimit`, cursor | 3 |
| [smaniches/semantic-scholar-mcp](https://github.com/smaniches/semantic-scholar-mcp) | Py | stdio/HTTP | 14 | S2 | 5-min memory cache | opt. key | snippets | `response_format` md/json; tiered field sets | 23 |
| [zongmin-yu/semantic-scholar-fastmcp](https://github.com/zongmin-yu/semantic-scholar-fastmcp-mcp-server) | Py | stdio | 16 | S2 | none | opt. key | snippets | `fields` | 165 |
| Ai2 Asta | — | HTTP | 8 | S2 corpus | none | API key | 500-word snippets | field selection, date filters | official |
| [szeider/mcp-dblp](https://github.com/szeider/mcp-dblp) | Py | stdio | 7 | DBLP | session BibTeX buffer → `.bib` | none | — | — | 45 |
| [OpenCodice openreview-mcp](https://github.com/OpenCodice-Research/openreview-mcp) | Py | stdio | 11 | OpenReview | none | opt. login | PDF URL | — | 2 |
| [genomoncology/biomcp](https://github.com/genomoncology/biomcp) | Rust | stdio/HTTP | 1 grammar | ~70 biomedical | none | mostly none | articles via PubTator/EPMC | `_meta.next_commands`, `section_sources` | 647 / daily |
| [Hylouis233/bibverify](https://github.com/Hylouis233/bibverify) | Py | stdio | few | 9 providers | SQLite cache, backups | opt. keys | — | txt/json/jsonl/csv reports | 99 |
| [mlava/scholar-sidekick-mcp](https://github.com/mlava/scholar-sidekick-mcp) | TS | stdio/HTTP | 7 (read-only) | hosted resolver, Crossref, Unpaywall | none | opt. key | — | provenance block | 10 |
| [docling-mcp](https://github.com/docling-project/docling-mcp) | Py | stdio/SSE/HTTP | ~10 | local files | LRU cache (10) | none | layout-aware conversion | image modes | 768 |
| [scholar-rag-mcp](https://github.com/notwhiteblank/scholar-rag-mcp) | Py | stdio | 11 | local PDFs | **lib** (Qdrant KBs) | local models | MinerU | paginated, hard caps, async jobs | 4 |
| [YGao2005/scholar-feed-mcp](https://github.com/YGao2005/scholar-feed-mcp) | TS | stdio → hosted | 27 | own CS index (600k) | cloud lib + verdict notes | opt. key (200/mo free) | by section, ≤8 papers | `fields`, `verbose` | 12 |
| [WenyuChiou/research-hub](https://github.com/WenyuChiou/research-hub) | Py | stdio + REST | many | arXiv, S2, PubMed, Crossref → Zotero/Obsidian/NotebookLM | **lib** + claims memory + crystals | Zotero key | via NotebookLM | Markdown crystals | 62 |
| Undermind (hosted) | — | HTTP | 30+ | S2-based corpus | cloud workspaces | OAuth | `read_pdfs` | MCP Apps cards; async deep search | commercial |
| alphaXiv (hosted) | — | HTTP | 6 read (18 total) | arXiv + GitHub | cloud library | OAuth | paper text, PDF QA | — | commercial |
| scite / Consensus / Elicit / Scholar Gateway | — | HTTP | 1–few | proprietary | cloud | OAuth / key, paid | snippets (scite, Wiley) | quota-limited | commercial |

---

## 4. Synthesis

### 4.1 The common tool surface (what nearly every server exposes)

1. **`search(query, filters)`.** Keyword or semantic search with year, venue and author filters and a `limit`/`max_results`. It returns a list of paper records (title, authors, year, venue, ids, abstract, url, citation count).
2. **`get_paper(id)` / batch get.** Metadata by DOI, arXiv ID, PMID or a source-native ID. Better servers accept any ID type and normalize it.
3. **`citations(id)` / `references(id)`.** One hop, usually from Semantic Scholar or OpenAlex, sometimes Crossref.
4. **`download` / `read_fulltext(id)`.** PDF or HTML to text or Markdown, with an OA fallback chain (source → PMC/EPMC → Unpaywall → CORE/OpenAIRE).
5. **`export/format_citation(ids, style)`.** Usually BibTeX, sometimes CSL styles, RIS or CSL-JSON.
6. **Authors** (`search_authors`, `author_papers`) and **recommendations** (S2 recommendations, "related").
7. Reference-manager servers add: **collections/tags/notes CRUD, annotations, add-by-DOI, semantic search over the library, duplicate find/merge.**

Configuration is uniform too: optional API keys through env vars (S2, OpenAlex, NCBI, CORE, Unpaywall email), and stdio by default with streamable HTTP as an option.

### 4.2 Gaps: what almost none of them do

| Gap | Who comes closest | Status |
|---|---|---|
| **Project scoping** (a literature corpus tied to *this* repo or paper, living with the code) | Zotero collections; research-hub "clusters"; Undermind workspaces; scholar-rag KBs | Nobody scopes to a repo directory or a version-controlled file. All use a global user library or the cloud. |
| **Persistent, provenance-tracked notes** (who wrote it, when, from which passage and tool call) | scholar-feed `annotate_paper` (cloud); Liyux3 library notes; Zotero notes via zotero-mcp/zoteus; zotio journal for *writes* | Notes are free text attached to a paper. No source passage, no author or agent identity, no session ID, no confidence. |
| **Claim/evidence graph** (claim ↔ supporting or contradicting passages ↔ papers) | scite Smart Citations (proprietary, global, not *your* claims); research-hub `memory emit` (entities, claims, methods); zoteus evidence tables (locked quotations with page locators) | **No open server stores the user's or agent's own claims with typed evidence links.** |
| **Claim verification** ("does paper X actually support sentence Y in my draft?") | scite; Consensus; zoteus passage retrieval | Existence and metadata verification is well covered (bibverify, scholar-sidekick `verifyCitation`, harcx, OpenAlex `resolve_references`). *Semantic support* checking is essentially absent in open source. |
| **Cross-source dedup / canonical identity** (DOI ↔ arXiv ↔ S2 ↔ OpenAlex ↔ PMID ↔ OpenReview; preprint ↔ published version) | Liyux3 (canonicalization across 6 ID systems); paper-search-mcp `search_papers` dedup; biomcp article dedup; Crossref `relations` exposed by cyanheads | It is ad hoc per query. No persistent canonical-work table with merge history. Preprint-vs-venue preference for BibTeX is not handled. |
| **Citation snowballing as a managed process** (frontier, depth, visited set, screened-in/out with reasons) | Liyux3 `build_paper_graph` (bounded, PageRank); scholar-feed `get_foundational_lineage`, `find_gaps`; "academic-search" random walk | Everyone else exposes only one-hop citation calls. **No server keeps snowball state across sessions.** |
| **Screening / inclusion decisions (PRISMA-style)** | Elicit Systematic Review API (cloud, paid); zotio `library health --for systematic-review`; research-hub HITL `workflow_*` decisions | Absent in the open-source search servers. |
| **Multi-agent attribution** (several agents or subagents writing to the same store; who added what) | zotio journal (per run, not per agent); Undermind/alphaXiv workspaces (per user) | **None.** No `agent_id` or `session_id` fields, no locking semantics for concurrent agents (zoteus has optimistic locking against Zotero versions only). |
| **LaTeX-ready BibTeX export with stable citekeys** | mcp-dblp (server-rendered entries, model picks key only); zotio `bibcheck thesis.tex`; cite-rag (Better BibTeX keys); arxiv-mcp `export_citations`; mendeley `export_bibtex` | There is no citekey policy, no sync of `refs.bib` with `\cite{}` usage in the manuscript, no "prefer the published venue over arXiv" rule, and no guarantee that keys stay stable across re-exports. mcp-dblp's buffer is session-only. |
| **Retraction / correction awareness** | crossref `updatedBy`, citeguard, zotio, scholar-sidekick, zoteus | Available, but as separate tools. It is rarely applied automatically when a paper enters the library or the `.bib`. |
| **Context-budget-aware outputs** | arxiv-mcp (12k chunks), cyanheads (deferred IDs, 64 KB budget, `over_budget`), scholar-rag (hard caps), smaniches (tiered field sets), Liyux3 (concise YAML + structured) | Most servers still return full upstream JSON or whole papers. None lets the caller set a token budget per call, and few offer an "IDs-only" mode. |
| **Async long-running jobs** | Undermind deep search, Elicit reports, scholar-rag `get_job`, deep-research-mcp | The MCP *Tasks* primitive (async jobs, spec 2025-11-25) is essentially unused. Jobs are hand-rolled. |
| **Retrieval quality evaluation** | Liyux3 (LitSearch R@k), Elicit (BioASQ claim) | Almost no server publishes evals. |
| **Security against prompt injection from paper text** | arxiv-mcp README warning; Liyux3 credential redaction | No server marks paper text as untrusted (e.g. fenced, labelled) in the response itself. |

### 4.3 Design patterns: good vs bad for agent ergonomics

**Good (copy these):**

1. **A small, intent-level core surface with opt-in profiles.** Examples: Liyux3 (6 core + `research` extension), 54yyyu `ZOTERO_MCP_TOOLSETS`, official OpenAlex (11 data tools), Asta (8), cyanheads OpenAlex (5). *Measured cost:* 38 Zotero tools take **13.4k tokens per request**. A skill + CLI costs ~100 tokens idle (54yyyu). zotio pushes the idea to the extreme with 2 facade tools (`command_search`/`command_run`). Claude Code's deferred tool loading lowers the cost but does not remove it.
2. **Namespaced, verb-first names:** `pubmed_search_articles`, `crossref_get_work`, `zotero_get_fulltext`, `openalex_resolve_name`. Avoid vendor-generated monsters like `hasdata_google_scholar_scholar_getScholarSearchResults`.
3. **Merge related calls into one selective tool.** For example, `paper_info(id, include=[citations, references])` (Liyux3), or `get_paper` handling batch lookup and BibTeX (scholar-feed). This beats separate `get_citations`/`get_references`/`get_details`.
4. **Bounded outputs with explicit continuation and truncation metadata.** Examples: `next_start`/`is_truncated` (arxiv-mcp); `deferred.ids` plus a `truncation` report (cyanheads PubMed); an `over_budget` flag (cyanheads OpenAlex). Use **outline first, then section** reading (arxiv-mcp, arxiv-latex-mcp, scholar-rag) and page ranges (Liyux3).
5. **Curated default fields plus an explicit `fields`/`select` whitelist** that errors on invalid names (cyanheads, smaniches tiered field sets), with a `describe_fields` helper.
6. **Provenance on every response:** source labels plus the effective query (cyanheads PubMed); the OQL query and a rerun link (official OpenAlex); formatter and style metadata (scholar-sidekick); per-source yield and degradation reporting (Liyux3); freshness stamps (zotio).
7. **Distinguish failure modes.** Use `matched / no_match / ambiguous / rate_limited / auth_error` (bibverify) and per-item results in batches (cyanheads). "No result" must never be confused with "rate-limited".
8. **The server owns bibliographic truth.** The LLM picks a citekey but never writes or edits entry fields (mcp-dblp). Citekeys come from the store, not the model (cite-rag). Quotations are locked to their passages (zoteus evidence tables).
9. **Preview-first, journaled, gated writes.** Plan/apply envelopes, max-change caps, destructive opt-in and undo (zotio). Two-phase deletes (scholar-rag). Delete disabled by default (zoteus). Mark tools `readOnlyHint`/`destructiveHint` (scholar-sidekick).
10. **Forgiving identifier input:** accept DOI, arXiv, PMID, PMCID, S2, OpenAlex, URL forms, case and zero-padding variants, and validate them before any request (cyanheads, smaniches).
11. **Polite API citizenship as a built-in:** queue pacing, `Retry-After`, `mailto`/polite pool, caching successes but not failures (bibverify), and per-source time budgets so one slow source does not block the rest (Liyux3).
12. **Resources for status and reference data, prompts or skills for workflows:** `scholar://status`, `pubmed://database/info`, `mendeley://library/recent`. Prompts: arxiv-mcp's 7 workflow prompts and the PubMed `research_plan`. Bundled skills: Liyux3 Deep Research, zoteus' 4 skills, asta-skill.
13. **Structured output alongside compact text** (MCP `structuredContent`/`outputSchema` plus concise YAML or Markdown text). Liyux3 and zoteus do this. The model reads the compact text, and code or hosts use the structure.
14. **Hints for what to call next** (biomcp `_meta.next_commands`; arxiv-mcp's `next_retrieval` instruction).

**Bad (avoid these):**

1. **A per-source tool explosion**: paper-search-mcp's 74 tools (`search_x`/`download_x`/`read_x` × 25). It wastes context and leaves the model to choose sources it knows little about.
2. **Unbounded payloads**: whole papers or raw upstream JSON (`raw_metadata`) by default. markitdown-style "convert everything" with no size control.
3. **Stateless-only designs.** Agents re-run the same searches every session. Nothing remembers what was read, rejected or why.
4. **Session-only state that looks persistent** (mcp-dblp's BibTeX buffer).
5. **Hidden LLM calls inside tools** (PaperQA via OpenAI, research-hub crystals, Undermind/Elicit engines). These add cost, latency and opacity, and they double-bill reasoning. zotio's "never calls an LLM" rule is the cleaner contract for a substrate server.
6. **Scraping Google Scholar** (CAPTCHA, ToS) and **Sci-Hub fallbacks** (legal risk). At most, offer them as clearly opt-in plugins.
7. **Mixed or ambiguous naming** across tools and inconsistent `summary|detailed` toggles. Free-text "formatted strings" with no structured counterpart (kujenga, many early servers).
8. **SaaS-bound memory and quotas** for core workflow state (scholar-feed at 200 calls/month, Consensus at 1000 searches/month). Your project's evidence should not live in someone else's account.
9. **Long synchronous calls** (deep research that takes hours) without a job or polling model.

---

## 5. Implications for our server (preliminary)

- **Do not re-implement search breadth.** Wrap 4–5 high-value sources: OpenAlex, Semantic Scholar/Asta, arXiv, Crossref, plus optionally DBLP, OpenReview and PubMed. Put them behind **one `search` tool with a `sources` parameter** and canonical-ID merging, in the style of Liyux3. Interoperate with or delegate to Zotero rather than replacing it.
- **Differentiate on the persistent, project-scoped evidence layer**, which nobody has. It needs:
  - a canonical work table that tracks preprint ↔ venue versions;
  - passage-anchored notes and claims (paper, section or page, char offsets, quote hash);
  - typed evidence links (supports / contradicts / mentions);
  - screening decisions with reasons;
  - snowball frontier state;
  - **agent and session attribution on every write**;
  - an append-only journal.
  It should live in the repo (e.g. `.litreview/` with SQLite and/or JSONL that diffs well in git).
- **Emit LaTeX-ready `refs.bib` from server-owned records** with a stable citekey policy. Run `\cite{}` coverage checks against the manuscript, and apply retraction checks automatically on insert and on export.
- **Make outputs budget-aware by default.** Default to compact records (ID, title, year, venue, and one line). Offer `fields=`, `max_chars`/`max_tokens` with continuation tokens, and IDs-only modes. Return deferred-ID lists instead of silent truncation. Fence and label untrusted paper text.
- **Keep the tool count around 10–15**, grouped by intent (discover, read, record, link, verify, export). Put status and project overview in resources and workflows in prompts or skills. Ship a CLI as well, given the measured 13k-token MCP overhead versus ~100 tokens for a skill.
- **Write safety:** preview/apply for bulk mutations, `readOnlyHint`/`destructiveHint` annotations, and no hidden LLM calls inside the server.

---

## 6. Sources

**First-party and vendor:**
- Claude connectors: [PubMed](https://claude.com/marketplace/connectors/pubmed), [bioRxiv](https://claude.com/marketplace/connectors/biorxiv), [Scholar Gateway](https://claude.com/marketplace/connectors/scholar-gateway), [scite](https://claude.com/fr/marketplace/connectors/scite)
- Claude Science: [connectors & skills](https://claude.com/docs/claude-science/connectors-and-skills), [literature access](https://claude.com/docs/claude-science/literature-access), [launch coverage](https://pharmaphorum.com/news/anthropic-launches-claude-science-pharma-researchers), [Claude for Life Sciences](https://www.anthropic.com/news/claude-for-life-sciences)
- Other vendors: [OpenAlex MCP connector](https://help.openalex.org/access/connector/), [official registry entry](https://registry.modelcontextprotocol.io/v0.1/servers?search=org.openalex), [Asta skill / endpoint](https://sharedcontext.ai/skills/external/Agents365-ai/asta-skill), [alphaXiv listing](https://agentman.ai/agentskills/connections/mcp-server/alphaxiv), [alphaXiv Scalekit doc](https://docs.scalekit.com/agentkit/connectors/alphaxivmcp/), [scite MCP](https://www.scite.ai/mcp), [Consensus MCP](https://apigene.ai/mcp/official/consensus), [Undermind MCP](https://www.undermind.ai/mcp), [Elicit API & MCP](https://elicit.com/blog/the-elicit-api-and-mcp-powering-autonomous-research-engines), [HF MCP docs](https://huggingface.co/docs/hub/hf-mcp-server), [HF MCP changelog](https://huggingface.co/changelog/hf-mcp-server), [OpenAI Prism](https://openai.com/index/introducing-prism/)
- Commentary: [SMU Libraries (Aaron Tay) on academic MCP servers](https://library.smu.edu.sg/topics-insights/what-if-claude-or-chatgpt-could-search-academic-databases-you-and-then-do-something)

**Open-source repositories:** [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server), [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp), [Liyux3/scholar-mcp](https://github.com/Liyux3/scholar-mcp), [54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp) ([tools doc](https://github.com/54yyyu/zotero-mcp/blob/main/docs/tools.md)), [kujenga/zotero-mcp](https://github.com/kujenga/zotero-mcp), [cookjohn/zotero-mcp](https://github.com/cookjohn/zotero-mcp), [oscardvs/zoteus](https://github.com/oscardvs/zoteus), [OrgMentem/zotio](https://github.com/OrgMentem/zotio), [dvdsosa/zotero-native-mcp](https://github.com/dvdsosa/zotero-native-mcp), [pallaprolus/mendeley-mcp](https://github.com/pallaprolus/mendeley-mcp), [cyanheads/pubmed-mcp-server](https://github.com/cyanheads/pubmed-mcp-server), [cyanheads/openalex-mcp-server](https://github.com/cyanheads/openalex-mcp-server), [cyanheads/crossref-mcp-server](https://github.com/cyanheads/crossref-mcp-server), [zongmin-yu/semantic-scholar-fastmcp-mcp-server](https://github.com/zongmin-yu/semantic-scholar-fastmcp-mcp-server), [smaniches/semantic-scholar-mcp](https://github.com/smaniches/semantic-scholar-mcp), [JackKuo666/semanticscholar-MCP-Server](https://github.com/JackKuo666/semanticscholar-MCP-Server), [JackKuo666/Google-Scholar-MCP-Server](https://github.com/JackKuo666/Google-Scholar-MCP-Server), [HasData/google-scholar-mcp](https://github.com/HasData/google-scholar-mcp), [szeider/mcp-dblp](https://github.com/szeider/mcp-dblp), [OpenCodice-Research/openreview-mcp](https://github.com/OpenCodice-Research/openreview-mcp), [anyakors/openreview-mcp-server](https://github.com/anyakors/openreview-mcp-server), [takashiishida/arxiv-latex-mcp](https://github.com/takashiishida/arxiv-latex-mcp), [andybrandt/mcp-simple-arxiv](https://github.com/andybrandt/mcp-simple-arxiv), [genomoncology/biomcp](https://github.com/genomoncology/biomcp), [ElliotPadfield/unpaywall-mcp](https://github.com/ElliotPadfield/unpaywall-mcp), [Hylouis233/bibverify](https://github.com/Hylouis233/bibverify), [mlava/scholar-sidekick-mcp](https://github.com/mlava/scholar-sidekick-mcp), [Vashistht/harcx-mcp](https://github.com/Vashistht/harcx-mcp), [wedo911/citeguard](https://github.com/wedo911/citeguard), [xiaofei03/cite-rag-mcp](https://github.com/xiaofei03/cite-rag-mcp), [docling-project/docling-mcp](https://github.com/docling-project/docling-mcp), [microsoft/markitdown (markitdown-mcp)](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp), [notwhiteblank/scholar-rag-mcp](https://github.com/notwhiteblank/scholar-rag-mcp), [YGao2005/scholar-feed-mcp](https://github.com/YGao2005/scholar-feed-mcp), [WenyuChiou/research-hub](https://github.com/WenyuChiou/research-hub), [pminervini/deep-research-mcp](https://github.com/pminervini/deep-research-mcp), [huggingface/hf-mcp-server](https://github.com/huggingface/hf-mcp-server), [modelcontextprotocol/servers memory](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)

**Directories:** [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) (Research section), [official MCP registry](https://registry.modelcontextprotocol.io) (name search on 2026-10-06 returned: 13 arXiv, 4 OpenAlex including the domain-verified `org.openalex/openalex`, 6 Crossref, 2 DBLP, 1 OpenReview, 15 "citation" and 53 "paper" entries. Zotero, scholar and PubMed queries timed out.), [glama.ai](https://glama.ai/mcp/servers) (OpenReview, Mendeley, GROBID, MinerU listings), [mcp.so](https://mcp.so) (PubMed, GROBID, paperqa listings).
