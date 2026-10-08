# AI-native literature tools & research agents: survey and design lessons (2026-10-06)

Scope: AI-native literature/research products and agent systems (not plain MCP servers, which are covered
separately). The goal is to pull out the **mechanisms** that matter when the "user" is an AI coding agent
(Claude Code, Codex) doing ML research, and turn them into a feature list for a reusable lit-review /
bibliography MCP server. Facts were checked against 2025-2026 sources on 2026-10-06. Product details change
fast. Anything taken only from secondary reviews is marked *(secondary)*.

---

## 0. TL;DR: what this survey says

1. **Every serious system grounds answers in passages, not papers.** PaperQA2 (chunks + RCS), OpenScholar
   (250-word passages), Ai2 ScholarQA (quote extraction from full-text snippets), Elicit (supporting quotes
   per extracted cell), Anthropic Research (a dedicated CitationAgent that locates the source span), OpenAI
   deep research (character-offset annotations). The unit of evidence is a **passage with an anchor**.
2. **The best searchers combine several strategies and iterate.** Keyword + dense retrieval + LLM relevance
   judgment + **citation traversal** (forward and backward), with query reformulation from what was found
   (Undermind, Paper Finder, PaperQA2, Consensus Deep Search, Elicit). A single keyword query is a known
   failure: the AI Scientist novelty check called 12/12 ideas "novel".
3. **Stopping is either a fixed budget or a saturation estimate.** Undermind is the only product that
   explicitly estimates coverage from how fast new relevant papers are still turning up. Everyone else uses
   budgets (papers, queries, tool calls, minutes).
4. **Commercial tools converged on screening + extraction tables + PRISMA-style audit trails** (Elicit,
   Consensus, SciSpace). Every decision carries a reason and a quote.
5. **Contradiction handling exists but is rare.** ContraCrow (claim extraction, then PaperQA2 search for
   counter-evidence, then an 11-point Likert score; 70% of flagged contradictions confirmed by humans) and
   scite (citation statements classified as supporting / contrasting / mentioning) are the two real mechanisms.
6. **Citation verification is now an industry of its own.** Since ICLR 2026 desk-rejected papers with
   fabricated references, there are deterministic-first verifiers (CheckIfExist, CiteTracer, CiteAudit,
   GPTZero Hallucination Check). The common design: **exact field matching against Crossref / S2 /
   OpenAlex first, LLM judgment only for ambiguous cases.**
7. **Measured failure rates are high even for retrieval-backed systems.** DeepTRACE (ICLR 2026) found
   40-80% citation accuracy across deep-research systems. SourceCheckup found 50-90% of LLM answers not fully
   supported by their own cited sources. Ungrounded GPT-4o fabricated 78-90% of citations (OpenScholar).
   Retracted papers were cited without warning by Elicit, ScholarQA, Perplexity and Consensus. **A tool
   should make it cheap to check every claim mechanically.**
8. **The vendors are moving to MCP.** Elicit (API + MCP, 2026-07-15), scite MCP (2026-02/04), Consensus
   MCP (2026), Asta MCP (`asta-tools.allen.ai/mcp/v1`), Edison/FutureHouse API, Claude Science connectors.
   An agent-facing server should be able to **use these as search backends** but own the
   **library, identity, provenance, verification and export layer**, which none of them provide to the
   calling agent in a persistent, local, auditable form.

---

## 1. Mechanism matrix (condensed)

| System | Search | Rank / dedupe | Read depth | Grounding | Verification | Contradiction | Structured extraction | Iteration / stopping | Persistence |
|---|---|---|---|---|---|---|---|---|---|
| **PaperQA2 / PaperQA3** (FutureHouse → Edison) | Agent issues keyword queries to S2 (+ local tantivy index); citation traversal over S2/Crossref | Dense top-k (30) → LLM score 0-10 + contextual summary (RCS) → top 5-15 | Full text (Grobid/PyMuPDF/Docling/Nemotron); PQ3 adds figures and tables | Chunk-level summaries, cited by doc key and pages | Metadata from Crossref/S2/Unpaywall; retraction and journal-quality flags | ContraCrow (claim extraction + Likert) | Contextual-summary metadata (e.g. gene names) | Agent picks tools; can answer "insufficient info" | Pickled `Docs`, index in `~/.pqa` keyed by settings hash |
| **OpenScholar** (UW/Ai2, Nature 2026) | Dense over peS2o (45M papers, ~234M passages) + S2 API keywords + academic web search | Bi-encoder top-100 → cross-encoder; ≤3 passages/paper; citation-count prior | 250-word passages, full text when OA | Inline passage citations | Post-hoc attribution pass that adds missing citations | No | No | Self-feedback loop (≤3 feedback items, retrieves more for gaps) | Static datastore |
| **Ai2 ScholarQA / Asta "Summarize literature"** | Hybrid BM25 + dense over full-text snippets (Vespa; ~8M papers at launch) | Transformer reranker, top 50 | Full-text snippets | **Quote extraction first**, then sections built from quotes | Quotes link to papers | No | **Comparison tables** (schema generation, then value generation) | Fixed pipeline | Open-source library (`ai2-scholar-qa`) |
| **Asta Paper Finder** | Query analysis → sub-workflows: specific paper, semantic, metadata, author; **bidirectional citation chasing** + query reformulation | LLM relevance on **decomposed sub-criteria**, combined with metadata (recency, citations) | Abstracts + snippets | Per-paper relevance justification | — | — | — | Fast (~30 s) vs diligent (~3 min); stops when enough found or candidate cap reached | Apache-2.0 snapshot on GitHub |
| **Undermind** | Embeddings + citations + LLM reasoning to generate candidates | LLM classifies each candidate (highly / closely / not relevant) | Full text where available *(secondary)* | Per-paper explanation | — | — | — | ~3 adaptive rounds; **estimates % of relevant papers found** from the discovery-rate decay | Saved searches |
| **Elicit** (Systematic Review, Research Agent) | Keyword import + semantic (adds ≤500 papers) + citation trails both directions | Auto-generated screening criteria; papers ranked by P(meets all criteria) | Title/abstract screening, then full-text extraction | **Supporting quote + explanation per cell**; sentence-level citations | PRISMA 2020 support (2026-05); exclusion reasons + screening quotes (2026-04) | — | **User-defined columns**; CSV export; reports up to 80 papers | Can revisit any step; "living reviews"; Routines (2026-09) | Library collections, alerts, API + MCP (2026-07) |
| **Consensus** | Hybrid keyword + semantic over ~200-220M papers (OpenAlex, S2, PubMed, arXiv); Deep Search runs ~20 queries + citation following | ~1,500 candidates → rerank by relevance + "research strength" (citations, recency, journal via SciScore) → 20 (Pro) / 50 (Deep) | Mostly abstracts; full text only for OA + partners | Color-coded citations | Retraction data added after 2025 criticism | **Consensus Meter** (yes/no/possibly/mixed vote count) | Study snapshot (population, method, n, outcomes) | PRISMA-like flow diagram; **non-deterministic** reruns | Account history; MCP |
| **scite** | Search over 280M articles / 1.6B classified citation statements | Supporting / contrasting / mentioning counts | Citation-context sentences from full text | Answers grounded in citation statements | **Reference Check**: retractions, contested claims in a manuscript or bibliography | **Contrasting-citation classifier** | — | — | Dashboards; MCP (2026) |
| **Anthropic Research** (Claude) | Lead agent plans, then parallel subagents search broad → narrow | Source-quality heuristics added after agents favored SEO farms | Web/connector pages | **Separate CitationAgent** maps claims to source spans | LLM-judge rubric incl. citation accuracy | — | — | Effort scaling (1 agent / 3-10 calls up to 10+ subagents) | Plan saved to external memory before 200k truncation |
| **OpenAI deep research** (API) | Web search + ≤2 vector stores + **MCP servers that must expose `search` and `fetch`** | Model-internal | Fetched pages/docs | Annotations with URL, title, **start/end char offsets** | — | — | — | `max_tool_calls`; background mode | None (stateless) |
| **Gemini Deep Research** (API, 2026-04) | Plans, then up to ~160 web searches *(secondary)* + files + MCP | — | Web + files | Cited report | — | — | Charts | Interactive vs "Max" async | NotebookLM import |
| **NotebookLM** | Deep Research (since 2025-11) finds sources; otherwise user-curated corpus | — | Full text of uploaded sources | Answers cite passages in user sources only | Closed-corpus grounding | — | — | — | Notebook = persistent corpus |
| **STORM / Co-STORM** | Perspective discovery → simulated writer/expert Q&A grounded in search | — | Web snippets | Sentence citations | — | Implicit via perspectives | Outline; Co-STORM **mind map** (insert/reorganize) | Conversation turns; human can steer | Mind map / knowledge base |
| **ContraCrow** | PaperQA2 per claim | — | Full text | Evidence for/against each claim | — | 0-10 Likert; AUC 0.842 | Claims list (~35/paper) | — | — |
| **CiteGuard / CiteAgent** | S2 search + `search_text_snippet` + `find_in_text` + `ask_for_more_context` | — | Targeted snippets (full text added +3% accuracy at 2-4x cost) | Attribution to a specific paper | Accepts **multiple valid citations** | — | — | ReAct loop | — |

