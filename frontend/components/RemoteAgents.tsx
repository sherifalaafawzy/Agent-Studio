"use client";
// Remote A2A agents: the "create an agent" choice, connecting one (card preview, test), and managing it afterwards.
import { useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import { ScopeBadge } from "@/components/CustomEndpoints";
import { toast } from "@/components/Nav";
import { useDirty } from "@/lib/dirty";

export type CardSkill = { id: string; name: string; description?: string; tags?: string[] };
export type CardSummary = { name?: string; description?: string; version?: string; provider?: string; streaming?: boolean | null; skills: CardSkill[]; auth: string[]; interfaces: string[]; input_modes: string[]; output_modes: string[] };
export type Remote = {
  id: string; label: string; url: string; enabled: boolean; has_token: boolean; name?: string | null; description?: string | null;
  auth_kind: "bearer" | "header"; auth_header: string; delegates: string[]; network_scope: string; scope_label: string; card: CardSummary | null;
};
type Preview = { ok: boolean; base_url?: string; card?: CardSummary; suggested_id?: string; scope?: string; kind?: string; title?: string; detail?: string };
type AgentRef = { id: string; name: string };

export function AgentCreateChoice({ onWizard, onA2A }: { onWizard: () => void; onA2A: () => void }) {
  return (
    <div className="ag-main" data-testid="create-choice">
      <h3 style={{ marginTop: 0 }}>Add an agent</h3>
      <p className="page-sub">Build a new agent here, or connect one that already runs somewhere else.</p>
      <div className="grid">
        <button className="card choice" onClick={onWizard} style={{ textAlign: "left", cursor: "pointer" }}>
          <b>New agent (wizard)</b>
          <div className="small mut">Answer a few simple questions: what it is for, how it should work, what it may use. Agent Studio runs it for you.</div>
        </button>
        <button className="card choice" onClick={onA2A} style={{ textAlign: "left", cursor: "pointer" }}>
          <b>Connect an A2A agent</b>
          <div className="small mut">Use an agent that someone else runs, over the open A2A protocol. You give its address; it keeps running where it is, with its own models and billing.</div>
        </button>
      </div>
    </div>
  );
}

export function CardInfo({ card }: { card: CardSummary }) {
  return (
    <div className="small" data-testid="card-info">
      <div><b>{card.name || "(unnamed agent)"}</b>{card.version ? <span className="mut"> · version {card.version}</span> : null}{card.provider ? <span className="mut"> · by {card.provider}</span> : null}</div>
      {card.description && <div style={{ margin: "4px 0" }}>{card.description}</div>}
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className={`pill ${card.streaming ? "ok" : ""}`}>{card.streaming ? "streams answers" : "answers in one piece"}</span>
        {card.auth.length ? card.auth.map((a) => <span key={a} className="pill">{a}</span>) : <span className="pill">no sign-in declared</span>}
      </div>
      <div style={{ marginTop: 6 }}><b>Skills</b> ({card.skills.length})</div>
      {card.skills.length === 0 && <div className="mut">The agent does not list any skills.</div>}
      <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>{card.skills.map((s) => (
        <li key={s.id}><b>{s.name || s.id}</b>{s.description ? <> — <span className="mut">{s.description}</span></> : null}</li>))}</ul>
    </div>
  );
}

function AuthFields({ v, set }: { v: { auth_kind: string; token: string; auth_header: string; has_token?: boolean }; set: (k: string, val: string) => void }) {
  return (<>
    <label>Sign-in
      <select value={v.auth_kind} onChange={(e) => set("auth_kind", e.target.value)}>
        <option value="bearer">Bearer token (Authorization header)</option><option value="header">API key in a header of my choice</option></select></label>
    {v.auth_kind === "header" && <label>Header name<input value={v.auth_header} placeholder="X-API-Key" onChange={(e) => set("auth_header", e.target.value)} /></label>}
    <label>{v.auth_kind === "header" ? "API key" : "Token"} (write-only, optional){v.has_token && <span className="pill ok" style={{ marginLeft: 6 }}>stored</span>}
      <input type="password" autoComplete="off" value={v.token} placeholder={v.has_token ? "(unchanged)" : "leave blank if it needs none"} onChange={(e) => set("token", e.target.value)} /></label>
  </>);
}

export function A2AConnect({ onConnected, onCancel }: { onConnected: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState({ url: "", auth_kind: "bearer", token: "", auth_header: "", label: "" });
  const [pv, setPv] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const set = (k: string, v: string) => { setF((s) => ({ ...s, [k]: v })); if (k !== "label") setPv(null); };
  useDirty("a2a-connect", !!(f.url || f.token));
  const creds = () => ({ url: f.url, auth_kind: f.auth_kind, token: f.token, auth_header: f.auth_header });

  async function check() {
    setBusy(true); setPv(null); setMsg("");
    try { const r = await api<Preview>("/api/access/remotes/preview", { method: "POST", body: JSON.stringify(creds()) }); setPv(r); if (r.ok && !f.label) setF((s) => ({ ...s, label: r.card?.name ?? "" })); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
    setBusy(false);
  }
  async function connect() {
    try { const r = await api<Remote>("/api/access/remotes/connect", { method: "POST", body: JSON.stringify({ ...creds(), label: f.label }) }); toast(`Connected ${r.label}`); onConnected(r.id); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  return (
    <div className="card ag-main" data-testid="a2a-connect">
      <div className="card-head"><b>Connect an A2A agent</b><InfoTip id="a2a-remote" /></div>
      <p className="small mut">Paste the agent&apos;s address. Agent Studio reads its <b>agent card</b> (its name, skills and how to sign in), then you can connect it and let your own agents call it. The agent keeps running where it is.</p>
      <div className="grid">
        <label>Agent address<input value={f.url} placeholder="https://agents.example.com/travel/" onChange={(e) => set("url", e.target.value)} />
          <span className="small mut">The base address, or the full link to <code>/.well-known/agent-card.json</code>.</span></label>
        <AuthFields v={f} set={set} />
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={check} disabled={busy || !f.url.trim()}>{busy ? "Reading the card…" : "Check the agent"}</button>
        {pv && !pv.ok && <span className="small err" role="alert">{pv.title}: {pv.detail}</span>}
      </div>
      {pv?.ok && pv.card && (
        <div style={{ marginTop: 10 }} data-testid="card-preview">
          <div className="row"><ScopeBadge scope={pv.scope} /><span className="pill">Remote A2A</span></div>
          <CardInfo card={pv.card} />
          <label style={{ marginTop: 8 }}>Name in your list<input value={f.label} onChange={(e) => set("label", e.target.value)} /></label>
        </div>)}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!pv?.ok} onClick={connect}>Connect</button>
        <button onClick={onCancel}>Cancel</button><span className="small err" role="alert">{msg}</span>
      </div>
    </div>
  );
}

export function RemotePanel({ r, agents, onChanged, onDeleted }: { r: Remote; agents: AgentRef[]; onChanged: () => void; onDeleted: () => void }) {
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ label: r.label, url: r.url, auth_kind: r.auth_kind, auth_header: r.auth_header, token: "" });
  const [deleg, setDeleg] = useState<string[]>(r.delegates);
  const [prompt, setPrompt] = useState("Say hello and tell me in one sentence what you can do.");
  const [ping, setPing] = useState<{ ok: boolean; title: string; detail: string; reply?: string; ms?: number } | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const dirty = edit && (f.label !== r.label || f.url !== r.url || f.auth_kind !== r.auth_kind || f.auth_header !== r.auth_header || !!f.token);
  useDirty(`remote:${r.id}`, dirty);
  const same = deleg.length === r.delegates.length && deleg.every((d) => r.delegates.includes(d));

  async function call<T>(label: string, fn: () => Promise<T>, after?: (x: T) => void) {
    setBusy(label); setMsg("");
    try { const x = await fn(); after?.(x); } catch (e) { setMsg("⚠ " + (e as Error).message); }
    setBusy("");
  }
  const put = (patch: any, done: string) => call("save", () => api(`/api/access/remotes/${r.id}`, { method: "PUT", body: JSON.stringify(patch) }), () => { toast(done); setEdit(false); onChanged(); });
  const card = r.card;

  return (
    <div className="card ag-main" data-testid="remote-panel">
      <div className="row">
        <b>{r.label}</b><span className="pill acc">Remote A2A</span><ScopeBadge scope={r.network_scope} /><InfoTip id="a2a-remote" />
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={r.enabled} onChange={(e) => put({ enabled: e.target.checked }, e.target.checked ? "Turned on" : "Turned off")} /> enabled</label>
        <span className="grow" />
        <button disabled={!!busy} onClick={() => call("refresh", () => api<{ ok: boolean; title?: string; detail?: string }>(`/api/access/remotes/${r.id}/refresh`, { method: "POST" }),
          (x) => { if (x.ok) { toast("Card refreshed"); onChanged(); } else setMsg(`⚠ ${x.title}: ${x.detail}`); })}>{busy === "refresh" ? "Reading…" : "Refresh card"}</button>
        <button onClick={() => { if (!confirm(`Remove the remote agent "${r.label}"? Your agents will no longer be able to call it.`)) return;
          call("delete", () => api(`/api/access/remotes/${r.id}`, { method: "DELETE" }), () => { toast(`Removed ${r.label}`); onDeleted(); }); }}>Remove</button>
      </div>
      <div className="small mut" style={{ margin: "4px 0 8px" }}>{r.url} · {r.scope_label}. It runs on its own servers and billing, outside Agent Studio&apos;s spend caps.</div>
      {card ? <CardInfo card={card} /> : <div className="small mut">No agent card stored yet. Press Refresh card.</div>}

      <h4 style={{ marginTop: 14 }}>Try it</h4>
      <div className="row"><input style={{ flex: 1, minWidth: 200 }} aria-label="Test message" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <button disabled={!!busy || !prompt.trim() || !r.enabled} title={r.enabled ? "" : "Turn the agent on first"}
          onClick={() => call("ping", () => api<NonNullable<typeof ping>>(`/api/access/remotes/${r.id}/ping`, { method: "POST", body: JSON.stringify({ message: prompt }) }), setPing)}>{busy === "ping" ? "Asking…" : "Send test message"}</button></div>
      {ping && <div className="small" role="status" data-testid="ping-result" style={{ marginTop: 6 }}>
        <span className={`pill ${ping.ok ? "ok" : "bad"}`}>{ping.ok ? "answered" : "failed"}</span> <b>{ping.title}</b> — {ping.detail}{ping.ok && ping.reply ? <> “{ping.reply}”</> : null}</div>}

      <h4 style={{ marginTop: 14 }}>Which of your agents may call it <InfoTip id="a2a-delegates" /></h4>
      <div className="row">{agents.map((a) => (
        <label key={a.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={deleg.includes(a.id)} onChange={() => setDeleg((d) => (d.includes(a.id) ? d.filter((x) => x !== a.id) : [...d, a.id]))} />{a.name}</label>))}</div>
      <div className="small mut">Ticked agents get the <code>call_remote_agent</code> tool. With nothing ticked, any agent that already has that tool may call it.</div>
      <div className="row" style={{ marginTop: 6 }}>
        <button disabled={same || !!busy} onClick={() => call("deleg", () => api(`/api/access/remotes/${r.id}/delegates`, { method: "PUT", body: JSON.stringify({ agents: deleg }) }), () => { toast("Saved who may call it"); onChanged(); })}>Save</button></div>

      <h4 style={{ marginTop: 14 }}>Connection <button className="ghost small" onClick={() => setEdit((e) => !e)}>{edit ? "Close" : "Edit"}</button></h4>
      {edit && (<>
        <div className="grid">
          <label>Name in your list<input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></label>
          <label>Agent address<input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></label>
          <AuthFields v={{ ...f, has_token: r.has_token }} set={(k, v) => setF({ ...f, [k]: v })} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="pri" disabled={!dirty || !!busy} onClick={() => put({ label: f.label, url: f.url, auth_kind: f.auth_kind, auth_header: f.auth_header, ...(f.token ? { token: f.token } : {}) }, "Saved")}>Save connection</button>
          {r.has_token && <button onClick={() => put({ token: "" }, "Token cleared")}>Clear stored token</button>}
        </div></>)}
      <span className="small err" role="alert">{msg}</span>
    </div>
  );
}
