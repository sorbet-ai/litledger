<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/litledger-dark.png">
    <img src="docs/logo/litledger.png" alt="litledger" width="360">
  </picture>
</h1>

A self-hosted literature ledger for AI agents and the person directing them. Agents capture papers by ID, URL,
BibTeX or title; litledger resolves them to one canonical record (arXiv ↔ DOI ↔ DBLP ↔ OpenReview, preprint ↔
published), tags them per project, stores full text as anchored passages, checks quotes, follows citations, and
exports clean BibTeX with stable citekeys. You browse it all in a web UI with tag facets, a citation/evidence graph
and mind maps that agents can read and edit.

It runs as one Docker container: **MCP** at `/mcp`, **REST** at `/api/v1`, **web UI** at `/`, plus a `litledger` CLI.
It never calls an LLM, and it is built to *save* agent tokens: 8 core tools (~1.1k schema tokens), one-line results
keyed by citekey, outline-first reading. See [docs/SPEC.md](docs/SPEC.md) and the [research](docs/research/) behind it.

## Run it

```bash
mkdir -p backups && sudo chown 10001 backups   # Linux only, once: the server runs as uid 10001 and writes backups here
docker compose up -d --build
docker compose logs litledger # setup WARNs with steps for missing recommended keys
```

Open <http://127.0.0.1:8765>. As the server's admin, choose **Sign in with admin token** and paste the token from
`docker compose exec litledger cat /data/tokens/admin`. Everyone else **creates an account** (email and password, or
Google/GitHub once set up) and starts as a member; promote the people who should help run the server under Admin →
People, and they sign in with their own password from then on. Sign-up is open by default: limit it to email domains
or invites under Admin → Sign-in. Forgot a password without email set up?
`docker compose exec litledger litledger reset-password EMAIL` prints a reset link. Roles, tokens, invitations and the
rest: [docs/ACCESS.md](docs/ACCESS.md).

**All configuration happens in the UI.** Everyone has Settings (profile, password, sessions, connected apps, API
tokens). Admins also have Admin: People, Apps & tokens, Sources (*Add source* picks a source and takes its keys),
Sign-in, Server, Backups and the audit log. Changes apply immediately: no restart, no `.env`. From a terminal the same settings are
`litledger config show|set KEY=VALUE|clear KEY|test SOURCE`. On Git Bash for Windows prefix `docker compose exec`
commands that contain `/data/...` with `MSYS_NO_PATHCONV=1`.

The database and downloaded papers live in the `litledger-data` volume. Backups of both are written daily to
`./backups` (Admin → Backups: schedule, how many to keep, *Back up now*, downloads). On Linux that folder must belong
to uid 10001 (the `chown` above); otherwise *Back up now* says it can't write there. To restore:
`docker compose stop litledger && docker compose run --rm --no-deps litledger litledger restore /backups/<file>`
(it refuses while the server still uses the data).

To reach it from your home network, change the port mapping in `docker-compose.yml` to `"8765:8765"` and set the
public address (`PUBLIC_URL`) under Admin → Server. Every request needs a sign-in; papers are only fetched from public
internet addresses unless you allow private ones (Admin → Server).