---

## 2. Commercial / hosted AI literature tools

### 2.1 Elicit
- **Systematic Review workflow** (launched 2025-01; PRISMA 2020 support 2026-05-07; screening quotes and
  exclusion reasons 2026-04-02): search (keyword import + semantic top-up of ≤500 papers) → **auto-generated
  screening criteria** the user can edit → papers ranked by likelihood of meeting *all* criteria →
  per-criterion decisions with reasons and supporting quotes → full-text **extraction table with
  user-defined columns**. Each cell has a supporting quote and an explanation → CSV export → report.
  Users can return to any step ("living reviews"). Screening scales to tens of thousands of papers
  *(secondary: "40,000")*.
- **Research Agent** (2026-08-05): "transparent methods trail and sentence-level citations". It searches
  saved library collections (2026-09-17). Collaborative Sessions + Skills (2026-09-01). **Routines**
  (2026-09-30) replace alerts with proactive scheduled agent work. Find Papers / Extract Data are exposed
  as agent skills.
- **API + MCP** (public 2026-07-15): Search, Reports and Systematic Review endpoints, usable from Claude,
  ChatGPT and Gemini. Citation-trail search in both directions since 2024-12. Zotero import. Browser
  extension for paywalled full text (2025-05).
- **Lessons:** extraction tables whose **every cell is backed by a quote**; screening as **per-criterion**
  decisions with reasons (auditable and re-runnable); PRISMA counts as a by-product of state; library
  collections that the agent can search; scheduled re-runs.
- Sources: https://elicit.com/blog/systematic-review · https://support.elicit.com/en/articles/14823097-changelog ·
  https://elicit.com/solutions/literature-review

### 2.2 Consensus
- Corpus of ~200-220M papers from OpenAlex, S2, PubMed and arXiv. **Mostly abstracts.** Full text only for
  OA papers and publisher partners, so paywalled fields get abstract-level synthesis.
- Retrieval stack: hybrid keyword + embedding → first rerank of ~1,500 by relevance and "research
  strength" → quality signals (citations, recency, SciScore journal rigor, LLM-extracted study design) →
  20 papers (Pro) or 50 (Deep).
- **Deep Search** runs ~20 queries plus citation following and shows a **PRISMA-like flow diagram** of the
  queries run, retrieval counts, screening removals and final set.
- **Consensus Meter** = vote counting (yes / no / possibly / mixed). Critics note it ignores sample size,
  effect sizes and heterogeneity. Study snapshots (population, method, n, outcome) are LLM-extracted and
  can be wrong.
- Pitfalls documented by Aaron Tay: **non-deterministic rankings across reruns**, opaque cut from 573
  eligible papers to 50, journal score used as a proxy for study quality, and biomedical-tuned signals
  applied to other fields. It started using retraction data (publishers, Retraction Watch) after a 2025
  report that it cited retracted papers.
- MCP server (Claude directory; ChatGPT app store since 2026-04): 1,000 searches/month, 20 papers per search.
- **Lessons:** show the query log and flow counts. Do **not** turn evidence into a single vote-count score.
  Record whether each judgment came from an abstract or full text. Make reruns reproducible.
- Sources: https://aarontay.substack.com/p/a-2025-deep-dive-of-consensus-promises ·
  https://support.goconsensus.com/setting-up-the-consensus-ai-connect-consensus-mcp-server ·
  https://www.technologyreview.com/2025/09/23/1123897/ai-models-are-using-material-from-retracted-scientific-papers/

### 2.3 Undermind
- Candidate generation from semantic embeddings, citations and LLM reasoning over Semantic Scholar. An
  LLM (originally GPT-4) **classifies each candidate** as highly relevant / closely related / ignorable.
  Tay reports ~2% of human-"highly relevant" papers were marked irrelevant. The search then **adapts and
  searches again** from what it found (~3 rounds, 3-5 min).
- **Coverage estimate:** tracks the discovery rate of new relevant papers. As it decays, Undermind
  estimates the fraction of all relevant papers found ("found X%"). This is the only explicit
  **saturation-based stopping criterion** among the products surveyed.
- Users clarify the question first ("tell me exactly what you want, like a colleague"). Results are grouped
  into themes. An API is available.
- **Lessons:** relevance classification per paper with a reason. **Saturation metrics** (new-relevant per
  round) let the agent decide when to stop. Clarify intent before searching.
- Sources: https://aarontay.substack.com/p/undermindai-different-type-of-ai-agent ·
  https://www.undermind.ai/whitepaper.pdf · https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12352444/

### 2.4 SciSpace
- Corpus of ~280M papers. Tools: Literature Review, **Deep Review** (broader search plus structured
  synthesis), Chat with PDF, Extract Data, AI Writer, Citation Generator, a general **SciSpace Agent**,
  a Biomedical Agent (2025-12) and **Systematic Research with PRISMA methodology**. Reviews say synthesis is
  fine for first drafts but needs fact-checking.
- **Lesson:** the field has converged on a PRISMA-shaped workflow, with extraction as a column table.
- Sources: https://www.educatorstechnology.com/2026/09/scispace-review.html · https://wiki.ubc.ca/SciSpace

### 2.5 scite (Research Solutions)
- **Smart Citations**: deep-learning classification of each citation statement (the sentence + context in
  the citing full text) as **supporting / contrasting / mentioning**. Coverage is ~1.6B citation statements
  over ~280M articles. Accuracy is high for "mentioning" and lower for supporting / contrasting (independent
  evaluation).
- **scite Assistant** answers from citation statements. **Reference Check** scans a manuscript or
  bibliography for retracted papers and heavily contested claims.
- **scite MCP** (launched 2026-02-26; in Claude Connectors 2026-04-29).
- **Lessons:** **citation context** (the sentence in which paper B cites paper A) is the most useful
  signal for contradiction detection and for spotting citation laundering. A "check my bibliography"
  primitive is valuable on its own.
- Sources: https://journals.iupui.edu/index.php/hypothesis/article/view/26528 · https://docs.scite.ai/mcp/overview ·
  https://researchsolutions.investorroom.com/2026-02-26-Research-Solutions-Launches-Scite-MCP,-Connecting-ChatGPT,-Claude,-Other-AI-Tools-To-Scientific-Literature

### 2.6 Perplexity (Academic focus)
- Restricts search to scholarly sources (S2, PubMed, arXiv/bioRxiv, journals). Numbered citations.
  Guides still say to click through and verify each claim. Not designed for review state or extraction.
- **Lesson:** source filtering alone does not give you faithful attribution.
- Source: https://www.educatorstechnology.com/2026/02/perplexity-ai-for-research.html

### 2.7 Deep research agents: OpenAI, Google, Anthropic
- **OpenAI deep research API** (`o3-deep-research`, `o4-mini-deep-research`; docs say they are being
  replaced, with retirement by 2026-07-23). Tools: web search, file search over ≤2 vector stores, code
  interpreter, and **remote MCP servers that must implement exactly `search(query)` and `fetch(id)`**
  (read-only, `require_approval: "never"`). Output citations are **annotations with URL, title and start/end
  character offsets** in the answer. Recommended flow: clarify → rewrite prompt → research. `max_tool_calls`
  caps effort. Background mode. Explicit **prompt-injection warning** for MCP and web content: use only
  trusted servers, log all tool calls, stage public vs private data.
  → An agent-facing lit server should offer a **`search`/`fetch`-compatible surface** so deep-research models
  can use the library directly.
  Source: https://developers.openai.com/api/docs/guides/deep-research
