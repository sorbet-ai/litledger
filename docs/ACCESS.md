# litledger: accounts and access

How people and agents get into a litledger server and what each may do. One install is one team or lab
(single-tenant), so there are no organisations or workspaces: the server is the team. Code: `accounts.py` (the model
and its rules), `auth.py` (secrets → who is calling), `signin.py` (browser sign-in), `access.py` (the account API),
`oauth.py` (MCP apps), `server/rest.py` (`require_admin`, `require_person`).

## 1. What we borrowed, and what we left out

| Pattern | Seen in | litledger |
|---|---|---|
| A server-side secret is the root of trust; people get admin rights from an admin | Grafana's initial admin, Gitea `admin user create --admin`, Home Assistant | The first start writes the admin token to `/data/tokens/admin`; the root admin signs in with it and promotes people |
| Roles: admin, member | GitHub orgs, Linear, Grafana, Sentry | Two roles; new accounts are members |
| Self sign-up with a policy: open, allowed email domains, invite only | Gitea, Slack, Linear, Grafana `allow_sign_up` | `SIGNUP` = `anyone` (default), `domains`, `invited` |
| Invite by email with a role; copy the link when there's no mail server | Grafana, Linear, Sentry | Single-use links, 7 days, revocable; emailed with SMTP, always shown to the admin |
| Sign in with Google/GitHub; link a *verified* email; link from settings | GitHub, Gitea, Vercel | Linked identities; only a confirmed email links automatically; one account per provider |
| Password reset by email; admin-made reset link; shell recovery | GitHub, Grafana, Gitea `admin user change-password` | Email link (60 min) with SMTP; Admin → People → reset link; `litledger reset-password`, `litledger admin promote` |
| Sessions and devices with sign-out; sessions end when the password changes | GitHub, Google | Settings → Profile; a new password ends every other session |
| Personal access tokens: named, expiring, scoped, shown once, last used, revocable | GitHub fine-grained PATs, Stripe and Anthropic API keys | API tokens with an expiry (30/90/365 days or never), project limits, library or full access |
| Authorized apps with revoke | GitHub "Authorized OAuth Apps", Google account | Settings → Connected apps (OAuth apps and terminals) |
| Bots that belong to the team, not a person | GitHub Apps, Slack bots, Grafana service accounts | Shared tokens (no owner), made by admins, e.g. for CI |
| Audit log | GitHub, Sentry, Grafana | Admin → Audit log; kept out of the journal agents read |
| Suspend vs delete | GitHub suspended users, Grafana disable | Disable (reversible, pauses their agents) and remove (for good, history kept) |

Left out on purpose:

- **A first-run "claim the server" page and an owner role.** Whoever holds the server's admin token is in charge;
  nobody can claim a fresh server by reaching it first.
