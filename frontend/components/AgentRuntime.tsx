"use client";
// In the agent editor: where this agent runs (this PC, Docker, AgentCore, Vertex, Foundry), which sandbox its commands use and which data sources it may
// read. For a saved agent on a remote runtime it also walks through deploying: plan -> shadow version -> test -> switch traffic -> roll back.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Tgt = { id: string; label: string; enabled: boolean; configured: boolean; remote: boolean; cost_note: string };
type SbKind = { id: string; label: string; available: boolean; detail: string; experimental: boolean; notice: string };
type Src = { id: string; label: string; kind: string; enabled: boolean };
type AgentLike = { id: string; runtime?: string; sandbox?: string; data?: string[] };
type Rel = { id: string; target: string; version: number; state: string; live: boolean; shadow: boolean; image: string; prev_id: string | null; detail: string; health: { ok?: boolean; detail?: string; smoke?: boolean } };
type Change = { field: string; before: string; after: string };
type Compat = { level: string; feature: string; text: string };
type Mig = { runtime: string; live: Rel | null; stale: boolean; changes: Change[]; compat: Compat[]; candidate: Rel | null; message: string };
type Plan = { target: string; compat: Compat[]; blocked: boolean; diff: Change[]; shadow: boolean; image: string; image_ok: boolean; commands: string[]; bundle: { folder: string; files: string[] };
  cost_note: string; side_effects: string[]; ready: boolean; problem: string; info: { label: string; platform: string } };

const STATE_PILL: Record<string, string> = { ready: "ok", deploying: "warn", failed: "bad", retired: "", deleted: "" };