- **Gemini Deep Research** (Interactions API; 2026-04-21 upgrade to Gemini 3.1 Pro): Deep Research
  (interactive) and Deep Research Max (async). Plans, runs many searches *(secondary: up to ~160)*, reads
  files, supports MCP data sources natively, returns cited reports with charts. NotebookLM can import the
  report and **its full source list (cited and uncited)** as notebook sources (2025-11).
  Sources: https://ai.google.dev/gemini-api/docs/interactions/deep-research ·
  https://www.buildfastwithai.com/blogs/gemini-deep-research-api-tutorial
- **Anthropic Research (multi-agent)**: an Opus lead agent writes a plan to **external memory** (context
  past 200k tokens is truncated) and spawns parallel Sonnet subagents. They search broad first, then
  narrow, using interleaved thinking. A **CitationAgent** post-processes the report to find the exact
  source location for each claim. Effort scales with query complexity (1 agent and 3-10 calls, up to 10+
  subagents). It was evaluated with an LLM judge on factual accuracy, **citation accuracy**, completeness,
  **source quality (primary over secondary)** and tool efficiency. Observed failures: spawning too many
  subagents, searching endlessly for sources that don't exist, and **preferring SEO content farms over
  academic PDFs**. Better tool descriptions cut task time by 40%.
  Source: https://www.anthropic.com/engineering/multi-agent-research-system
- **Claude connectors / Claude Science**: directory connectors include **PubMed, bioRxiv, Clinical Trials,
  ChEMBL, Scholar Gateway (Wiley), Consensus, scite**. Claude Science (beta) adds featured read-only
  connectors, including a **"Literature Graph" (OpenAlex + arXiv)**, plus a **literature-review skill**.
  Users can write their own skills and import them from GitHub. Tay notes that connecting unused servers
  floods context and that the LLM "can still occasionally misrepresent what a paper actually says".
  Sources: https://claude.com/docs/claude-science/connectors-and-skills ·
  https://www.anthropic.com/news/claude-for-life-sciences ·
  https://library.smu.edu.sg/topics-insights/what-if-claude-or-chatgpt-could-search-academic-databases-you-and-then-do-something
- **Lessons:** (i) separate **finding** from **citing**: a dedicated verification pass maps each claim to a
  span. (ii) Keep plan and progress outside the context window. (iii) Give the agent source-quality metadata
  so it doesn't default to web SEO pages. (iv) Expose a few well-described tools, not many.

### 2.8 Ai2 Asta ecosystem (Paper Finder, ScholarQA, Asta tools, AstaBench)
- **Asta** (2025-08) is Ai2's agentic science platform: "Find papers" = Paper Finder, "Summarize
  literature" = ScholarQA, plus data analysis.
- **Paper Finder**: query analysis (known-item vs topical; extracts author, year, venue and phrases like
  "recent" or "central"). Parallel sub-flows: specific-paper (title APIs + citation proximity + LLM
  verification), semantic, metadata, author. **Bidirectional citation chasing** on highly relevant papers,
  with query reformulation, until enough papers are found or a cap is hit. **Relevance is judged per
  sub-criterion** (e.g. "unscripted dialog" + "English" + "annotated") and then combined. Final rank =
  content relevance + query-implied criteria (recency, influence). Fast (~30 s) vs diligent (~3 min) mode.
  **Every step is shown** (query analysis, workflows, judgments). An Apache-2.0 snapshot is on GitHub.
- **ScholarQA**: hybrid BM25 + dense scoring of **full-text snippets** (Vespa) → reranker → top 50 →
  **LLM quote extraction** → clustering of quotes into an outline (paragraph vs bullet-list sections) →
  section-by-section generation with attribution → **comparison tables** (schema generation, then cell
  values; evaluated on ArxivDIGESTables). Released as `ai2-scholar-qa` on PyPI.
- **Asta Scientific Corpus tools / MCP**: 225M+ papers, 80M+ authors, 2.4B+ citation edges. A
  **snippet_search over 12M+ full-text papers (285M+ passages)** that can be **restricted to given paper
  IDs**. MCP at `https://asta-tools.allen.ai/mcp/v1` (streamable HTTP, `x-api-key`). **Date-restricted
  search** supports reproducible evaluation.
- **AstaBench** (arXiv 2510.21652): 2,400+ problems, including paper finding, ScholarQA-CS2, LitQA2 and
  ArxivDIGESTables. 57 agents evaluated. "AI remains far from solving" science research assistance.
