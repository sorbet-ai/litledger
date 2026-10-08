# Synthesis: what the formal lit-review tool should be (draft, 2026-10-06)

Inputs: [01 human tools](01-human-tools.md) · [02 MCP servers](02-mcp-servers.md) ·
[03 AI research tools](03-ai-research-tools.md) · [04 data sources](04-data-sources.md)

## Positioning

Search breadth is already a commodity: there are about 45 MCP servers, and vendors such as Asta/S2,
OpenAlex, scite, Elicit and Consensus now run official ones. Zotero already does human-facing library
management well. **No open tool gives agents a persistent, project-scoped evidence layer.** That layer
needs canonical works (preprint and venue linked), passage-anchored and attributed evidence,
read-depth and verification levels, a claim graph, screening and snowball state that survives
sessions, and LaTeX-ready export. An earlier per-project memory tool was already halfway there. A hand-kept bibliography's
discipline (verification tags, print gate, "never re-discover") is the process half.
The formal tool should combine the two and add the infrastructure the workaround scripts kept
re-inventing.

**Contract:** the server is deterministic infrastructure and never calls an LLM. Judgment stays with
the calling agent: what's relevant, summaries, whether a passage supports a claim, when to stop.
The server provides the primitives that make those judgments checkable.

## Feature set, by priority

### P0: table stakes plus the fixes the audit calls for
1. **Canonical works with an ID crosswalk.** Accept any ID and merge them: DOI, arXiv (versioned or not,
   abs/html/pdf URLs), S2, OpenAlex, DBLP, OpenReview, ACL, PMID. Model preprint and published as versions
   of one work. Every merge is recorded and reversible. "Not a duplicate" decisions are remembered.
2. **Enforced CSL-JSON metadata with per-field provenance** (which provider, when). No more
   `author`/`authors`/`first_author` drift.
3. **Providers behind a single `search`/`resolve`**: arXiv (keyless), Semantic Scholar (needs a key),
   OpenAlex (needs a key since 2026-02), Crossref (mailto), DBLP via SPARQL (the search API is bot-walled),
   HF papers. Polite rate limiting, caching, and distinct `no_match` / `rate_limited` / `auth_error` results.
4. **Read depth and verification levels**, recorded per (work, project). Levels:
   `metadata < abstract < skimmed < sections < full`, and evidence tags
   `verified` (primary text read, quote retrieved) / `snippet` / `recall`. Tags are never downgraded. A
   **print gate** controls which evidence may be cited in output.
5. **Evidence notes** (immutable, attributed, as now) that also carry a kind (`quote | summary | inference |
   reported-result`), a structured anchor (attachment, page/section, char span, quote hash) and structured
   attribution (agent, model, session, project).
6. **A controlled relation vocabulary**: `supports`, `contradicts`, `qualifies`, `extends`, `uses`,
   `compares_to`, `cites`, `about`. Custom labels need a namespace (`x:...`). Every link still requires evidence.
7. **Batch and declarative import.** Upsert many works or notes in one call, and import a JSON/YAML list of
   IDs. That replaces the 862-line populate script.
8. **Export**: BibTeX/BibLaTeX with **stable citekeys** owned by the server, CSL-JSON, RIS, and the existing
   lossless JSON. The `.bib` is generated from server-owned records; the model picks keys but never writes
   entry fields.
9. **One installed package, not copies.** Project scoping happens inside the tool (see the decisions below).
   Health output shows which library and project are active.
10. **Migration** from earlier per-project libraries.
    It normalizes arXiv URL records to arXiv IDs and maps the free relation labels onto the vocabulary.

### P1: what makes it better than a reference manager for agents
11. **Full-text acquisition chain**: arXiv HTML, then arXiv LaTeX source (`.bbl` gives the exact
    references), then the PDF. Then Unpaywall/OpenAlex OA, then Europe PMC for bio. Parsing: pypdfium2 in
    the core install, Docling as an optional extra. Originals are kept in a content-addressed blob store.
    Scanned or textless PDFs are flagged as such rather than "read".
12. **Passage search** with FTS5 (already in place), plus optional sqlite-vec hybrid search through a small
    ONNX embedder. Search can be scoped to a set of works. Results are chunks with anchors.
13. **Quote check**: "does this exact or near-exact quote appear in work X, and where?" This is the
    cheapest defence against misattributed claims.
14. **Reference verification**: check a `.bib` or a list of references field by field against the
    providers, flag retractions and published versions, and audit `\cite{}` coverage against a manuscript.
15. **Snowballing as managed state**: a frontier, depth, a visited set, and each candidate's status
    (`pending / in / out + reason`). It persists across sessions and is ranked by overlap with the seed set.
16. **Search log**: every external query is stored verbatim with its result IDs and yield. It answers
    "have we searched this already?", and the saturation stats (new relevant papers per query) give an
    Undermind-style stopping signal.

### P2: research-program features
17. **Screening ledger** (decision, stage, reviewer, reason codes) with PRISMA counts derived from it.
18. **Typed extraction tables**: datasets, metrics, reported numbers, baselines, each cell backed by
    evidence. This targets a past failure mode: baselines and benchmarks found but never adopted.
19. **Zotero mirror**, read-only through the local API and Better BibTeX. Writes go to a staging
    collection only, and only when the user opts in.
20. **Saved searches and alerts** as pull queries ("what's new on arXiv for topic X since the last check").