function Deploy({ agentId, runtime, label }: { agentId: string; runtime: string; label: string }) {
  const [rels, setRels] = useState<Rel[]>([]);
  const [mig, setMig] = useState<Mig | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [image, setImage] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(() => api<{ releases: Rel[]; migration: Mig }>(`/api/runtimes/agents/${agentId}`).then((s) => { setRels(s.releases.filter((r) => r.target === runtime)); setMig(s.migration); }).catch(() => undefined), [agentId, runtime]);
  useEffect(() => { load(); setPlan(null); setNote(""); }, [load]);

  const act = async (what: string, path: string, init?: RequestInit, ok?: string) => {
    setBusy(what); setNote("");
    try { const r = await api<any>(path, init); if (ok) toast(ok); await load(); return r; }
    catch (e) { setNote((e as Error).message); toast((e as Error).message, "err"); return null; }
    finally { setBusy(""); }
  };
  const review = async () => {
    setBusy("plan"); setNote(""); setSure(false);
    try { const p = await api<Plan>(`/api/runtimes/agents/${agentId}/plan`, { method: "POST", body: JSON.stringify({ target: runtime, image: image || undefined }) }); setPlan(p); setImage(p.image); }
    catch (e) { setNote((e as Error).message); setPlan(null); }
    finally { setBusy(""); }
  };
  const deploy = async () => {
    const r = await act("deploy", `/api/runtimes/agents/${agentId}/deploy`, { method: "POST", body: JSON.stringify({ target: runtime, image, confirm: true }) }, "Deployment started");
    if (r) { setPlan(null); setSure(false); }
  };
  const live = rels.find((r) => r.live);
  return (
    <div className="card" data-testid="deploy-panel" style={{ marginTop: 10 }}>
      <div className="card-head"><b>Deploy to {label}</b><InfoTip id="runtime-shadow" /></div>
      {mig && <div className="small mut" data-testid="migration-message">{mig.message}</div>}
      {mig?.stale && (
        <div className="small" role="alert" data-testid="migration-changes" style={{ margin: "6px 0", color: "var(--warn)" }}>
          Changed since it was deployed: {mig.changes.map((c) => <div key={c.field}><b>{c.field}</b>: {c.before || "(empty)"} → {c.after || "(empty)"}</div>)}
          {mig.candidate ? <div>A shadow version (v{mig.candidate.version}) is already deployed: test it, then switch to it below.</div> : <div>Review and deploy it as a shadow version: it receives no traffic until you switch.</div>}
        </div>)}
      {rels.length > 0 && (
        <table className="tbl" style={{ marginTop: 6 }}><tbody>
          {rels.map((r) => (
            <tr key={r.id} data-testid="release">
              <td>v{r.version}</td>
              <td><span className={`pill ${STATE_PILL[r.state] ?? ""}`}>{r.state}</span>{r.live && <span className="pill ok">live</span>}{r.shadow && !r.live && <span className="pill">shadow</span>}</td>
              <td className="small mut">{r.health?.detail ?? r.detail}</td>
              <td className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                <button className="small" disabled={!!busy} onClick={() => act("check", `/api/runtimes/releases/${r.id}/refresh`, { method: "POST" })}>Check</button>
                {r.state === "ready" && <button className="small" disabled={!!busy} title="Sends one short message: it uses model tokens" onClick={() => act("test", `/api/runtimes/releases/${r.id}/health`, { method: "POST", body: JSON.stringify({ smoke: true }) }, "Test sent")}>Send test message</button>}
                {r.state === "ready" && !r.live && <button className="small" disabled={!!busy} onClick={() => { if (confirm(`Switch traffic to v${r.version}? New messages will be answered by it.`)) void act("promote", `/api/runtimes/releases/${r.id}/promote`, { method: "POST", body: JSON.stringify({ confirm: true }) }, "Switched"); }}>Switch traffic</button>}
                {(!r.live || rels.length === 1) && r.state !== "deleted" && <button className="ghost small" disabled={!!busy} onClick={() => { if (confirm(`Delete v${r.version} from the platform? This cannot be undone.`)) void act("delete", `/api/runtimes/releases/${r.id}?confirm=true`, { method: "DELETE" }, "Deleted"); }}>Delete</button>}
              </td>
            </tr>))}
        </tbody></table>)}
      {live?.prev_id && <button className="small" style={{ marginTop: 6 }} disabled={!!busy} onClick={() => { if (confirm("Roll back to the previous version? New messages will be answered by it.")) void act("rollback", `/api/runtimes/agents/${agentId}/rollback`, { method: "POST", body: JSON.stringify({ confirm: true }) }, "Rolled back"); }}>Roll back to the previous version</button>}
      {note && <p className="err small" role="alert">{note}</p>}
      <div className="row" style={{ marginTop: 8 }}><button className="small" disabled={!!busy} onClick={review}>{plan ? "Refresh plan" : rels.length ? "Review a new version" : "Review deployment"}</button></div>
      {plan && (
        <div data-testid="plan" style={{ marginTop: 8 }}>
          <div className="small mut">{plan.shadow ? "A live version exists, so this is deployed next to it as a shadow version." : "The first version goes live by itself once the platform says it is ready."}</div>
          <div className="lbl" style={{ marginTop: 8 }}>What this agent loses there</div>
          <ul className="small" style={{ margin: "2px 0", paddingLeft: 18 }}>{plan.compat.map((c) => <li key={c.text} style={c.level === "block" ? { color: "var(--bad)" } : c.level === "warn" ? { color: "var(--warn)" } : undefined}>{c.level === "block" && <b>Blocks deploy: </b>}{c.text}</li>)}</ul>
          {plan.diff.length > 0 && <div className="small">Changed from the live version: {plan.diff.map((c) => c.field).join(", ")}</div>}
          <div className="lbl" style={{ marginTop: 8 }}>1. Build and push the image ({plan.info.platform})</div>
          <div className="small mut">The files are in <code>{plan.bundle.folder}</code> ({plan.bundle.files.join(", ")}). Run these commands:</div>
          {plan.commands.length > 0 ? <pre className="small" style={{ overflowX: "auto" }}>{plan.commands.join("\n")}</pre> : <div className="small mut">Set an image address template in Settings &gt; Runtimes, or type the image address below.</div>}
          <label style={{ marginTop: 6 }}>2. Image to deploy<input value={image} placeholder="registry/name:tag" aria-label="Image to deploy" onChange={(e) => setImage(e.target.value)} /></label>
          <div className="small" style={{ marginTop: 6, color: "var(--warn)" }}>{plan.cost_note}</div>
          <ul className="small" style={{ margin: "2px 0", paddingLeft: 18 }}>{plan.side_effects.map((s) => <li key={s}>{s}</li>)}</ul>
          {!plan.ready && <div className="small err" role="alert">{plan.problem}</div>}
          <label className="check" style={{ marginTop: 6, flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I understand this creates resources on {plan.info.label} that may cost money</label>
          <button className="pri small" style={{ marginTop: 6 }} disabled={!sure || plan.blocked || !plan.ready || !image || !!busy} onClick={deploy}>Deploy</button>
        </div>)}
    </div>
  );
}

export default function AgentRuntime({ agent, isNew, onChange }: { agent: AgentLike; isNew: boolean; onChange: (patch: { runtime?: string; sandbox?: string; data?: string[] }) => void }) {
  const [targets, setTargets] = useState<Tgt[]>([]);
  const [kinds, setKinds] = useState<SbKind[]>([]);
  const [sources, setSources] = useState<Src[]>([]);
  useEffect(() => {
    api<{ targets: Tgt[] }>("/api/runtimes").then((o) => setTargets(o.targets)).catch(() => setTargets([]));
    api<{ kinds: SbKind[] }>("/api/sandboxes").then((o) => setKinds(o.kinds)).catch(() => setKinds([]));
    api<{ sources: Src[] }>("/api/data/sources").then((o) => setSources(o.sources)).catch(() => setSources([]));
  }, []);
  const rt = agent.runtime ?? "local";
  const cur = targets.find((t) => t.id === rt);
  const data = agent.data ?? [];
  const all = data.includes("*");
  const sb = kinds.find((k) => k.id === agent.sandbox);
  return (
    <div className="card" id="ag-runtime" data-testid="agent-runtime" style={{ marginTop: 14 }}>
      <div className="card-head"><b>Where it runs and what it can reach</b><InfoTip id="runtimes" /></div>
      <div className="grid">
        <label>Where it runs
          <select value={rt} onChange={(e) => onChange({ runtime: e.target.value })} aria-label="Where it runs">
            {(targets.length ? targets : [{ id: "local", label: "This PC" } as Tgt]).map((t) => <option key={t.id} value={t.id}>{t.label}{t.id !== "local" && !t.enabled ? " (switched off)" : ""}</option>)}
          </select></label>
        <label>Sandbox for its commands<InfoTip id="sandbox" />
          <select value={agent.sandbox ?? ""} onChange={(e) => onChange({ sandbox: e.target.value })} aria-label="Sandbox for its commands">
            <option value="">Default (Settings &gt; Agents &amp; sandbox)</option>
            {kinds.map((k) => <option key={k.id} value={k.id}>{k.label}{k.experimental ? " (experimental)" : ""}</option>)}
          </select></label>
      </div>
      {cur && rt !== "local" && <div className="small mut" style={{ marginTop: 6 }}>{cur.cost_note}{!cur.enabled && <> Switch it on in <a href="/settings/#runtimes">Settings &gt; Runtimes</a>.</>}</div>}
      {sb && !sb.available && <div className="small" role="alert" style={{ color: "var(--warn)", marginTop: 6 }}>{sb.label}: {sb.detail}</div>}
      {sb?.experimental && <div className="small mut" style={{ marginTop: 4 }}>{sb.notice}</div>}
      <div className="lbl" id="ag-data" style={{ marginTop: 12 }}>Data sources it may read <InfoTip id="data-sources" /></div>
      {sources.length === 0
        ? <div className="small mut">None set up yet. Add databases or Salesforce in <a href="/settings/#data">Settings &gt; Data sources</a>.</div>
        : (<div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          <label className="chip small" style={{ flexDirection: "row", gap: 4 }}><input type="checkbox" checked={all} onChange={(e) => onChange({ data: e.target.checked ? ["*"] : [] })} />All</label>
          {sources.map((s) => (
            <label key={s.id} className="chip small" style={{ flexDirection: "row", gap: 4 }}>
              <input type="checkbox" checked={all || data.includes(s.id)} disabled={all} onChange={(e) => onChange({ data: e.target.checked ? [...data, s.id] : data.filter((x) => x !== s.id) })} />{s.label} <span className="mut">({s.kind}{s.enabled ? "" : ", off"})</span>
            </label>))}
        </div>)}
      {rt !== "local" && !isNew && <Deploy agentId={agent.id} runtime={rt} label={cur?.label ?? rt} />}
      {rt !== "local" && isNew && <div className="small mut" style={{ marginTop: 8 }}>Save the agent first, then deploy it from here.</div>}
    </div>
  );
}
