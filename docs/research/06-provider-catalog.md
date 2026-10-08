# Provider catalog (draft, 2026-10-06)

Every external source the server can talk to is a **provider**: a plugin that declares what it can do,
what credentials it needs, and how fast it may be called. Agents never pick providers by tool name.
They call `search` / `resolve` / `fulltext` / `citations`, optionally passing `sources=[...]`, and the
server routes the call to the enabled providers that have that capability.

Facts come from [02-mcp-servers.md](02-mcp-servers.md) and [04-data-sources.md](04-data-sources.md),
checked 2026-10-06.

## Capabilities a provider can declare

`search` · `resolve` (ID → metadata) · `ids` (cross-walk between ID types) · `refs` / `cites` (citation
graph) · `recommend` · `fulltext` (OA locations / PDF / HTML / LaTeX source) · `bibtex` (venue-quality
entries) · `reviews` (peer review / acceptance) · `new` (listings for alerts) · `verify` (retraction /
update notices) · `passages` (full-text snippet search)

## Kinds of provider

1. **Native API adapters.** These are our own code over REST/SPARQL/OAI and cover most providers.
2. **Upstream MCP / vendor APIs.** Key-based ones (Asta, Elicit) can be adapters. OAuth-only ones (scite,
   Consensus, Undermind, alphaXiv, OpenAlex's own MCP) are awkward for a headless container, so the
   default is for agents to connect to them directly and **import** the results here via `resolve`.
3. **Local services.** These include the Zotero local/web API, a watched folder of PDFs, and parser
   sidecars (Docling, GROBID) running as extra containers in the same compose file.

## Default state

**On (keyless)** means it works with an empty `.env`. **Key** means it turns on when its credential is
set. **Opt-in** means it is off until listed in `LITLEDGER_ENABLE`, because of cost, scope or ToS.

| Provider | Capabilities | Auth | Default | Notes |
|---|---|---|---|---|
| arXiv | search, resolve, fulltext (HTML, LaTeX, PDF), new | none | **On** | 1 req / 3 s, single connection. Preprint metadata of record. |
| Crossref | search, resolve, refs, verify | none (`mailto` → polite pool) | **On** | NeurIPS/ICML/ICLR have no DOIs. Watch for junk repost DOIs (`10.65215/*`). |
| DataCite | resolve | none | **On** | Resolves `10.48550/arXiv.*` DOIs. |
| DBLP (SPARQL + dumps) | search, resolve, ids, bibtex | none | **On** | The search API is behind a bot wall from this machine; SPARQL works. Clean CS venue metadata. |
| OpenAlex (ID lookups) | resolve, ids, fulltext (OA locations) | none | **On** | ID/DOI lookups are free even keyless. Search is budgeted (keyless $0.10/day). |
| HF Papers | search, resolve, new (trending), code links | none | **On** | Successor to Papers with Code links. |
| ACL Anthology | resolve, bibtex | none | **On** | Static data. The `acl-anthology` package needs Python ≥3.11. |
| OpenCitations | refs, cites | none (optional token) | **On** | CC0 DOI-to-DOI graph, 180 req/min. |
| Unpaywall | fulltext (OA locations) | email only | **On if `CONTACT_EMAIL` set** | |
| Europe PMC | search, resolve, refs, fulltext (JATS) | none | **On** | Bio/neuro. Cheap to leave on. |
| PubMed E-utilities | search, resolve | none (optional key → 10 rps) | **On** | 3 rps keyless. |
| bioRxiv / medRxiv | resolve, preprint ↔ published | none | **On** | |
| INSPIRE-HEP | search, resolve, refs, bibtex | none | Opt-in | Physics. |
| Zenodo | resolve (software/data DOIs) | none | Opt-in | |
| Semantic Scholar | search, resolve, ids, refs, cites (intents/contexts), recommend, passages, embeddings | **key** | Key | Keyless is effectively 429-only. Best ML citation graph and ID cross-walk. The API licence forbids redistribution, so keep the server private. |
| OpenAlex (search) | search | **key** | Key | Raises the daily budget for search/list calls. |
| CORE | search, fulltext | **key** | Key | Long-tail repository full text. |
| OpenReview | reviews, resolve, fulltext (PDF, revisions) | **account + token** | Key | Anonymous access is challenged since late 2025. Tokens last ≤1 week. |
| NASA ADS | search, resolve, refs, bibtex | token | Key | Physics/astro. |
| Ai2 Asta | search, passages (S2 corpus) | key | Key | De facto official S2 MCP. Could be an adapter. |
| Elicit | search, reports, systematic review | key, paid | Key | "Research engine as a tool". It calls an LLM upstream. |
| Lens.org / Dimensions | search | token / application | Key | Patents; commercial index. |
| Zotero | library read (mirror), citekeys via Better BibTeX | local API (desktop running) or web API key | Opt-in | Read-only. Writes only to a staging collection, and only if allowed. |
| Mendeley | library read | OAuth | Opt-in | |
| scite | citation statements (supporting / contrasting / mentioning) | OAuth, paid | Connect directly | Import results via `resolve`. |
| Consensus | search | OAuth, quota | Connect directly | |
| Undermind | deep search (async) | OAuth | Connect directly | |
| alphaXiv | search, full-text QA | OAuth | Connect directly | |
| Wiley Scholar Gateway | passages (Wiley only) | sign-in, paid | Connect directly | |
| Google Scholar (via SerpApi etc.) | search | key | **Not shipped** | No API, ToS violation, and Google is suing SerpApi. |
| Sci-Hub / LibGen / Anna's Archive | fulltext | — | **Not shipped** | Declined: shadow-library downloaders facilitate copyright infringement. Providers load through a plugin entry point, so third-party provider packages can be installed separately. |

### Parsers (fulltext → text with anchors)

| Parser | Default | Notes |
|---|---|---|
| arXiv HTML / LaTeX source | **On** | Best quality. The `.bbl` gives exact references. |
| pypdfium2 | **On** | Permissive licence, Windows/Linux wheels, no GPU. |
| Docling | Opt-in (sidecar or extra) | MIT. Layout-aware. Heavier install. |
| GROBID | Opt-in (sidecar container) | TEI output with parsed references. Docker only. |
| PyMuPDF / Marker / MinerU | Not shipped | AGPL or licence conditions. |

## `.env` shape (superseded)

> **Update (implemented):** there is no `.env`. These settings are entered on the web UI's Sources page (or
> `litledger config set KEY=VALUE`), stored in the database and applied without a restart. Names are the same
> without the `LITLEDGER_` prefix. See [SPEC §2.4](../SPEC.md). The original plan follows for reference.

