"""Settings. Only bootstrap values come from the process environment (set by the Docker image); everything a user
configures — API keys, contact email, sources, defaults — is saved from the web UI (or `litledger config`) into the
database and applied at runtime. FIELDS declares every such setting except the sources' own keys (providers add
those)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PREFIX = "LITLEDGER_"

# MCP toolsets (the TOOLS setting and the X-Litledger-Tools header choose among them).
TOOLSETS = {"core": ["find", "discover", "resolve", "work", "read", "note", "tag", "export"],
            "graph": ["graph", "snowball", "entity", "link"], "maps": ["map_get", "map_edit"], "check": ["check"],
            "admin": ["status", "update_work"]}


def _field(key: str, label: str, help: str = "", *, secret: bool = False, provider: str | None = None,
           group: str | None = None, **extra) -> dict:
    """A setting as the web UI shows it. personal=True: an agent may learn whether it is set, never its value."""
    out = {"key": key, "label": label, "secret": secret, "provider": provider, **extra, "help": help}
    if group:
        out["group"] = group
    return out


FIELDS = [
    # -- sources and general behaviour -------------------------------------------------------------------------
    _field("CONTACT_EMAIL", "Contact email", "Needed by Unpaywall; speeds up Crossref and OpenAlex.",
           personal=True),
    _field("NCBI_API_KEY", "NCBI (PubMed) API key", "Optional: faster PubMed. ncbi.nlm.nih.gov/account/settings",
           secret=True, provider="pubmed"),
    _field("OPENCITATIONS_TOKEN", "OpenCitations token", "Optional. https://opencitations.net/accesstoken", secret=True,
           provider="opencitations"),
    _field("ENABLE", "Extra sources to enable",
           "Comma-separated source ids.", multi=True),
    _field("DISABLE", "Sources to disable", "Comma-separated source ids.", multi=True),
    _field("TOOLS", "Default MCP toolsets",
           "Tools apps get by default. Fewer tools, fewer tokens.",
           multi=True, default="core", choices=[*TOOLSETS, "all"]),
    _field("FETCH_FULLTEXT", "Fetch full text", "",
           default="on_demand", choices=["on_demand", "on_add"]),
    _field("MAX_UPLOAD_MB", "Max upload size (MB)", "", default="100", type="int"),
    _field("ALLOW_PRIVATE_FETCH", "Allow fetching from private networks",
           "Leave off unless you fetch papers from your own network.", default="0", choices=["0", "1"]),
    _field("QUIET_RECOMMENDATIONS", "Silence setup warnings",
           "In the server log and what agents see. Admin still lists them.", default="0", choices=["0", "1"]),
    # -- signing in (Admin → Sign-in) ---------------------------------------------------------------------------
    _field("PUBLIC_URL", "Public address",
           "e.g. https://lit.example.com. Leave empty for local use. Apps reached through an old address that "
           "stops working must reconnect.", group="access"),
    _field("GITHUB_CLIENT_ID", "GitHub client ID",
           "From github.com/settings/developers.",
           group="access", personal=True),
    _field("GITHUB_CLIENT_SECRET", "GitHub client secret", secret=True, group="access"),
    _field("GOOGLE_CLIENT_ID", "Google client ID",
           "From a Google Cloud OAuth client.",
           group="access", personal=True),
    _field("GOOGLE_CLIENT_SECRET", "Google client secret", secret=True, group="access"),
    _field("SIGNUP", "Who can sign up", "Everyone else needs an invite.", group="access",
           default="anyone", choices=["anyone", "domains", "invited"]),
    _field("SIGNUP_DOMAINS", "Email domains that can sign up", "Comma-separated, e.g. uni.edu. A password sign-up "
           "confirms the address by email first.", group="access"),
    _field("SMTP_HOST", "SMTP server", "For invites and password resets. Without it, admins copy the links.",
           group="access"),
    _field("SMTP_PORT", "SMTP port", "", group="access", default="587", type="int"),
    _field("SMTP_USER", "SMTP user", group="access", personal=True),
    _field("SMTP_PASSWORD", "SMTP password", "", secret=True, group="access"),
    _field("SMTP_FROM", "Send email from", "Defaults to the SMTP user.", group="access", personal=True),
    _field("OAUTH_CLIENT_HOSTS", "Trusted app hosts",
           "Leave as is unless an app can't sign in.",
           group="access", default="claude.ai,chatgpt.com"),
    # -- backups ----------------------------------------------------------------------------------------------
    _field("BACKUP_EVERY_HOURS", "Back up every (hours)", "0 turns backups off.", group="backup", default="24", type="int"),
    _field("BACKUP_KEEP", "Backups to keep", "", group="backup", default="7", type="int"),
    _field("BACKUP_DIR", "Backup folder", "Leave empty for ./backups.", group="backup"),
]
DEFAULTS = {f["key"]: f["default"] for f in FIELDS if "default" in f}


def _list(value: str | None) -> list[str]:
    return [v.strip().lower() for v in (value or "").split(",") if v.strip()]


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # bootstrap (environment; fixed by the container)
    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"
    port: int = 8765
    offline: bool = False  # tests: never touch the network
    # addresses (IPs, networks or "*") whose X-Forwarded-For/-Proto headers are believed: the reverse proxy in front
    trusted_proxies: str = "127.0.0.1"
    # runtime (saved in the database from the web UI)
    values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if environ is None else environ)
        return cls(data_dir=Path(env.get(PREFIX + "DATA") or "/data"), host=env.get(PREFIX + "HOST") or "0.0.0.0",
                   port=int(env.get(PREFIX + "PORT") or 8765), offline=_bool(env.get(PREFIX + "OFFLINE")),
                   trusted_proxies=(env.get(PREFIX + "TRUSTED_PROXIES") or "127.0.0.1").strip())

    def with_values(self, values: dict[str, str]) -> "Settings":
        return replace(self, values=dict(values))

    def get(self, name: str) -> str:
        """A runtime setting or provider credential, e.g. get('S2_API_KEY'); unset settings give their declared default."""
        name = name.upper()
        return (self.values.get(name) or DEFAULTS.get(name, "")).strip()

    def number(self, name: str) -> int:
        """An integer setting; a value that is not a number counts as the default."""
        try:
            return int(self.get(name))
        except ValueError:
            return int(DEFAULTS.get(name.upper()) or 0)

    @property
    def contact_email(self) -> str:
        return self.get("CONTACT_EMAIL")

    @property
    def enable(self) -> list[str]:
        return _list(self.get("ENABLE"))

    @property
    def disable(self) -> list[str]:
        return _list(self.get("DISABLE"))

    @property
    def quiet_recommendations(self) -> bool:
        return _bool(self.get("QUIET_RECOMMENDATIONS"))

    @property
    def allow_private_fetch(self) -> bool:
        return _bool(self.get("ALLOW_PRIVATE_FETCH"))

    @property
    def tools(self) -> list[str]:
        return _list(self.get("TOOLS"))

    @property
    def fetch_fulltext(self) -> str:
        return self.get("FETCH_FULLTEXT").lower()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ledger.sqlite3"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def token_dir(self) -> Path:
        return self.data_dir / "tokens"