- **Semantic Scholar features**: **TLDRs** (SciTLDR model, ~60M papers in CS/bio/med), **Semantic Reader**
  (inline citation cards showing the cited paper's TLDR, skimming highlights for goal/method/result).
- **Lessons:** (i) **decompose criteria** before judging relevance. (ii) **snippet search scoped to known
  IDs** is the core "read" primitive. (iii) **date cutoffs** for reproducibility. (iv) TLDR-level
  summaries are cheap triage context.
- Sources: https://allenai.org/blog/ai2-scholarqa · https://github.com/allenai/asta-paper-finder ·
  https://aarontay.substack.com/p/ai2-paper-finder-and-futurehouse · https://arxiv.org/abs/2510.21652 ·
  https://allenai.org/asta/resources · https://www.semanticscholar.org/product/tldr ·
  https://www.infodocket.com/2025/03/27/research-tools-allen-institute-for-artificial-intelligence-introduces-ai2-paper-finder/

### 2.9 FutureHouse platform → Edison Scientific (Crow / Falcon / Owl / Kosmos)
- **Platform** (2025-05): Crow (concise cited answers, API-oriented), **Falcon** (deep review), **Owl**
  ("has anyone done X?", i.e. a novelty / precedent check), Phoenix (chemistry). The UI has a Reasoning tab
  (papers evaluated, evidence extracted) and a References tab with **quality labels** ("peer reviewed",
  "domain leading") and per-reference **reasoning traces**, showing which context was used and which was
  not.
- **Edison Scientific** spun out in 2025-11. **Kosmos** reads ~1,500 papers and runs ~42k lines of code per
  run. **Edison Literature** is backed by **PaperQA3** (2026-02-18; API `literature-20260216[-high]`). PaperQA3
  is **multimodal**: it reads figures and tables from 150M+ papers and patents, and the agent decides
  *when* to pull a figure into context. It parses with **NVIDIA Nemotron Parse** (~10 s/page). Metadata
  from many sources is used for **provenance skepticism**: predatory journals, retractions, patent origin.
- **Lessons:** label each reference with quality signals and **why it was used**. Show "evidence considered
  but not used". Fetch figures and tables on demand, not by default.
- Sources: https://www.futurehouse.org/news/launching-futurehouse-platform-ai-agents ·
  https://edisonscientific.com/news/edison-literature-agent · https://docs.edisonscientific.com/ ·
  https://www.hpcwire.com/aiwire/2025/11/07/futurehouse-spins-out-edison-scientific-launches-kosmos-ai-for-research/

### 2.10 Smaller / reading-focused tools
- **alphaXiv**: arXiv overlay with highlight-to-ask chat on a paper (2025-02), Deep Research over arXiv
  (2025-04), paper ↔ code integration, AI blog summaries, and an Assistant on Explore. **Lesson:** for ML
  research, **paper ↔ code repo links** are first-class metadata. https://www.alphaxiv.org/changelog
- **NotebookLM**: a closed-corpus grounded QA. Answers cite passages **only from user-added sources**.
  Deep Research imports sources. **Lesson:** "answer only from the library" is a useful mode.
- **Iris.ai RSpace**: content-based search, smart filters, summaries, and an **Extract** tool that fills a
  schema (e.g. experiments, outcomes, unit-normalized values) with **confidence scores**. Little public
  change after RSpace 1.3 (2024-12). **Lesson:** typed values with unit normalization and confidence.
  https://iris.ai/blog/introduction-to-researcher-workspace
- **Keenious**: recommends papers for a **draft text** (Word / Docs add-in) using OpenAlex. Signals: shared
  rare terms, predicted meaning, citation count. **Lesson:** "find papers relevant to this paragraph / my
  draft" is a distinct and useful query mode (it also suits a related-work section).
  https://help.keenious.com/article/54-how-keenious-recommends-research-articles
- **Scholarcy**: structured "summary flashcards" (aims, methods, findings, limitations, figures, tables,
  **formatted reference list**). Available as an API. **Lesson:** a fixed **per-paper digest schema** is
  cheap, reusable context. https://www.scholarcy.com/faq

---

## 3. Open-source / academic agent systems

### 3.1 PaperQA2 (FutureHouse; repo now CalVer, latest 2025.12.17)
- **Tools:** `paper_search` (keywords + optional year range → S2 (+ local tantivy full-text index), ~12
  candidates, parse and chunk) · `gather_evidence` (embed the question, take the top-k=30 chunks, then
  **RCS**: an LLM writes a 200-400-token **query-specific summary + relevance score 0-10** per chunk and
  reranks) · `citation_traversal` (from papers whose summaries scored ≥8, get **backward references (S2 +
  Crossref) and forward citers (S2)**; keep papers that appear in ≥⌈α·|D|⌉ of the source papers' lists with
  α=1/3, cap 12, skip already-seen papers) · `gen_answer` (top 5-15 summaries → answer with citations;
  may answer **"insufficient information"**).
- **Parsing:** Grobid (sections and tables; ~8.9k tokens/paper) vs PyMuPDF (~16k tokens). Docling and
  Nemotron readers are also supported, with page numbers, images and tables. Chunk size 2,250 chars
  (750-3,000 made little difference).
- **Ablations:** removing RCS significantly hurt accuracy, and RCS needs a strong model (weaker models
  dropped below the no-RCS baseline). Removing the agent loop hurt accuracy. Citation traversal improved
  **DOI recall** significantly but accuracy only marginally. Typical run: 1.26 searches and 0.46 traversals
  per question, ~14.5 papers examined.
- **Results:** LitQA2 precision 85.2% vs 73.8% for PhD/postdoc humans, with similar accuracy (66 vs 68%) and
  21.9% abstention. WikiCrow: 86.1% cited-and-supported vs Wikipedia 71.2%. $1-3/query, $4.48/article.
- **Metadata:** Crossref, S2, Unpaywall; **retraction checks**; journal quality; citation counts.
- **Persistence:** pickled `Docs` objects, indexes under `~/.pqa` versioned by a settings hash.
  Settings presets: `high_quality`, `fast`, `wikicrow`, `contracrow`.
- **ContraCrow:** split a paper into 5k-char section chunks → LLM extracts claims and keeps those with
  quality ≥8 (~35 claims/paper) → each claim goes through PaperQA2 with a contradiction prompt and gets an
  **11-point Likert** score. ContraDetect: AUC 0.842, 88% precision at threshold 8, 98% correct
  "no-evidence" handling. On 93 biology papers: 2.34 contradictions/paper, 70% confirmed by experts.
  **Calibration issue:** scores of 9-10 were no more accurate than 8.
- Sources: https://arxiv.org/abs/2409.13740 · https://github.com/Future-House/paper-qa ·
  https://www.futurehouse.org/paperqa · https://arxiv.org/abs/2312.07559 · https://arxiv.org/abs/2412.21154 (Aviary)

### 3.2 OpenScholar (Asai et al.; Nature 2026)
- Datastore: **peS2o v3, 45M OA papers to 2024-10, 250-word chunks with titles, ~234M passages**. A
  bi-encoder continually pretrained on peS2o → top-100 → **cross-encoder reranker** with **≤3 passages per
  paper** and a normalized citation-count prior. It also queries the S2 API with generated keywords and
  academic web search (You.com).
- **Self-feedback loop:** draft → ≤3 natural-language feedback items (which can trigger more retrieval)
  → revise → **post-hoc citation verification** that adds citations to uncited, citation-worthy
  statements.
- **ScholarQABench:** 2,967 questions (single-paper SciFact, PubMedQA, QASA; multi-paper CS, bio, physics,
  neuro) with expert answers. Metrics include **citation precision/recall F1**. Ungrounded GPT-4o
  fabricated **78.7% (CS) / 94.8% (biomed)** of citations. Untrained 8B models' citation F1 collapses as
  context grows. Limitations: OA-only bias, non-English underrepresented.
- Sources: https://arxiv.org/abs/2411.14199 ·
  https://www.infodocket.com/2026/02/04/research-paper-synthesizing-scientific-literature-with-retrieval-augmented-language-models/

### 3.3 STORM / Co-STORM (Stanford OVAL)
- STORM: **perspective discovery** from related topics' articles → simulated conversations (writer persona
  asks questions, expert answers grounded in retrieval) → **outline** → cited article.
- Co-STORM: human-in-the-loop discourse with a turn-management protocol. A **dynamic mind map** (concept
  tree; `insert` places information under the best-matching concept, `reorganize` renames and prunes
  empty or single-child nodes) reduces cognitive load and serves as persistent state.
- **Lessons:** perspectives / facets as a coverage device. A **hierarchical topic map that evidence is
  attached to** is a better review structure than a flat list.
- Sources: https://github.com/stanford-oval/storm · https://arxiv.org/abs/2408.15232

### 3.4 GPT Researcher
- Planner → parallel execution agents (crawl and summarize) → publisher. A multi-agent mode has a
  chief editor and section researchers. **Deep Research mode** is a recursive tree with breadth/depth
  parameters (~5 min). Aggregates 20+ sources. Supports MCP retrievers and has its own MCP server.
  Web-first, not citation-graph aware.
- Source: https://github.com/assafelovic/gpt-researcher

### 3.5 Automated survey writers: AutoSurvey, SurveyX, SurveyForge (+ SurveyG, DeepSurvey)
- **AutoSurvey** (NeurIPS 2024): embedding retrieval over an arXiv CS database → outline → **parallel
  subsection drafting** → integration and refinement → evaluation including **citation recall/precision
  via NLI** (does the cited paper support the sentence?).
- **SurveyX** (arXiv 2502.14776): preparation phase with **online reference retrieval** (keyword expansion
  + embedding filtering) and **AttributeTree**, which pre-extracts *type-specific attributes per paper*
  (e.g. method papers vs benchmark papers) to compress context. Then outline → RAG rewriting →
  re-polishing. NLI-based citation-quality evaluation.
- **SurveyForge** (arXiv 2503.04629): outline heuristics learned from **human-written survey outlines** +
  a memory-driven scholar navigation agent. **SurveyBench** (100 human surveys; reference / outline /
  content quality). Later work: **SurveyG** (hierarchical citation graph, 2510.07733) and **DeepSurvey**
  (citation reliability, 2605.29522).
- **Lessons:** **per-paper attribute extraction into a typed schema** before synthesis is the standard fix
  for context overflow. **Sentence-level NLI support checks** are the standard citation-quality metric.
- Sources: https://arxiv.org/abs/2406.10252 · https://arxiv.org/abs/2502.14776 · https://arxiv.org/abs/2503.04629 ·
  https://arxiv.org/abs/2510.07733 · https://arxiv.org/abs/2605.29522

### 3.6 LitLLM (ServiceNow)
- From an abstract: LLM → keywords → S2 search → **LLM/embedding reranking** against the abstract →
  **plan-based generation**. A sentence plan fixes the number of sentences, their lengths, and **which
  paper is cited in which sentence**. This reduces hallucinated citations because the citation slots are
  fixed before writing.
- Source: https://arxiv.org/abs/2402.01788

### 3.7 ResearchAgent (Baek et al., NAACL 2025)
- Starts from a core paper, expands over references and citations (academic graph), and uses an
  **entity-centric knowledge store** (co-occurrence of key concepts across the literature) to draw links
  between domains. **ReviewingAgents** critique ideas iteratively.
- **Lesson:** concept/entity indexing across the library (datasets, methods, tasks) supports idea
  generation and gap finding.
- Source: https://arxiv.org/abs/2404.07738

### 3.8 AI Scientist (Sakana) & Agent Laboratory
- **AI Scientist:** the novelty check queries the S2 API and shows the **top-10 results with abstracts**
  for a few rounds. An independent evaluation (arXiv 2502.14297) found it called **all 12 ideas novel**,
  including well-known ones such as micro-batching SGD. Keyword-only, abstract-only novelty checking is
  unreliable.
- **Agent Laboratory** (arXiv 2501.04227): the PhD agent uses the arXiv API with three actions:
  `summary` (abstracts of the top 20), `full_text` (one paper), `add_paper` (into the curated review).
  This is a minimal, sensible **search → read → curate** primitive set, but there is no dedup, provenance
  or verification.
- Sources: https://arxiv.org/abs/2408.06292 · https://sakana.ai/ai-scientist/ · https://arxiv.org/abs/2502.14297 ·
  https://arxiv.org/abs/2501.04227

