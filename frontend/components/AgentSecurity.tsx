"use client";
// In the agent editor: which guardrail profiles watch this agent, and what its identity may do and hand over.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "./InfoTip";

export type Identity = { scopes?: string[]; delegates?: string[] };
type Profile = { id: string; name: string; mode: string };
type IdCfg = { trust_domain: string; scopes: Record<string, string> };
type Pass = { source: string; claims: { scope?: string; exp?: number } | null; fingerprint: string; expires_in: number };

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export default function AgentSecurity({ agentId, isNew, guardrails, identity, others, onChange }:
  { agentId: string; isNew: boolean; guardrails: string[]; identity: Identity; others: { id: string; name: string }[]; onChange: (g: string[], i: Identity) => void }) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [cfg, setCfg] = useState<IdCfg | null>(null);
  const [aud, setAud] = useState("https://api.example.com");
  const [pass, setPass] = useState<Pass | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<{ profiles: Profile[] }>("/api/guardrails").then((g) => setProfiles(g.profiles)).catch(() => setProfiles([]));
    api<IdCfg>("/api/identity/config").then(setCfg).catch(() => setCfg(null));
  }, []);
  const mode = guardrails.includes("none") ? "none" : guardrails.length ? "pick" : "default";
  const setMode = (m: string) => onChange(m === "none" ? ["none"] : m === "pick" ? profiles.slice(0, 1).map((p) => p.id) : [], identity);
  const autoScopes = !("scopes" in identity), autoDel = !("delegates" in identity);
  const scopes = identity.scopes ?? [], delegates = identity.delegates ?? [];
  const tryPass = async () => {
    setErr(""); setPass(null);
    try { setPass(await api<Pass>(`/api/identity/agents/${agentId}/token`, { method: "POST", body: JSON.stringify({ audience: aud }) })); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div data-testid="agent-security">
      <h3 id="ag-guardrails">Guardrails <InfoTip id="agent-guardrails" /></h3>
      <div className="row">
        {([["default", "Default profiles"], ["none", "None (this agent runs unchecked)"], ["pick", "Choose profiles"]] as const).map(([k, l]) => (
          <label key={k} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="radio" name="ag-guard" checked={mode === k} onChange={() => setMode(k)} />{l}</label>))}
      </div>
      {mode === "pick" && <div className="row">{profiles.map((p) => (
        <label key={p.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={guardrails.includes(p.id)} onChange={() => onChange(toggle(guardrails, p.id), identity)} />{p.name} <span className="pill">{p.mode}</span></label>))}</div>}
      <div className="mut small">Guardrails only act when they are switched on in <a href="/settings/#security">Settings → Security</a>.</div>

      <h3 id="ag-identity">Identity <InfoTip id="identity" /></h3>
      <div className="small" style={{ overflowWrap: "anywhere" }}>{cfg ? `spiffe://${cfg.trust_domain}/agent/${agentId || "(new agent)"}` : "…"}</div>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6 }}>
        <input type="checkbox" checked={autoScopes} onChange={(e) => { const { scopes, ...rest } = identity; onChange(guardrails, e.target.checked ? rest : { ...rest, scopes: [] }); }} />
        What it may do: work it out from its tools <InfoTip id="identity-scopes" /></label>
      {!autoScopes && <div className="row">{Object.entries(cfg?.scopes ?? {}).map(([s, d]) => (
        <label key={s} title={d} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={scopes.includes(s)} onChange={() => onChange(guardrails, { ...identity, scopes: toggle(scopes, s) })} />{s}</label>))}</div>}
      <label style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6 }}>
        <input type="checkbox" checked={autoDel} onChange={(e) => { const { delegates, ...rest } = identity; onChange(guardrails, e.target.checked ? rest : { ...rest, delegates: [] }); }} />
        Who it may hand work to: its sub-agents</label>
      {!autoDel && <div className="row">{others.map((a) => (
        <label key={a.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={delegates.includes(a.id)} onChange={() => onChange(guardrails, { ...identity, delegates: toggle(delegates, a.id) })} />{a.name}</label>))}</div>}
      {!isNew && (
        <div className="row" style={{ marginTop: 8 }}>
          <input aria-label="Audience" value={aud} onChange={(e) => setAud(e.target.value)} style={{ minWidth: 220 }} />
          <button onClick={tryPass}>Try a pass</button>
          {pass && <span className="pill ok" role="status">pass {pass.fingerprint} · {pass.claims?.scope || "no scopes"} · {pass.expires_in}s</span>}
          {err && <span className="pill bad" role="alert">{err}</span>}
        </div>)}
    </div>
  );
}