## Ergonomics rules (from the MCP survey)

- Keep the core at about 12–15 intent-level tools: discover, resolve, read, record, link, verify,
  export, plus admin. Use resources for status and project overview, and a bundled skill for the workflow
  (standing research rules, rewritten as a skill). Ship a **CLI twin** for scripts and Codex.
- Default to compact output: ID, title, year, venue and one line per result. Add `fields=`, character
  budgets with continuation cursors, an IDs-only mode, and explicit truncation reports.
- Fence and label paper text as untrusted in responses. Keep the existing "data, not instructions" stance.
- Concurrency: several agents write at once (one project's notes came from 8+ distinct agents). Keep SQLite WAL with
  `BEGIN IMMEDIATE`, use idempotent content-addressed writes, and preview/apply bulk mutations.

## Direction agreed in conversation (2026-10-06)

- **One library, scoped by project inside it.** Works, metadata and full text are shared across projects.
  Notes, links, read depth, screening and citekeys belong to a project. Each repo commits a snapshot
  (JSON + `.bib`).
- **Deployment: a Docker service** that runs locally first, later on the home network, eventually on a
  server. One long-running container, with MCP over streamable HTTP plus a REST API for the CLI and
  scripts. `/data` is a named volume (SQLite + content-addressed blobs).
  - Tools take **content or URLs, never client file paths**: the container can't see `D:\`. The local
    CLI reads a file and uploads it. Exports are returned or downloaded, not written to the server's disk.
  - The project is identified by a request header set in each repo's committed `.mcp.json`, so worktrees
    inherit it. Per-client bearer tokens give structured agent attribution.
  - On Docker Desktop for Windows, don't bind-mount NTFS for SQLite; use a named volume.
- **Keep an append-only operation journal from day one** (audit, snapshots, and offline replicas or
  multi-master sync later if needed). Merge semantics if that ever happens: immutable items union,
  monotonic levels take the max, scalars use last-writer-wins with history, citekey clashes are flagged.

- **Keyless by default.** Missing strongly recommended keys produce startup WARNs with numbered fix
  steps (see [06](06-provider-catalog.md)).
- **No Zotero.** The user doesn't use it, so the Zotero mirror is dropped from P2 (it could come back
  later as an opt-in provider).

- **Web view for the user, served by the same container.** It is a browser over the library plus a
  **mind-map editor**. Maps are first-class, machine-readable data, not pictures (details below).

- **Out of scope: migrating the sibling projects' libraries or switching them over.**
- **Capture-first, rigor optional.** The main use is mining papers for future reference. Strict
  verification and read depth are available as annotations, never enforced as a workflow.
- **A general-purpose tag system** (namespaced, library-wide or per-project) is the main organising tool,
  e.g. tagging papers by project phase.
- **Citekeys are shared across projects** and stable.

## Web view and mind maps

**Browse.** Lists of works and topics with filters (project, read depth, verification, tag). Each paper
page shows metadata, read depth and verification per project, notes and evidence (with their passages),
links, and who wrote what. Two graph views are computed from the data: the citation neighbourhood, and
the evidence/claim graph. Live updates over SSE, so the user sees what agents add.

**Mind maps** are stored in the same database and journal as everything else:

- **Map**: id, project, title, author.
- **Node**: either a *reference* to a library entity (work, topic, claim, note/passage) or a *free* text
  node.
- **Edge**: source, target, label. The label uses the shared relation vocabulary or is free.
- **Layout** (positions, colours, collapsed state) is stored separately from meaning, so agents read
  structure and ignore geometry.
- Every node and edge is attributed (human vs which agent). Edits are journaled, so maps have history.

Map edges are *sketches*: user assertions without evidence. They stay out of the evidence graph until
**promoted**, which means attaching an evidence note turns an edge into a real link. Brainstorming
stays free and the evidence graph stays clean. Unresolved references show up as todos, for example a
free node "some paper on delta-rule decay?" that an agent can resolve into a work.

**Agent access.**
- `map_get` returns a compact indented outline with entity IDs and short titles.
- `map_edit` adds or changes nodes and edges. Agent edits are visibly marked in the UI.
- An agent can draft a map ("map the hoard literature") for the user to rearrange.
- Export / import: JSON Canvas (open format, jsoncanvas.org), Mermaid `mindmap`, Markdown outline, OPML.

**Stack.** React + React Flow (xyflow, MIT) for the editable maps, and Cytoscape.js or Sigma.js for
large read-only graphs. The app is built to static files and served by the API container. The user
authenticates with their own token, so attribution reads "you".

**Order.** Core backend → web read views → map editor.

## Decisions for the user

1. **Storage scope.** (a) One global library, e.g. `~/.litledger/`, with per-project views/collections.
   This allows cross-project dedup and "have I read this anywhere?". (b) A DB per project, as today, plus a
   global metadata cache. (c) Per project, stored as git-diffable JSONL in the repo.
   *Recommendation: (a), with a per-project export snapshot committed to each repo.*
2. **API keys.** Semantic Scholar and OpenAlex are close to required now. OK to require them, or keyless
   (arXiv + Crossref + DBLP) by default with keys as upgrades?
3. **Zotero.** Do you use Zotero yourself? If not, P2 is enough. If you do, it moves up.
4. **Name and packaging.** Python ≥3.11 (`acl-anthology` needs it; this machine's default is 3.10.5),
   installed with `uv tool install` and registered once per client.