### 3.9 Citation & retrieval benchmarks
- **CiteME** (NeurIPS 2024 D&B): ML-paper excerpts that each cite exactly one paper. LMs alone score 4.2-18.5%,
  humans 69.7%. **CiteAgent** (GPT-4o + S2 with `search` / `read` / `select`) scores 35.3%.
  **CiteGuard** (arXiv 2510.17853) adds `search_text_snippet`, `find_in_text` and `ask_for_more_context`
  and reaches 65.4% with DeepSeek-R1. It treats attribution as **alignment with any valid citation**
  (several papers can support a claim). Targeted snippets beat full text: full text adds +3% at 2-4x the
  tokens. https://arxiv.org/abs/2407.12861 · https://arxiv.org/abs/2510.17853
- **LitSearch** (EMNLP 2024): 597 ML/NLP literature-search questions (inline-citation and author-written).
  Dense retrievers beat BM25 by **24.8 points R@5**, and LLM reranking adds +4.4. → Keyword-only search
  is not enough. https://arxiv.org/abs/2407.18940
- **ScholarQABench** (see OpenScholar), **LitQA2** (PaperQA2), **AstaBench**, **SurveyBench**, **LABBench2**.
- **DeepTRACE** (ICLR 2026, arXiv 2509.04499): an audit framework with 8 dimensions. Statement-level
  decomposition yields a **citation matrix** and a **factual-support matrix**. Deep-research systems are
  more thorough but still **one-sided on debate questions**, with **citation accuracy of 40-80%** and many
  statements unsupported by their own sources.
- **SourceCheckup** (Nat. Commun. 2025): 58k statement-source pairs. **50-90% of LLM responses are not fully
  supported**. GPT-4o with web search has ~30% of statements unsupported. https://arxiv.org/abs/2402.02008

### 3.10 Hallucinated-citation detection (2025-2026 surge)
- Context: GPTZero found **100+ hallucinated citations in 53 accepted NeurIPS 2025 papers**, and 50 of 300
  sampled ICLR 2026 submissions had at least one. **ICLR 2026 desk-rejected 600+ flagged submissions**
  after a screen → senior-reviewer → chair-check pipeline. ICML and ACM CCS 2026 have similar policies.
  Typical corruption is **subtle**: initials expanded into guessed first names, paraphrased titles, real
  authors with the wrong paper, wrong venue or year.
- **CheckIfExist** (OSS web tool): multi-source matching against **Crossref + S2 + OpenAlex**.
- **CiteTracer** (arXiv 2605.08583, "Source or It Didn't Happen"): extract citations from PDF / BibTeX →
  cache lookup → URL fetch → scholarly connectors → web search → **deterministic field matching** → only
  ambiguous cases go to class-specialist LLM judges. 97.1% accuracy on 2,450 synthetic citations;
  includes 957 real fabricated ICLR 2026 citations.
- **CiteAudit** ("You cited it, but did you read it?"): five agents (extractor, dual-end memory, web
  search, judge, scholar). F1 0.903 and recall 1.0 on 467 real fabrications. Checks **existence and
  claim-evidence alignment**. Free web app.
- **GPTZero Hallucination Check**: used by the ICLR program chairs.
- **Lesson:** the right design is **deterministic resolution first, with field-level diffs, and LLM
  adjudication only for the ambiguous remainder**. This is exactly the split between server and agent.
- Sources: https://gptzero.me/news/neurips/ · https://www.alphaxiv.org/abs/2605.08583 ·
  https://the-decoder.com/hallucinated-references-are-passing-peer-review-at-top-ai-conferences-and-a-new-open-tool-wants-to-fix-that/ ·
  https://liner.com/review/citeaudit-you-cited-it-but-did-you-read-it-benchmark ·
  https://betakit.com/start-up-investigation-reveals-50-peer-reviewed-papers-contained-hallucinated-citations/

---

## 4. Full-text / PDF infrastructure

| Tool | What it is | Strengths for lit review | Weaknesses | Use in our server |
|---|---|---|---|---|
| **arXiv LaTeX source** (`/e-print/<id>`) | tar.gz with .tex, .bib/.bbl, figures | Exact math, section structure and labels, **machine-readable bibliography (.bbl/.bib)**, captions, `\cite` keys → **exact citation contexts** | Macros, multi-file builds, some source-less papers; licenses vary | **First choice for arXiv** (most of our sources are arXiv). Parse `\cite` contexts + .bbl → resolve references. Tools: `bibextract`, LaTeXML, unarXive pipeline |
| **arXiv HTML** (LaTeXML; native since ~Dec 2023) / **ar5iv** (older) | HTML render of LaTeX | Easy section / paragraph anchors, math as MathML | Conversion failures on some papers | Second choice; stable anchors (`#S3.SS2`) |
| **GROBID** | CRF/DL PDF → TEI XML | **References: ~0.87-0.90 F1**; citation-context callouts linked to bib entries (0.76-0.91 F1); **consolidation via Crossref / biblio-glutton (DOI resolution >0.95 F1)**; header metadata | Weak on math and complex tables; Java service | **Reference extraction + citation contexts + header metadata** for non-arXiv PDFs. Used by PaperQA2 (better tokens/quality) |
| **Docling** (IBM) | Layout + TableFormer → structured doc / Markdown | Fast (~8 s / 12 pages *(secondary)*), good layout, page provenance, MIT license, PaperQA reader | Partial table structure on hard cases | Default body-text parser (CPU friendly) |
| **Marker / Marker 2** (Datalab) | Pipeline: Surya OCR, layout, reading order → Markdown + LaTeX equations | Fast; 76.0 olmOCR-bench claim *(secondary)* | Not all equations / tables right; GPL / commercial license terms | Optional backend |
| **MinerU 2.5** (OpenDataLab) | 1.2B decoupled VLM (layout pass, then crop recognition) | Top OmniDocBench layout (97.5 mAP *(secondary)*), full table structure, LaTeX formulas | GPU-heavier; slower | Optional high-quality backend for tables and equations |
| **Nougat** (Meta) | End-to-end ViT → Markdown / LaTeX trained on arXiv | Good math | **Repetition loops and page-level hallucination**, slow, effectively superseded | Avoid as default. Never trust without a text-layer cross-check |
| **olmOCR / Nemotron Parse** | VLM OCR | Scanned PDFs; Nemotron is used by PaperQA3 for figures and tables | GPU | Optional for scans |
| **S2ORC / peS2o** | Ai2 full-text corpora (S2ORC: sections, bib entries, **inline citation mentions** linked to papers; peS2o: cleaned LM corpus, 45M papers in v3) | Pre-parsed, citation-linked | OA subset; bulk dataset downloads need an API key; not always current | Lookup / fallback for full text and citation contexts |
| **S2 API snippet search** | `/snippet/search`: ~500-word snippets from title, abstract and **body** with `section`, `snippetOffset`, annotations; filters for year and venue; limit ≤1000 | Remote passage-level evidence without downloading PDFs; Asta version can **restrict to paper IDs** | Coverage limited to licensed full text; rate limits | **Remote "read" backend** before full download |
| **Unpaywall / OpenAlex OA locations** | Legal OA PDF URLs | Fetch chain for non-arXiv papers | — | PDF acquisition |

Benchmarks: **OmniDocBench** (CVPR 2025; 9 doc types, 19 layout categories), olmOCR-bench.
Practical rule: **scanned PDFs and
OCR'd text are where hallucinated "readings" come from**. Record which parser produced which text, and
treat OCR-only text as lower trust.

Sources: https://grobid.readthedocs.io/en/latest/Introduction/ · https://arxiv.org/abs/2509.22186 ·
https://arxiv.org/abs/2412.07626 · https://jimmysong.io/blog/pdf-to-markdown-open-source-deep-dive/ ·
https://github.com/datalab-to/marker/releases · https://arxiv.org/abs/2301.10140 ·
https://aident.ai/actions/semanticscholar_tools/semanticscholar_text_snippet_search ·
https://www.piwheels.org/project/bib4llm

---

## 5. Cross-cutting patterns (what the good systems share)

1. **Search is multi-strategy and iterative**: keywords (exact terms, known items) + dense (concepts) +
   citation graph (snowballing, both directions, overlap-weighted) + metadata filters, with reformulation
   from findings. Known-item lookup ("that paper by X on Y, ~2023") is a separate path.
