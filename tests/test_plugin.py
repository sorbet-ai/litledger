"""The Claude Code and Codex plugins in this repo stay in step with the server's bundled skill."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_skill_matches_bundled_skill():
    bundled = (ROOT / "src/litledger/skill/SKILL.md").read_text(encoding="utf-8")
    plugin = (ROOT / "plugins/litledger/skills/litledger/SKILL.md").read_text(encoding="utf-8")
    assert plugin == bundled, "run: cp src/litledger/skill/SKILL.md plugins/litledger/skills/litledger/SKILL.md"


def test_marketplaces_point_at_the_plugin():
    claude = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    assert claude["plugins"][0]["source"] == "./plugins/litledger" and codex["plugins"][0]["source"]["path"] == "./plugins/litledger"
    for manifest in ("plugins/litledger/.claude-plugin/plugin.json", "plugins/litledger/.codex-plugin/plugin.json"):
        data = json.loads((ROOT / manifest).read_text(encoding="utf-8"))
        assert data["name"] == "litledger"
    codex_manifest = json.loads((ROOT / "plugins/litledger/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert codex_manifest["mcpServers"] == {}  # no server built in: Codex has no plugin options for the address, so it is the user's own config
    servers = json.loads((ROOT / "plugins/litledger/.mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["litledger"]["url"] == "${user_config.server}/mcp"  # the address is a plugin option, set with --config
    claude_manifest = json.loads((ROOT / "plugins/litledger/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert claude_manifest["userConfig"]["server"]["default"] == "http://127.0.0.1:8765"
    assert "Authorization" not in servers["litledger"].get("headers", {})  # apps sign in with OAuth, never a pasted token