- **2FA and passkeys.** Deferred. People who want a second factor sign in with Google or GitHub, which have one.
- **Email verification before using an account (with `SIGNUP=anyone`).** A password sign-up works at once. The address
  stays *unconfirmed* until the person opens a link (sent when SMTP is set up) in the browser where they're signed in
  to that account (as GitHub does: the link alone doesn't confirm). An unconfirmed address is never used to link a
  Google/GitHub account automatically: only confirmed emails link. Since anyone can sign up with any address, the
  first password reset of an unconfirmed account is a **change of owner**: it confirms the address and ends
  everything set up before (linked Google/GitHub accounts, API tokens, terminals, apps and their grants, sessions);
  see *Forgot password*. With `SIGNUP=domains` the address is the permission, so a password sign-up only happens
  through an emailed link (the account is made when it's opened), and without SMTP people with an allowed domain sign
  up with Google or GitHub.
- **Organisations, teams, guest/viewer roles and per-project roles.** One server is one team. Project limits (a
  person, token or app limited to some projects) cover "only this project".
- **Generic OIDC/SAML SSO and SCIM.** Google and GitHub cover the labs this is for.
- **Deleting your own account.** An admin removes people.

## 2. The model

- **The root admin** (`admin`) is the first-start admin token's person. It signs in with the token (the browser gets a
  normal session; the token isn't kept), has no email or password, and can't be demoted, disabled or removed, so the
  server always has an admin. Scripts use the same token, and `litledger approve` uses it in the container.
- **People** (`kind=human`) are `admin` or `member`. Everyone who creates an account, through sign-up or an invite
  without a role, is a member; an admin promotes them. They sign in with a password and/or linked Google/GitHub
  accounts.
- **Agents** (`kind=agent`) act for someone. An agent a person owns is one of:
  - an **API token** the person made (Settings → API tokens),
  - a **terminal** the person approved (`litledger login`),
  - an **app** the person connected over OAuth (Claude Code, Codex; one agent per person and app).

  An owned agent has library access, or full access when an admin made it so; it is never more than its owner (a full
  token of a demoted admin is a member). It stops working while its owner is disabled and dies when they're removed.
  **Shared agents** have no owner: tokens admins make for CI.
- **Credentials**: an API token (`ll_…`), a session cookie (30 days), OAuth access (1 hour, `/mcp` only) and refresh
  tokens (90 days, rotating), and one-time links: invite (7 days), password reset (60 minutes), email confirmation and
  domain sign-up (24 hours). All are stored as SHA-256 hashes; passwords as scrypt hashes.
- **Attribution**: every journal entry records the principal that wrote it, and an owned agent's owner never changes,
  so the UI shows "claude-code via ada". `X-Litledger-Agent` adds a free label (a sub-agent's name) on top.
- **Audit log**: every account, security and server change, with who, when and from where (§6).

## 3. Who may do what

The server checks all of this; the web app only hides what you can't use.

| | Member | Admin | Owned agent | Shared agent |
|---|---|---|---|---|
| Use the library (papers, notes, maps, tags, exports, uploads) | ✓ | ✓ | ✓ (its projects) | ✓ (its projects) |
| Delete, retract, merge, split, rename or archive library-wide items | ✓ (no project limit) | ✓ (no project limit) | without project limits | without project limits |
| Own profile, password, linked accounts, sessions | ✓ | ✓ | | |
| Own API tokens, connected apps; approve own terminal sign-ins; connect an app | ✓ | ✓ | | |
| Full-access tokens | | ✓ | | |
| Settings, sources, sign-in methods, backups | | ✓ | full access | full access |
| Invite; promote and demote; disable, remove; reset links; sign others out | | ✓ | full access | full access |
| Everyone's tokens and apps; shared tokens; audit log | | ✓ | full access | full access |

Guards: nobody can demote, disable, remove, reset or limit the root admin; you can't disable or remove yourself;
the last admin can't be demoted, disabled or removed (the root admin always counts). A limited person hands out no
more projects than they have. An admin can't clear the email of someone who signs in with it (a password, or no
linked Google/GitHub account): they would have no way to sign in.

**Project limits.** A person, token, terminal or app may be limited to some projects. Everything inside those
projects works as usual; a request for another project gets 403 (header or query) or an error (a tool's `project`).
The library itself (works, library entities, library-wide links and tags) is shared by every project, and one rule,
`Actor.check_library_change` (db.py), decides what a limited principal may do to it: **read and add, never destroy**.
It can capture papers, create library entities, add links and tags; it can't delete or rename a library entity,
retract a library-wide link, merge, rename or archive a library tag, or split a merged work: those change every
project, so they need a principal without project limits. A limited principal also reaches works only through its
projects (`works.VISIBLE`, set once per request): `work`, `read`, notes, tags, `update_work` and the REST work pages
answer "unknown work" for a work in none of its projects, library-wide search lists only its projects' works, and a
citekey from another project names nothing. Capturing a paper by its DOI, arXiv id or exact title still finds the
shared record (with any text stored for it) and adds it to the project: the library's papers are shared, project
limits protect what projects add to them.

## 4. Flows

**The admin.** On the first start the server writes the admin token; read it with
`docker compose exec litledger cat /data/tokens/admin`. On the sign-in page, *Sign in with admin token*. From there:
Admin → People to promote people, Admin → Sign-in to set the sign-up policy, Google/GitHub and SMTP. Wrong tokens get
one generic answer and are rate-limited per address.

**Creating an account.** *No account? Create one*: name, email, password, or *Sign up with Google/GitHub*. With
`SIGNUP=anyone` (the default) the account works at once; its email is confirmed by a link when SMTP is set up. With
`SIGNUP=domains` the email must be at an allowed domain: Google/GitHub check that themselves (verified emails only);
a password sign-up gets an emailed link that makes the account. With `SIGNUP=invited`: "Ask an admin for an invite."
A taken email is refused with "That email is already in use here. Sign in, or reset the password to sign everyone
else out": never "your password", since the account may be a stranger's who signed up with this address. Ten sign-ups
per address per hour. The confirmation link works only in a browser signed in to the account ("Sign in first"
otherwise); someone who gets a link for an account they didn't make resets its password instead.

**Becoming an admin.** An admin opens Admin → People → *Make admin*. It applies at once: the person's next page load
shows Admin, and their own password or Google/GitHub sign-in is all they use.

**Signing in.** Email and password, or *Continue with GitHub/Google* when set up. A wrong password and an unknown
email get the same answer. Ten wrong tries per email or address in 15 minutes are refused for a while. A disabled
person is told so only after the right password. Coming back from Google/GitHub only works in the browser that
started (a nonce cookie), so nobody can sign your browser into their account. If a Google/GitHub email matches an
account whose email isn't confirmed, nothing links: "An account already uses that email. Sign in with its password,
then link it under Settings → Profile."

**Invites.** Admin → People → *Invite*: email, role, optional projects. With SMTP the link is emailed; either way the
admin sees it to copy. The link opens *Join litledger*: choose a name and password, or continue with Google/GitHub
(any account: the invite is the proof, and the person gets the invited email, confirmed). A new invite to the same
email replaces the old one; links expire after 7 days and work once. Invites work under every sign-up policy.

**Forgot password.** *Forgot password?* With SMTP: "If an account uses that email, a reset link is on its way."
Without: "This server can't send email. Ask an admin for a reset link", plus the shell command. Either way the answer
doesn't reveal whether the account exists. The link opens *Set a new password*; it signs you in and ends every other
session. Admins make the same link under Admin → People; on the server, `litledger reset-password EMAIL` prints one.
On an account whose email was never confirmed, the reset also confirms it and takes the account over: linked
Google/GitHub accounts, API tokens, terminals, connected apps (with their OAuth grants), sessions and pending links all
end in the same transaction, audited as `account.reclaim`, and the page says "Signed out other devices and removed
links and tokens made before you confirmed this email."

**Your profile** (Settings → Profile). Rename yourself; change your email (admins at once; members through a link to
the new address, or an admin when there's no SMTP); set or change your password (other sessions end); link or unlink
Google/GitHub (you can't unlink your only way in); see where you're signed in and sign out one or all other sessions;
appearance. The root admin's profile has only its name, role, sessions and appearance.

**Agents.** Settings → API tokens: *New token* (name, expiry, projects; admins also choose library or full access);
the token is shown once, with the lines to use it. Settings → Connected apps: approve or deny waiting
`litledger login` codes (the terminal becomes yours: "laptop via ada"), see your apps and terminals with when they
were last used, limit their projects, disconnect them. Apps connect by adding `<server>/mcp`; the browser opens the
consent page (*Connect Claude Code?*), where any signed-in person allows it for all or some projects. Terminals and
tokens have library access, so an admin who wants `litledger config` from a terminal sets `LITLEDGER_TOKEN` to a
full-access token (or the admin token).

**Admins** (Admin → People): make admin or member, edit email and projects, make a reset link, sign someone out
everywhere, disable (signed out, their agents paused) or enable, remove (their sign-ins and agents end for good; what
they wrote stays attributed). Admin → Apps & tokens shows everyone's agents and the shared tokens.

**Recovery, in the container.** `litledger reset-password EMAIL` (a one-time link); `litledger admin list`;
`litledger admin promote EMAIL` (makes them an admin and enables them); `litledger token create admin --admin`
re-issues the admin token and rewrites `/data/tokens/admin`; `litledger approve CODE` approves a terminal with it.

## 5. Settings vs Admin in the web app

- **Settings** (everyone): Profile, Connected apps, API tokens.
- **Admin** (admins only; the nav item isn't shown to members): People (and pending invites), Apps & tokens, Sources,
  Sign-in (sign-up policy, Google/GitHub, SMTP with a test email, trusted app hosts), Server (public address, contact
  email, fetching, MCP toolsets), Backups, Audit log.

## 6. The audit log

**What's in it.** Sign-ins and sign-outs (password, admin token, Google/GitHub), failed and refused ones, rate limits
hit, sign-ups (and domain sign-up links opened), renames, password changes (and wrong current passwords), reset
requests (whether or not the email has an account; the answer to the browser never says), reset links and resets,
email change requests and confirmations, linking and unlinking Google/GitHub (and failed links), ending your own
sessions (one or all), takeovers of unconfirmed accounts by a reset (`account.reclaim`), invites made, replaced,
revoked, accepted and opened after they expired, role changes,
disabling, enabling, removing, edits, tokens made, re-issued and removed, terminals approved and denied, apps
registered, connected, declined, disconnected and revoking their own tokens, a reused refresh token (suspected theft:
the whole grant is revoked), settings, sources and backups. Library work goes to the journal instead (Activity).

**How it's guaranteed.** The action writes its audit row itself, with `tx.audit` inside the same database transaction
as the change, from a small request context (who, address, user agent; or `cli`, `schedule`, `startup`). Routes and
the CLI never write the log, so they can't forget it, and a row exists exactly when its change does: a refused or
failed change leaves none, a change never commits without its row. Two tests keep it that way:

- every mutating route (POST/PUT/PATCH/DELETE, and the few GETs that change state) is exercised and must write its
  declared audit op, or its journal entry for library data, or be listed with a reason (OAuth token rotation, the
  in-memory start of a terminal sign-in, test emails and source tests, which change nothing);
- in tests, a transaction that changed rows but wrote neither a journal entry nor an audit row fails; bookkeeping
  (job state, caches, call counts, last seen, the reading log, derived indexes) must say why with `internal=…`, and a
  bare `db.write()` doesn't exist: it takes that reason too. Production never runs the check.

**Noise.** Repeated failures (the same event, target and address within 15 minutes) count on one row ("×6"), so a
guessing attack is one line, not thousands. Routine hourly OAuth token rotation isn't recorded; the grant's start and
end are.

**Reading it.** Admin → Audit log: filter by person (what they and their agents did, or what was done to them), by
kind (sign-ins, accounts, people, tokens, apps, server) and to problems only. Failures are red, security events
(role changes, disabling, removals, re-issued tokens, reset links, signing someone out, token theft) amber.

## 7. Security notes

- The admin token is the root of trust: anyone with a shell in the container can read it. Keep it there; re-issue it
  with `litledger token create admin --admin` if it leaks.
- Secrets at rest are hashes; tokens are compared by hash lookup, passwords with `hmac.compare_digest`; scrypt runs
  for unknown emails too.
- Cookie writes, including `/auth/*` posts, need `X-Litledger-CSRF: 1`; the server grants no CORS outside OAuth. The
  session cookie is HttpOnly, SameSite=Lax, Secure over https.
- Rate limits (sliding windows, in memory; a refusal is 429, recorded once per window as `limit.hit`):

  | What | Limit |
  |---|---|
  | Password sign-in (`/auth/password`) | 10 wrong tries per email and per address in 15 min |
  | Admin-token sign-in, reset and invite links (`/auth/token`, `/auth/reset`, `/auth/invite`) | 30 per address in 15 min |
  | Sign-ups (`/auth/signup`) | 10 per address per hour |
  | Reset emails (`/auth/forgot`) | 5 per email and per address in 15 min |
  | Google/GitHub round trips started (`/auth/{provider}/start`) | 30 per address in 10 min; at most 1,000 in flight |
  | Invites | 30 per admin per hour |
  | OAuth dynamic registration (`/oauth/register`) | 20 per address per hour; a burst from one address is one `app.register` row with a count; registrations never used for a token are capped at 500 (the oldest are forgotten) |
  | OAuth authorization requests (`/oauth/authorize`) | 30 per address in 10 min; at most 1,000 waiting for consent and 1,000 unexchanged codes in memory (the oldest are dropped) |
  | Terminal sign-in (`/api/v1/login/start`) | 10 per address in 10 min; at most 3 waiting per address and 20 on the server; approvers see the newest 5, and any waiting code can still be approved |

  "Per address" is the client's IP address. Behind a reverse proxy, set `LITLEDGER_TRUSTED_PROXIES` to the proxy's
  address as the container sees it, so the server reads the client's address (and https) from `X-Forwarded-For` and
  `X-Forwarded-Proto`. Otherwise every client shares the proxy's address and one person's wrong passwords lock
  everyone out; the server logs a warning when `PUBLIC_URL` is https but proxied requests arrive as http.
- Sessions end when a password changes or is reset, and when a person is disabled or removed. Roles are read on every
  request, so a promotion or demotion applies at once.
- Sign-up is open by default. On a server others can reach, tighten it under Admin → Sign-in (allowed domains or
  invite only) before sharing the address.
- Google/GitHub client secrets and the SMTP password live in the settings table in clear (the server must use them);
  they are never returned by the API. Members and agents get only the library's settings from `/api/v1/config`
  (sources, toolsets, upload size; whether a key is set), not sign-in, mail, address or backup settings, nor the setup
  warnings.
- OAuth access tokens are bound to the server's `/mcp` URL. Both `PUBLIC_URL` and the address a request came in on
  count, and a refresh re-binds the grant to the current `PUBLIC_URL`, so changing the address doesn't break connected
  apps (an app reached only through an address that stops working must reconnect).
- Saved exports (`/api/v1/exports/…`) carry the project they were made in and are served only to callers that may use
  it; they are deleted after 7 days (and beyond the newest 200).