2. **Relevance is judged by an LLM, per criterion, with a reason** (Paper Finder sub-criteria, Elicit
   per-criterion screening, Undermind three-way labels). Scores are poorly calibrated at the top end
   (ContraCrow), so keep **categorical labels + reasons + quotes** rather than bare scores.
3. **Reading is passage-centric with anchors.** Chunks of 250 words to ~2k chars, ≤3 per paper in the
   final context, query-specific summaries (RCS) instead of raw chunks, and **figures and tables on demand**.
4. **Grounding is enforced by structure, not by prompts**: quote-first generation (ScholarQA), citation
   slots planned before writing (LitLLM), a post-hoc citation pass (OpenScholar, Anthropic CitationAgent),
   character-offset annotations (OpenAI), and NLI support checks (AutoSurvey, SurveyX).
5. **Verification is layered**: existence (ID resolution) → metadata correctness (field diff) → integrity
   (retraction, version, venue) → support (does the passage entail the claim?) → stance (supporting vs
   contrasting citations).
6. **State is the product for reviews**: screening ledgers, extraction tables, PRISMA counts, query logs,
   living reviews / alerts / routines, library collections the agent can search.
7. **Transparency UIs** (Paper Finder steps, FutureHouse reasoning and "used vs unused evidence", Consensus
   flow diagram) show that the **trace itself** is a deliverable. For an agent, the trace must be
   **queryable state**, not prose.
8. **Vendors now expose MCP**, but their MCP surfaces are *stateless search / answer* endpoints. None gives
   the calling agent a persistent, local, cross-session library with canonical IDs, read-depth provenance
   and verification. **That gap is what our server should fill.** Vendor MCPs (Elicit, scite, Consensus,
   Asta) can be optional *backends* or sibling servers.

---

## 6. Features an agent-facing lit-review tool should have (ranked by value)

The split rule: **the server does what is deterministic, checkable and must persist; the agent does what
needs judgment.** The server's job is to make agent judgments **cheap to make, impossible to fake, and
easy to audit later**.

### (a) What the tool server should do (deterministic infrastructure), in value order

| # | Feature | Why (evidence from survey) | Notes / primitive sketch |
|---|---|---|---|
| 1 | **Canonical identity & cross-ID dedup** | Every system keys on S2/DOI. Our audit found arXiv duplicates (`abs/` vs `html/…v1`) and no arXiv support | IDs: arXiv (versionless + version), DOI, S2 paperId/CorpusId, OpenAlex W-id, PMID/PMCID, ACL Anthology, OpenReview, DBLP key. Merge preprint ↔ published version. Fuzzy title + first-author + year match with confidence. `resolve(identifier \| free-text \| bibtex)` |
| 2 | **Reference existence & metadata verification** | ICLR 2026 desk rejects. CheckIfExist / CiteTracer / CiteAudit all match fields deterministically first. Subtle corruptions (expanded initials, paraphrased titles) | `verify_reference(ref)` → {status: exact/fuzzy/not_found/ambiguous, matched_id, **field-level diffs** (title, authors, year, venue), retraction flag}. **Generate BibTeX from authoritative metadata, never from LLM text.** `audit_bibliography(.bib/.tex/.md)` for a whole manuscript |
| 3 | **Provenance & read-depth levels per source and per note/claim** | Consensus abstract-only synthesis; `[verified]/[snippet]/[recall]` verification tags + print gate; AGENTS.md "downloaded ≠ reviewed" | Enum: `metadata_only < abstract < snippet < section_read < full_text_read`, plus origin (`search_result`, `fetched`, `parsed:<parser>`, `model_recall`). Never auto-downgrade. Optional **print gate**: export / cite only above a threshold level |
| 4 | **Full-text acquisition + parsing with stable anchors** | PaperQA2 parsing choice affects tokens and accuracy; PQ3 figures and tables; Elicit / ScholarQA quote anchors | Fetch chain: **arXiv LaTeX source → arXiv HTML → OA PDF (Unpaywall / OpenAlex) → user-supplied file**. Parsers: LaTeX / LaTeXML, GROBID (refs + citation contexts), Docling (body), optional MinerU / Marker. Store sections, paragraphs, **page numbers**, char offsets, figure / table captions, content hash, parser name and version |
| 5 | **Passage retrieval inside the library, scoped by paper IDs** | Asta snippet_search restricted to IDs; CiteGuard `find_in_text` / `search_text_snippet` (targeted beats full text at 2-4x lower cost); LitSearch (dense ≫ BM25); OpenScholar ≤3 passages per paper | Hybrid BM25 (FTS5) + optional local embeddings. Filter by paper IDs, sections and read level. **Diversity cap per paper.** Return passage + anchor + paper ID, bounded and paginated |
| 6 | **Quote / anchor verification** | Elicit cells need quotes; Anthropic CitationAgent; DeepTRACE unsupported statements; OpenAI char-offset annotations | `verify_quote(paper_id, text)` → exact / fuzzy location (section, page, offsets) or `not_found`. Purely deterministic. **Notes and extraction cells that claim a quote must pass this check** |
| 7 | **Federated external search with normalized results + "already known" flags** | All products use S2 / OpenAlex / arXiv / PubMed. Agents waste effort rediscovering papers (our audit) | Backends: S2 (search, snippet, recommendations), OpenAlex, arXiv, Crossref, optional Asta MCP / Elicit / scite / Consensus. **Date cutoffs** (AstaBench). Each hit is annotated with `in_library`, `screening_status`, `read_level`. Batch multi-query with cross-query dedup |
| 8 | **Citation-graph expansion with citation contexts** | PaperQA2 traversal (overlap α=1/3) improves recall; Paper Finder / Elicit / Consensus all snowball; scite contexts power contradiction detection | `expand(paper_ids, direction=refs\|citers\|both)` ranked by **overlap count across seeds**, excluding seen papers. Include **citation-context sentences** (S2 contexts / GROBID / LaTeX `\cite`) and S2 intents. Stance classification (supporting / contrasting) is left to the agent or to scite |
| 9 | **Review ledger: query log, screening state, saturation stats** | Elicit / Consensus PRISMA flows; Undermind coverage estimate; Consensus non-determinism | Per review project: queries (text, backend, date, n results, n new, n relevant). Per-paper state machine `candidate → screened_in/out (criterion, reason, quote) → read → extracted`. **Saturation metric**: new-relevant per round / per expansion. PRISMA count export. Snapshot result sets for reproducibility |
| 10 | **Typed extraction tables with evidence per cell** | Elicit columns; ScholarQA / ArxivDIGESTables; SurveyX AttributeTree; Iris.ai typed values; lesson from a past project ("baselines / benchmarks never adopted") | User- or agent-defined schema per review. **ML defaults**: task, datasets, metrics, reported numbers (value, unit, split, setting), baselines, model size / compute, code URL, license. Each cell has value + anchor + verified quote + read level + extractor. CSV / Markdown / JSON export, comparison views |
| 11 | **Claim / evidence graph with a controlled vocabulary** | ContraCrow; scite stance; our audit's relation sprawl and zero `contradicts` uses | Claims as first-class records. Relations from a fixed vocabulary {supports, contradicts, qualifies, extends, replicates, fails_to_replicate, uses_dataset, compares_against, cites} each **requiring an anchored evidence note**. Query helper `open_contradictions` / `claims_without_counterevidence_search` |
| 12 | **Integrity metadata** | Elicit, ScholarQA, Perplexity and Consensus all cited retracted papers; PaperQA / Edison retraction and journal checks; arXiv versions change | Retraction (Crossref + Retraction Watch data), withdrawn arXiv, version list (v1 → vN with dates), peer-review / venue status, published-version link, citation count with fetch date. Refreshable, timestamped |
| 13 | **Export & interop** | Zotero import (Elicit), CSV export, BibTeX demand | BibTeX (stable keys), CSL-JSON, RIS, Markdown bibliography, extraction CSV, PRISMA counts, lossless JSON. Zotero / BibTeX **import** that goes through `verify_reference` |
| 14 | **Cross-session, cross-project persistence & watch queries** | Elicit Routines / alerts; NotebookLM notebooks; Anthropic plan memory; audit's per-project-by-copy problem | One library with project / collection scoping; "seen in any project?" lookup; attributed immutable notes + revisions; structured agent / session attribution; saved queries re-runnable as "what's new since <date>" |
| 15 | **Agent ergonomics & safety** | Anthropic tool-description lesson (−40% time); Tay on context flooding; OpenAI prompt-injection guidance | Few, orthogonal, well-described tools. Bounded outputs + cursors. **Summary-first, expand-on-demand** (TLDR → abstract → passages → full section). Token-budget parameters. Batch / idempotent ops. All stored or fetched text marked untrusted. Optional **OpenAI-style `search` / `fetch` facade** so deep-research models can use the library |
| 16 | **Per-paper digest cache** | Scholarcy flashcards; S2 TLDR; PaperQA RCS summaries; SurveyX AttributeTree | Store agent-written digests (aims / method / results / limitations) **keyed to paper + digest schema version + read level**, with anchors, so later sessions reuse them instead of re-reading |
| 17 | (Lower) **Topic map / outline structure** | Co-STORM mind map; ScholarQA outline clustering; STORM perspectives | Hierarchical topics with papers and claims attached; reorganize ops. Useful but can be a simple tag tree |
| 18 | (Lower) **Figures / tables on demand** | PaperQA3 multimodal | Extract figure / table images + captions at parse time; return only when requested |

