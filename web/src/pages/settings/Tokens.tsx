import { useCallback, useEffect, useState } from "react";
import { Agent, api } from "../../lib/api";
import { Icon } from "../../ui/Icon";
import { useOverlays } from "../../ui/overlays";
import { AgentTable, TokenDialog, useTokenForm } from "./agents";

export function TokensTab() {
  const { toast } = useOverlays();
  const ask = useTokenForm();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [made, setMade] = useState<{ name: string; token: string } | null>(null);
  const load = useCallback(() => { api.myAgents().then((a) => setAgents(a.filter((x) => x.origin === "token"))); }, []);
  useEffect(load, [load]);
  const create = async () => {
    const r = await ask("New API token", { expires: "90" });
    if (!r) return;
    try { setMade(await api.createMyToken(r)); load(); } catch (e) { toast(String(e), true); }
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <div className="grow"><h2>API tokens</h2><div className="small muted">For scripts, CI and apps that take a header. They act for you.</div></div>
        <button className="btn primary" onClick={create}><Icon name="plus" />New token</button>
      </div>
      {agents && <AgentTable agents={agents} empty="No tokens yet." onChange={load} />}
      {made && <TokenDialog made={made} onClose={() => setMade(null)} />}
    </div>
  );
}