```dotenv
# Identity used in User-Agent / polite pools (Crossref, Unpaywall, OpenAlex)
LITLEDGER_CONTACT_EMAIL=

# Overrides on top of the defaults above (comma-separated provider ids)
LITLEDGER_ENABLE=            # e.g. inspire,zotero
LITLEDGER_DISABLE=           # e.g. pubmed,biorxiv

# Credentials: setting one enables that provider unless it is in LITLEDGER_DISABLE
LITLEDGER_S2_API_KEY=
LITLEDGER_OPENALEX_API_KEY=
LITLEDGER_CORE_API_KEY=
LITLEDGER_OPENREVIEW_USERNAME=
LITLEDGER_OPENREVIEW_PASSWORD=
LITLEDGER_NCBI_API_KEY=
LITLEDGER_ADS_TOKEN=
LITLEDGER_ASTA_API_KEY=
LITLEDGER_ELICIT_API_KEY=
LITLEDGER_ZOTERO_MODE=       # local | web
LITLEDGER_ZOTERO_API_KEY=
LITLEDGER_ZOTERO_LIBRARY_ID=

# Per-provider tuning (optional): LITLEDGER_<ID>_RATE, LITLEDGER_<ID>_TIMEOUT, LITLEDGER_<ID>_DAILY_BUDGET
```

- `docker compose` loads it with `env_file: .env`. Commit `.env.example`, never `.env`.
- **Recommendations are surfaced, not buried.** Each provider declares a recommendation level
  (`strongly-recommended` / `recommended` / `optional`) and a one-line reason. Missing
  strongly-recommended setup is reported in four places:
  1. a startup banner in the container logs;
  2. `litledger doctor` on the CLI;
  3. the MCP server instructions and the status resource, so the agent can relay it to the user;
  4. a short `degraded` note on results the gap actually affected. For example: "Semantic Scholar
     disabled (no LITLEDGER_S2_API_KEY): citation graph is OpenCitations/OpenAlex only, ML coverage
     reduced."

  Strongly recommended today: `LITLEDGER_S2_API_KEY`, `LITLEDGER_OPENALEX_API_KEY` and
  `LITLEDGER_CONTACT_EMAIL`. Example banner:

  At startup the server logs one `WARN` per missing strongly recommended setting, with numbered steps
  to fix it. It still starts, because keyless is a supported mode. `LITLEDGER_QUIET_RECOMMENDATIONS=1`
  silences these warnings once you've decided to run without a key.

  ```text
  WARN  litledger.providers  Semantic Scholar is disabled: LITLEDGER_S2_API_KEY is not set.
        Impact: no ML citation graph, no recommendations, no arXiv<->DOI<->venue ID cross-walk.
        To enable:
          1. Request a free key at https://www.semanticscholar.org/product/api#api-key-form
          2. Add  LITLEDGER_S2_API_KEY=<your key>  to .env (next to docker-compose.yml)
          3. Restart:  docker compose up -d
  WARN  litledger.providers  OpenAlex search is limited: LITLEDGER_OPENALEX_API_KEY is not set.
        Impact: search capped at ~$0.10/day of credit (ID/DOI lookups unaffected).
        To enable:
          1. Create a free key at https://openalex.org (account settings -> API key)
          2. Add  LITLEDGER_OPENALEX_API_KEY=<your key>  to .env
          3. Restart:  docker compose up -d
  WARN  litledger.providers  LITLEDGER_CONTACT_EMAIL is not set.
        Impact: Crossref uses the slow public pool; Unpaywall (OA full-text links) is disabled.
        To enable: add  LITLEDGER_CONTACT_EMAIL=<you@example.com>  to .env and restart.
  INFO  litledger  12 providers enabled; 3 setup warnings above (see `litledger doctor`).
  ```
- A `providers` status resource/tool reports each provider as enabled, disabled, missing credential,
  rate-limited or erroring, with its last error. Agents can see why a source returned nothing, instead
  of reading silence as "no papers".
- Every stored field records which provider supplied it, and every search records which providers
  were actually queried. Results from a run where S2 was off can then be told apart later.
