import { useCallback, useEffect, useState } from "react";
import { Agent, api } from "../../lib/api";
import { Icon } from "../../ui/Icon";
import { useOverlays } from "../../ui/overlays";
import { AgentTable, TokenDialog, useTokenForm } from "../settings/agents";

/** Everyone's apps, terminals and tokens, plus shared tokens that belong to no one (CI, the first admin token). */
export function AgentsTab() {
  const { toast } = useOverlays();
  const ask = useTokenForm();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [made, setMade] = useState<{ name: string; token: string } | null>(null);
  const load = useCallback(() => { api.principals().then((r) => setAgents(r.agents)); }, []);
  useEffect(load, [load]);
  const create = async () => {
    const r = await ask("New shared token", { expires: "never" });
    if (!r) return;
    try { setMade(await api.createSharedToken(r)); load(); } catch (e) { toast(String(e), true); }
  };
  const shared = agents?.filter((a) => !a.owner) ?? [];
  const owned = agents?.filter((a) => a.owner) ?? [];
  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <div className="grow"><h2>Shared tokens</h2><div className="small muted">Belong to no one, e.g. for CI. Keep working when people leave.</div></div>
          <button className="btn primary" onClick={create}><Icon name="plus" />New shared token</button>
        </div>
        {agents && <AgentTable agents={shared} owners empty="None." onChange={load} />}
      </div>
      <div className="panel">
        <div className="panel-head"><div className="grow"><h2>People's apps and tokens</h2>
          <div className="small muted">They act for the person who made them, and stop when that person is disabled or removed.</div></div></div>
        {agents && <AgentTable agents={owned} owners empty="Nothing yet." onChange={load} />}
      </div>
      {made && <TokenDialog made={made} onClose={() => setMade(null)} />}
    </>
  );
}