**Behind a reverse proxy** (Caddy, nginx, Traefik terminating https): set `PUBLIC_URL` to the https address, and set
the environment variable `LITLEDGER_TRUSTED_PROXIES` (see the comment in `docker-compose.yml`) to the proxy's address
as the container sees it: a proxy container's network such as `172.16.0.0/12`, or `172.17.0.1` for a proxy on the host.
Only those addresses' `X-Forwarded-For` and `X-Forwarded-Proto` are believed (default: `127.0.0.1`). Without it every
client appears to come from the proxy, so rate limits are shared (one person's wrong passwords lock everyone out) and
cookies aren't marked secure; the server logs a warning when `PUBLIC_URL` is https but proxied requests arrive as
http. Changing `PUBLIC_URL` later keeps connected apps working (their tokens re-bind when they refresh).

## Connect agents

Apps sign in through the browser: add the server, and the first time the app asks, a page opens where you click
**Allow**. Each app becomes an agent of yours, every write shows as "claude-code via you", and you can limit it to
some projects or disconnect it under Settings → Connected apps. The OAuth flow follows the MCP authorization spec
(client metadata documents, dynamic registration, PKCE, tokens bound to `/mcp`, rotating refresh tokens).

**Claude Code**: the plugin brings the MCP server and the litledger skill. Run these in a terminal; the last one opens
the browser, where you click **Allow**. Settings → Connected apps shows them with your server's address filled in.

```bash
claude plugin marketplace add sorbet-ai/litledger
claude plugin install litledger@litledger --config server=http://127.0.0.1:8765   # your server's address
claude mcp login plugin:litledger:litledger
```

**Codex** (app and CLI share `~/.codex/config.toml`). Add your server, then sign in:

```bash
codex mcp add litledger --url http://127.0.0.1:8765/mcp   # your server's address
codex mcp login litledger
```

The Codex plugin carries only the litledger skill (Codex can't take the server address from the environment, so the
plugin doesn't guess one): `codex plugin marketplace add sorbet-ai/litledger` and `codex plugin add litledger@litledger`.

**Which project.** A repo pins its project with a header in its own config; the folder name is used otherwise when a
project of that name exists (terminals that set `PWD`), else `default`. For Claude Code, commit a `.mcp.json` and
hide the plugin's copy of the server in that repo so tools aren't listed twice:

```json
// .mcp.json
{ "mcpServers": { "litledger": { "type": "http", "url": "http://127.0.0.1:8765/mcp",
  "headers": { "X-Litledger-Project": "thesis" } } } }
// .claude/settings.json
{ "deniedMcpServers": [{ "serverName": "plugin:litledger:litledger" }] }
```

For Codex, a trusted repo's `.codex/config.toml` merges with the plugin's server:

```toml
[mcp_servers.litledger]
url = "http://127.0.0.1:8765/mcp"
http_headers = { "X-Litledger-Project" = "thesis" }
```

And `.litledger.toml` (`project = "thesis"`) for the CLI.

**Terminals and scripts**: `litledger login --url http://127.0.0.1:8765` shows a code and opens the approval page
(Settings → Connected apps); the token is saved to `~/.litledger/token` and acts for you. With `--claude` it also
runs the Claude Code commands above, so Claude Code signs in through the browser (OAuth) and never holds a token. For CI, make an API token under Settings → API tokens (or a shared one under Admin → Apps & tokens, so it
outlives your account).

More tools for a client: header `X-Litledger-Tools: core,graph,maps,check,admin` (or `all`). The bundled skill
(`litledger skill`) teaches the habits; agents with a shell can use the CLI and skip MCP schemas entirely.

## Tools

| Toolset | Tools |
|---|---|
| core (default) | `find` · `discover` · `resolve` · `work` · `read` · `note` · `tag` · `export` |
| graph | `graph` (references/citations/evidence) · `snowball` (persistent citation chasing) · `entity` · `link` |
| maps | `map_get` · `map_edit` |
| check | `check` (quotes, reference lists, `.bib`, `\cite` coverage of `.tex`) |
| admin | `status` · `update_work` (library upkeep, not a permission) |

## CLI

```bash
pip install -e .                      # or use it inside the container
litledger login                       # once per machine (or export LITLEDGER_TOKEN=ll_...)
litledger backup                      # back up now (inside the container); restore with litledger restore
litledger add 2412.06464 10.18653/v1/N19-1423 --tags phase:baselines --why "main baselines"
litledger find --tags "phase:*"
litledger discover "gated linear attention" --year 2024-
litledger read yang2024gated --mode search --query chunkwise
litledger export --tags phase:writeup --to refs.bib
litledger check-tex paper.tex --bib refs.bib
litledger upload paper.pdf --tags todo:read
litledger import refs.bib --tags imported
litledger snapshot                    # litledger/ (library.json, refs.bib, maps/*.canvas) to commit
litledger doctor                      # setup warnings and source status
```

## Sources

Keyless by default: arXiv, Crossref, DataCite, DBLP (SPARQL), OpenAlex (ID lookups + small search budget), Hugging
Face Papers, web citation meta tags (PMLR, NeurIPS, CVF, ACL…), OpenCitations, Europe PMC, PubMed, bioRxiv, and
Unpaywall once a contact email is set. With credentials: Semantic Scholar, OpenAlex (bigger budget), CORE,
OpenReview, NASA ADS; opt-in: INSPIRE, Zenodo. Missing strongly recommended setup is printed as WARNs with fix steps
at startup, in `litledger doctor`, in the MCP instructions and under Admin → Sources. Third-party sources can be
added as Python entry points (`litledger.providers`).
See [docs/research/06-provider-catalog.md](docs/research/06-provider-catalog.md).

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Python ≥ 3.11
.venv/Scripts/python -m pytest                                   # offline: HTTP replayed from tests/fixtures
LITLEDGER_RECORD=1 .venv/Scripts/python -m pytest                # record missing fixtures from the live APIs
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/token_benchmark.py   # rewrites docs/token-benchmark.md
cd web && npm install && npm run dev                             # UI dev server (proxies to :8765)
LITLEDGER_DATA=./data litledger serve                            # server without Docker
```