### (b) What the calling LLM agent should do, and the primitives that support it

| Agent responsibility (judgment) | How good systems do it | Primitive the server provides |
|---|---|---|
| **Clarify scope; decompose question into facets / criteria** | OpenAI clarify → rewrite; Undermind "tell me like a colleague"; Paper Finder sub-criteria; STORM perspectives; Elicit auto-criteria | `review_create(question, criteria[], facets[], date_cutoff)`; criteria stored and versioned so screening refers to them by ID |
| **Formulate / reformulate queries (broad → narrow), choose strategies** | Anthropic broad-then-narrow; Undermind adaptive rounds; Paper Finder reformulation from found papers | Batch search over backends; **"new results only"** view; query log showing what has been tried; term suggestions from abstracts of relevant papers (deterministic keyphrase stats) |
| **Screen / judge relevance with reasons** | Elicit per-criterion; Undermind 3-way; Paper Finder sub-criteria scoring | `screen(paper_id, decisions[{criterion_id, verdict, reason, quote?}])`; screening queue sorted by retrieval score; **disagreement sampling** (re-screen a random k% by another agent or session, report agreement) |
| **Decide what to read deeply** | PaperQA2 RCS score ≥8 → traversal; FutureHouse "used vs unused" | Read-level tracking; `reading_queue` (screened-in but below the needed read level); cheap tiers (TLDR / abstract / snippets) before full text |
| **Query-specific summarization (RCS) & extraction** | PaperQA2 RCS (needs a strong model); Elicit cells; SurveyX AttributeTree | Passage retrieval with anchors; `record_extraction(cell, value, quote)` with automatic `verify_quote`; schemas versioned |
| **Claim extraction; contradiction / counter-evidence search** | ContraCrow; DeepTRACE finds one-sidedness | `claim_add`; `find_counterevidence(claim)` = deterministic bundle (passages matching negated / alternative phrasings the agent supplies + papers citing the claim's source + scite contrasting contexts if available); store Likert / categorical verdict + evidence |
| **Assess source quality / weight evidence** | Consensus quality signals (and their critique); FutureHouse quality labels; Anthropic primary-over-secondary rubric | Expose **facts** (venue, peer-review status, retraction, version, citation count + date, primary vs review / survey type, code availability), **not a single quality score** |
| **Decide when to stop** | Undermind saturation; Anthropic effort scaling; PaperQA abstention | Saturation stats per review (new-relevant per round, overlap of expansion results with known set, coverage by year / venue); budget counters |
| **Novelty / precedent judgment** | FutureHouse Owl; AI Scientist failure | Multi-strategy search + citation expansion + saturation report before "novel" may be recorded; store novelty claims as claims with the search trail attached |
| **Synthesis & writing (outline, related work, report)** | STORM / SurveyForge outlines; LitLLM sentence plans; ScholarQA quote-first sections | **Cite-by-key with gate**: agent writes `[@key]`. `check_citations(text)` resolves every key, flags read level below threshold, and **for each cited sentence returns the stored anchored evidence (if any) linking that sentence's claim to the key**. Missing evidence = flagged. The agent (or a verifier subagent) does the NLI judgment |
| **Final faithfulness pass** | Anthropic CitationAgent; OpenScholar post-hoc attribution; AutoSurvey NLI | `evidence_for(key, claim_text)` returns top passages from that paper only (scoped retrieval) so a verifier can confirm or deny cheaply. Results are stored as `support_verdict` on the citation |

---

## 7. Known failure modes of LLM-based literature review, and mitigating primitives

| Failure mode | Evidence | Tool-level mitigation |
|---|---|---|
| **Fabricated references** (paper doesn't exist) | GPT-4o 78-95% fabricated without retrieval (OpenScholar); NeurIPS 2025 / ICLR 2026 incidents | Nothing can be cited unless it resolves to a canonical ID (`verify_reference`). BibTeX is generated from fetched metadata. `audit_bibliography` for manuscripts. Provenance `model_recall` is never exportable without verification |
| **Corrupted references** (real paper, wrong authors / year / venue / title; expanded initials) | GPTZero NeurIPS analysis; CiteTracer field matching | Field-level diff on resolution. Store authoritative metadata separately from agent-supplied metadata. Warn on mismatches. Normalize authors (CSL) |
| **Misattributed / unsupported claims** (citation exists but doesn't say that) | DeepTRACE 40-80% citation accuracy; SourceCheckup 50-90% responses not fully supported; Tay: "can still misrepresent what a paper says" | Evidence-anchored links required. `verify_quote`. Scoped `evidence_for(key, claim)` for verifier passes. `support_verdict` stored per citation. `check_citations(text)` before export |
| **Citation laundering** (citing a review or secondary paper, or a paper that merely *cites* the claim's origin; claims drifting through citation chains) | scite "mentioning" vs supporting distinction; Anthropic rubric prefers primary sources | Detect when the anchored passage is itself a **citation sentence** (GROBID / LaTeX `\cite` callouts in the span) → flag `secondary_evidence` and offer `trace_to_primary` (resolve the inner citation, fetch its passage). Store paper type (survey / review vs primary) |
| **Over-reliance on abstracts / metadata** | Consensus mostly abstracts; AI Scientist top-10 abstracts; AGENTS.md "downloaded ≠ reviewed" | Read-depth levels on sources, notes and cells. Print gate (e.g. quantitative claims need `section_read`+). Full-text fetch chain. `reading_queue` |
| **Hallucinated readings from bad parses** (OCR / Nougat loops, scanned PDFs, broken tables) | Nougat limitations; earlier fetch notes; PaperQA parsing effects | Parser provenance + quality flags (OCR used, garbled-text ratio, table confidence). Prefer LaTeX source / HTML. Keep page references so humans can check. Mark OCR text lower-trust |
| **Recency gaps / model cutoff** | LLMs miss the long tail and recent work; AstaBench uses date cutoffs | Always search live backends (never rely on model recall). Default arXiv recency sweep. "What's new since <date>" watch queries. Explicit date-cutoff parameter |
| **Popularity / Matthew-effect bias; one-sidedness** | LLMs over-prefer highly cited papers (arXiv 2405.15739, 2504.02767); DeepTRACE debate one-sidedness | Retrieval not weighted by citations by default (citation counts shown as metadata only). Coverage report by year / venue / citation band. `find_counterevidence` and `open_contradictions` queries. Contrasting-citation contexts |
| **Retracted / superseded / withdrawn work** | Elicit 5, ScholarQA 17, Perplexity 11, Consensus 18 retracted papers cited unflagged; GPT-4o-mini never flagged retractions in 6,510 reports | Retraction + arXiv version / withdrawal checks at import and refresh. Warnings carried into exports and `check_citations` |
| **Duplicates & version confusion** (arXiv v1 vs vN vs proceedings) | Our audit (`abs/` vs `html/…v1`) | Canonical identity, version-aware anchors (an anchor records the version it was read from), preprint ↔ published merge |
| **Context overflow / lost state in long reviews** | Anthropic 200k truncation → memory; OpenScholar citation F1 collapses with more context; Tay on context flooding | Passage-level retrieval with per-paper caps. Digest cache. Summary-first responses with pagination. Persistent review state (queries, screening, extraction) so the agent can **resume** instead of re-reading. Small tool surface |
| **Rediscovery & inconsistency across sessions / agents** | Our audit: workaround scripts, schema drift, label sprawl | One library, enforced schema, controlled vocabularies, structured attribution, "seen anywhere?" lookup, batch / declarative import |
| **Non-reproducible searches** | Consensus rerun variance; black-box top-50 cut | Query log with timestamps and backend versions; snapshotted result sets; deterministic sort; PRISMA counts derived from ledger state |
| **False novelty claims** | AI Scientist judged 12/12 ideas novel | Novelty claims require a recorded multi-strategy search trail + citation expansion + saturation stat; stored as a claim with evidence |
| **Vote-counting / pseudo meta-analysis** | Consensus Meter critique | Store extracted effect sizes / n / settings as typed cells. Don't ship a "consensus score". Leave weighting to the agent and the human, with the data visible |
| **Prompt injection via paper / web text** | OpenAI deep-research guidance | Mark all fetched and stored text as untrusted data in tool outputs and server instructions. No tool executes instructions from content. Log tool calls |
| **Miscalibrated LLM scores** | ContraCrow 9-10 no better than 8 | Store categorical verdicts + reasons + quotes, not just scores. Support second-opinion / disagreement sampling |

---

## 8. Implications for our design (short)

- **Position:** a **local, persistent, verifiable library + review-state server** that agents call. It is
  not another answer engine. Vendor engines (Elicit, scite, Consensus, Asta, Edison) are optional search
  backends or sibling MCPs. Our value is identity, provenance, read depth, verification, ledger and
  export, which they don't give the agent.
- **Highest-leverage v1 set** (from §6a): canonical IDs + arXiv / S2 / OpenAlex resolution (1),
  `verify_reference` / `audit_bibliography` (2), read levels + print gate (3), arXiv-source / HTML / PDF
  fetch-and-parse with anchors (4), scoped passage search (5), `verify_quote` (6), federated search with
  `in_library` flags (7), citation expansion with overlap ranking (8), BibTeX / CSL export (13).
- **v2:** review ledger + saturation (9), typed extraction tables with ML defaults (10), claim graph with
  controlled vocabulary + counter-evidence bundles (11), integrity refresh (12), watch queries (14),
  `search` / `fetch` facade for deep-research models (15).
- **Don't build:** a single quality / consensus score, LLM-generated BibTeX, unbounded "read the whole PDF"
  tool outputs, or many overlapping tools.

---

## Sources (consolidated)

Commercial / hosted
- Elicit: https://elicit.com/blog/systematic-review · https://support.elicit.com/en/articles/14823097-changelog · https://elicit.com/solutions/literature-review
- Consensus: https://aarontay.substack.com/p/a-2025-deep-dive-of-consensus-promises · https://support.goconsensus.com/setting-up-the-consensus-ai-connect-consensus-mcp-server
- Undermind: https://aarontay.substack.com/p/undermindai-different-type-of-ai-agent · https://www.undermind.ai/whitepaper.pdf · https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12352444/
- SciSpace: https://www.educatorstechnology.com/2026/09/scispace-review.html · https://wiki.ubc.ca/SciSpace
- scite: https://docs.scite.ai/mcp/overview · https://journals.iupui.edu/index.php/hypothesis/article/view/26528 · https://researchsolutions.investorroom.com/2026-02-26-Research-Solutions-Launches-Scite-MCP,-Connecting-ChatGPT,-Claude,-Other-AI-Tools-To-Scientific-Literature
- Perplexity: https://www.educatorstechnology.com/2026/02/perplexity-ai-for-research.html
- OpenAI deep research: https://developers.openai.com/api/docs/guides/deep-research
- Gemini Deep Research: https://ai.google.dev/gemini-api/docs/interactions/deep-research · https://www.buildfastwithai.com/blogs/gemini-deep-research-api-tutorial
- Anthropic: https://www.anthropic.com/engineering/multi-agent-research-system · https://claude.com/docs/claude-science/connectors-and-skills · https://www.anthropic.com/news/claude-for-life-sciences
- Agent + academic MCPs: https://library.smu.edu.sg/topics-insights/what-if-claude-or-chatgpt-could-search-academic-databases-you-and-then-do-something
- Ai2 / Asta / S2: https://allenai.org/blog/ai2-scholarqa · https://github.com/allenai/asta-paper-finder · https://allenai.org/asta/resources · https://arxiv.org/abs/2510.21652 · https://aarontay.substack.com/p/ai2-paper-finder-and-futurehouse · https://www.semanticscholar.org/product/tldr · https://www.infodocket.com/2025/03/27/research-tools-allen-institute-for-artificial-intelligence-introduces-ai2-paper-finder/
- FutureHouse / Edison: https://www.futurehouse.org/news/launching-futurehouse-platform-ai-agents · https://edisonscientific.com/news/edison-literature-agent · https://docs.edisonscientific.com/ · https://www.hpcwire.com/aiwire/2025/11/07/futurehouse-spins-out-edison-scientific-launches-kosmos-ai-for-research/
- alphaXiv: https://www.alphaxiv.org/changelog
- NotebookLM: https://www.digitalocean.com/resources/articles/what-is-notebooklm · https://www.leadwithai.co/article/level-up-your-report-with-notebooklms-deep-research
- Iris.ai: https://iris.ai/blog/introduction-to-researcher-workspace · Keenious: https://help.keenious.com/article/54-how-keenious-recommends-research-articles · Scholarcy: https://www.scholarcy.com/faq

Open-source / academic
- PaperQA2 / ContraCrow / WikiCrow: https://arxiv.org/abs/2409.13740 · https://github.com/Future-House/paper-qa · https://www.futurehouse.org/paperqa · https://arxiv.org/abs/2312.07559 · https://arxiv.org/abs/2412.21154
- OpenScholar / ScholarQABench: https://arxiv.org/abs/2411.14199
- STORM / Co-STORM: https://github.com/stanford-oval/storm · https://arxiv.org/abs/2408.15232
- GPT Researcher: https://github.com/assafelovic/gpt-researcher
- AutoSurvey / SurveyX / SurveyForge / SurveyG / DeepSurvey: https://arxiv.org/abs/2406.10252 · https://arxiv.org/abs/2502.14776 · https://arxiv.org/abs/2503.04629 · https://arxiv.org/abs/2510.07733 · https://arxiv.org/abs/2605.29522
- LitLLM: https://arxiv.org/abs/2402.01788 · ResearchAgent: https://arxiv.org/abs/2404.07738
- AI Scientist: https://arxiv.org/abs/2408.06292 · evaluation https://arxiv.org/abs/2502.14297 · Agent Laboratory: https://arxiv.org/abs/2501.04227
- CiteME: https://arxiv.org/abs/2407.12861 · CiteGuard: https://arxiv.org/abs/2510.17853 · LitSearch: https://arxiv.org/abs/2407.18940
- DeepTRACE: https://arxiv.org/abs/2509.04499 · SourceCheckup: https://arxiv.org/abs/2402.02008
- Hallucinated citations: https://gptzero.me/news/neurips/ · https://www.alphaxiv.org/abs/2605.08583 · https://liner.com/review/citeaudit-you-cited-it-but-did-you-read-it-benchmark · https://the-decoder.com/hallucinated-references-are-passing-peer-review-at-top-ai-conferences-and-a-new-open-tool-wants-to-fix-that/ · https://betakit.com/start-up-investigation-reveals-50-peer-reviewed-papers-contained-hallucinated-citations/
- Citation bias: https://arxiv.org/abs/2405.15739 · https://arxiv.org/abs/2504.02767
- Retractions: https://www.technologyreview.com/2025/09/23/1123897/ai-models-are-using-material-from-retracted-scientific-papers/ · https://sheffield.ac.uk/ijc/news/new-research-suggests-chatgpt-ignores-article-retractions-and-errors-when-used-inform-literature

Full-text infrastructure
- GROBID: https://grobid.readthedocs.io/en/latest/Introduction/
- MinerU 2.5: https://arxiv.org/abs/2509.22186 · OmniDocBench: https://arxiv.org/abs/2412.07626
- Marker: https://github.com/datalab-to/marker/releases · comparison: https://jimmysong.io/blog/pdf-to-markdown-open-source-deep-dive/
- S2 platform / S2ORC: https://arxiv.org/abs/2301.10140 · snippet search: https://aident.ai/actions/semanticscholar_tools/semanticscholar_text_snippet_search
- arXiv LaTeX bibliography extraction (bibextract / bib4llm): https://www.piwheels.org/project/bib4llm
