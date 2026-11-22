"use client";
// Settings > Auto: turn Auto mode on, choose what reads each message (built-in rules or a local d1 decision model), how it
// trades cost against quality, which providers it may use, and try a prompt to see the routing decision without calling a model.
import { useCallback, useEffect, useState } from "react";
import { api, AutoInfo, AutoRoute, Provider } from "@/lib/api";
import ModelSelect from "@/components/ModelSelect";
import RouteNote from "@/components/AutoRoute";
import { toast } from "@/components/Nav";

type D1Step = { id: string; label: string; size_mb: number; status: string };
type D1Model = {
  model: string; label: string; note: string; state: "not_installed" | "installing" | "ready" | "failed"; ready: boolean; error: string | null;
  percent: number; progress_text: string; steps: D1Step[]; weights_gb: number; download_mb: number; disk_gb_needed: number; free_gb: number | null;
  shared_env: boolean; question_types: string[]; expect: string; busy_with: string | null; revision: string;
  license: { name: string; url: string; summary: string[] }; review_notes: string[];
};
type D1Models = { default: string; installing: string | null; models: Record<string, D1Model> };

const ENGINE_LABEL: Record<string, string> = { rules: "Built-in rules (no download)", "d1-2b": "d1-2B decision model (local)", "d1-3b": "d1-3B decision model (local)" };
const PREF_HELP: Record<string, string> = {
  cost: "Cheapest adequate model. The strong tier only for messages that are clearly hard (long, technical and needing reasoning).",
  balanced: "The strong tier for reasoning, agent-style coding and long, demanding messages; the fast tier for everything else.",
  quality: "The strong tier for anything that is not trivial, and the priciest known strong model (price is only a stand-in for capability).",
};
const POLL_MS = 2000;
const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);

function D1Row({ m, onChanged }: { m: D1Model; onChanged: () => void }) {
  const [accept, setAccept] = useState(false);
  const [device, setDevice] = useState("auto");
  const [msg, setMsg] = useState("");
  const install = async () => {
    setMsg("");
    try { await api("/api/d1/install", { method: "POST", body: JSON.stringify({ accept_license: accept, device, model: m.model }) }); toast(`Installing ${m.label}…`); onChanged(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const remove = async () => {
    try { await api(`/api/d1/uninstall?model=${m.model}`, { method: "POST" }); toast(`${m.label} removed`); onChanged(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const tone = m.state === "ready" ? "ok" : m.state === "failed" ? "bad" : "";
  return (
    <div className="auto-d1" data-testid={`d1-${m.model}`}>
      <div className="row">
        <b>{m.label}</b><span className={`pill ${tone}`}>{m.state === "not_installed" ? "not installed" : m.state}</span>
        <span className="small mut">{m.weights_gb} GB weights{m.shared_env && m.state !== "ready" ? " · reuses the other model's environment" : ""}</span>
        <span className="grow" />
        {m.state === "ready" && <button className="ghost small" onClick={remove}>Remove</button>}
      </div>
      <div className="small mut" style={{ margin: "4px 0 6px" }}>{m.note}</div>
      {m.state === "installing" && <div><div className="bar"><i style={{ width: `${m.percent}%` }} /></div><div className="small mut">{m.percent}% · {m.progress_text}</div></div>}
      {m.state === "failed" && <div className="small err">{m.error}</div>}
      {(m.state === "not_installed" || m.state === "failed") && (
        <div>
          <details className="small">
            <summary>Licence, review notes and what it needs</summary>
            <div className="mut">{m.expect}</div>
            <div style={{ marginTop: 6 }}><b>{m.license.name}</b> — <a href={m.license.url} target="_blank" rel="noreferrer">read the full licence</a></div>
            <ul>{m.license.summary.map((t, i) => <li key={i}>{t}</li>)}</ul>
            <div><b>Security review of the code it runs</b> (pinned commit {m.revision.slice(0, 8)}):</div>
            <ul>{m.review_notes.map((t, i) => <li key={i}>{t}</li>)}</ul>
          </details>
          <div className="row" style={{ marginTop: 6 }}>
            <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /> I accept the {m.license.name}</label>
            <select value={device} onChange={(e) => setDevice(e.target.value)} aria-label={`Device for ${m.label}`}>
              <option value="auto">GPU if available</option><option value="cpu">CPU only (smaller download)</option></select>
            <button className="acc small" disabled={!accept || !!m.busy_with} onClick={install} title={m.busy_with ? "Another model is installing" : ""}>
              Install · about {Math.round(m.download_mb / 100) / 10} GB</button>
          </div>
          <div className="small mut">Needs about {m.disk_gb_needed} GB of disk{m.free_gb != null ? ` (${m.free_gb} GB free)` : ""}. Nothing downloads until you press Install.</div>
        </div>)}
      {msg && <div className="small err">{msg}</div>}
    </div>
  );
}

function TierPick({ label, value, providers, onChange }: { label: string; value: string; providers: Provider[]; onChange: (v: string) => void }) {
  const [pid, setPid] = useState(value.split("/")[0]);
  const model = value.includes("/") ? value.slice(value.indexOf("/") + 1) : "";
  return (
    <div>
      <label>{label}
        <select value={pid} onChange={(e) => { setPid(e.target.value); onChange(""); }} aria-label={`${label} provider`}>
          <option value="">Automatic (each provider&apos;s own {label.toLowerCase().split(" ")[0]} model)</option>
          {providers.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
      {pid && <ModelSelect provider={pid} value={model} onChange={(m) => onChange(m ? `${pid}/${m}` : "")} emptyLabel="Choose a model" />}
    </div>
  );
}

export default function AutoSettings({ providers }: { providers: Provider[] }) {
  const [info, setInfo] = useState<AutoInfo | null>(null);
  const [d1, setD1] = useState<D1Models | null>(null);
  const [f, setF] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState("");
  const [probe, setProbe] = useState("");
  const [asAgent, setAsAgent] = useState(false);
  const [result, setResult] = useState<AutoRoute | null>(null);
  const [probeErr, setProbeErr] = useState("");
  const [probing, setProbing] = useState(false);

  const load = useCallback(() => Promise.all([api<AutoInfo>("/api/auto"), api<D1Models>("/api/d1/models")])
    .then(([a, m]) => { setInfo(a); setD1(m); }).catch((e) => setMsg("⚠ " + (e as Error).message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {                       // while a model installs, keep its progress fresh
    if (!d1?.installing) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [d1?.installing, load]);

  if (!info || !d1) return <div className="card"><div className="small mut">{msg || "Loading Auto settings…"}</div></div>;
  const val = (k: keyof AutoInfo | "allowText" | "denyText") => (k in f ? f[k] : k === "allowText" ? info.allow.join("\n") : k === "denyText" ? info.deny.join("\n") : (info as any)[k]);
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));
  const dirty = Object.keys(f).length > 0;
  const put = async (patch: Record<string, unknown>) => {
    setMsg("");
    try { setInfo(await api<AutoInfo>("/api/auto", { method: "PUT", body: JSON.stringify(patch) })); return true; }
    catch (e) { setMsg("⚠ " + (e as Error).message); return false; }
  };
  const save = async () => {
    const { allowText, denyText, ...rest } = f;
    const patch: Record<string, unknown> = { ...rest };
    if (allowText !== undefined) patch.allow = lines(allowText);
    if (denyText !== undefined) patch.deny = lines(denyText);
    if (typeof patch.timeout_s === "string") patch.timeout_s = Number(patch.timeout_s);
    if (await put(patch)) { setF({}); toast("Auto settings saved"); }
  };
  const tryIt = async () => {
    setProbing(true); setProbeErr(""); setResult(null);
    try {
      setResult(await api<AutoRoute>("/api/auto/route", { method: "POST", body: JSON.stringify({ message: probe, agent: asAgent,
        ...(val("engine") !== info.engine ? { engine: val("engine") } : {}), ...(val("preference") !== info.preference ? { preference: val("preference") } : {}) }) }));
    } catch (e) { setProbeErr((e as Error).message); }
    finally { setProbing(false); }
  };
  const warm = async () => {
    try { await api("/api/auto/warm", { method: "POST" }); toast("Loading the decision model…"); await load(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const engine: string = val("engine");
  const w = info.worker;

  return (
    <>
      <p className="page-sub">Pick <b>Auto</b> in the chat and Agent Studio chooses the provider and model for each message, so you never have to. It only uses providers you have enabled, never goes past a spend cap, and always shows what it chose and why.</p>

      <div className="card" data-tour="auto-card">
        <div className="card-head"><b>Auto mode</b>
          <span className={`pill ${info.available ? "ok" : "bad"}`}>{info.available ? "ready" : "unavailable"}</span></div>
        <label className="row" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={info.enabled} onChange={(e) => put({ enabled: e.target.checked })} /> Offer Auto in the chat</label>
        {!info.available && <div className="small err" style={{ marginTop: 6 }}>{info.reason}</div>}
        {info.available && <div className="small mut" style={{ marginTop: 6 }}>{info.reason || "Auto will use any enabled provider that has credentials, is allowed below and still has budget."}</div>}
        <label className="row" style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10 }}>
          <input type="checkbox" checked={info.fallback} onChange={(e) => put({ fallback: e.target.checked })} />
          If the chosen model fails, retry once on the next best</label>
      </div>

      <div className="card">
        <div className="card-head"><b>What reads each message</b></div>
        <div className="card-desc">The router looks at your message (and the last few) and decides how demanding it is. The built-in rules need nothing. A local d1 decision model is more accurate, free and takes milliseconds once loaded; until it is installed and loaded, or if it errors or runs out of time, the rules decide and the answer says so.</div>
        <div className="grid" style={{ marginTop: 10 }}>
          <label>Engine
            <select value={engine} onChange={(e) => set("engine", e.target.value)}>
              {info.engines.map((e) => <option key={e} value={e}>{ENGINE_LABEL[e] ?? e}{e !== "rules" && !info.engine_ready[e] ? " — not installed" : ""}</option>)}</select></label>
          <label>Time allowed for it (seconds)
            <input type="number" min={0.5} max={30} step={0.5} value={val("timeout_s")} onChange={(e) => set("timeout_s", e.target.value)} /></label>
        </div>
        {engine !== "rules" && w && <div className="small mut" style={{ marginTop: 8 }} data-testid="worker">
          {w.state === "ready" ? `${w.model} is loaded${w.device ? ` on ${w.device}` : ""}${w.load_seconds != null ? ` (took ${w.load_seconds} s)` : ""}.`
            : w.state === "loading" ? `${w.model} is loading in the background; until it is ready the rules decide.`
              : w.state === "failed" ? `${w.model} could not load: ${w.error}` : `${w.model} is not loaded yet.`}</div>}
        {engine !== "rules" && info.engine_ready[engine] && w?.state !== "ready" && w?.state !== "loading" &&
          <button className="ghost small" style={{ marginTop: 8 }} onClick={warm}>Load it now</button>}
        <div className="auto-d1s">{Object.values(d1.models).map((m) => <D1Row key={m.model} m={m} onChanged={load} />)}</div>
        <div className="small mut">d1-2B is Liquid AI&apos;s newer model and the default. d1-3B is the older, larger one: it only answers yes/no, choice and score questions, which is all Auto needs. Both run in their own isolated environment, locally.</div>
      </div>

      <div className="card">
        <div className="card-head"><b>How it chooses</b></div>
        <div className="grid">
          <label>Preference
            <select value={val("preference")} onChange={(e) => set("preference", e.target.value)}>
              {info.preferences.map((p) => <option key={p} value={p}>{p}</option>)}</select></label>
        </div>
        <div className="small mut" style={{ marginTop: 6 }}>{PREF_HELP[val("preference")]}</div>
        <div className="grid" style={{ marginTop: 12 }}>
          <label>Only use these (one per line: <code>provider</code> or <code>provider/model</code>; empty = all)
            <textarea rows={3} value={val("allowText")} onChange={(e) => set("allowText", e.target.value)} /></label>
          <label>Never use these
            <textarea rows={3} value={val("denyText")} onChange={(e) => set("denyText", e.target.value)} /></label>
        </div>
        <div className="grid" style={{ marginTop: 12 }}>
          <TierPick label="Fast-tier model" value={val("tier_models").flash} providers={providers} onChange={(v) => set("tier_models", { ...val("tier_models"), flash: v })} />
          <TierPick label="Strong-tier model" value={val("tier_models").pro} providers={providers} onChange={(v) => set("tier_models", { ...val("tier_models"), pro: v })} />
        </div>
        <div className="small mut" style={{ marginTop: 6 }}>Leave the two tier pickers on Automatic to use each provider&apos;s own Pro / Flash models from Model providers. Setting one pins that tier to a single model.</div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="acc" disabled={!dirty} onClick={save}>Save</button>
          <span className="small mut">{msg}</span>
        </div>
      </div>

      <div className="card" data-testid="try-it">
        <div className="card-head"><b>Try it</b></div>
        <div className="card-desc">Paste a message to see what Auto would pick (using the engine and preference chosen above, saved or not). Nothing is sent to any model provider and nothing is spent.</div>
        <textarea rows={3} value={probe} placeholder="e.g. Refactor this function and explain the trade-offs" onChange={(e) => setProbe(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={asAgent} onChange={(e) => setAsAgent(e.target.checked)} /> as if an agent were selected</label>
          <span className="grow" />
          <button className="acc" disabled={!probe.trim() || probing} onClick={tryIt}>Show the routing</button>
        </div>
        {probeErr && <div className="small err" style={{ marginTop: 8 }}>{probeErr}</div>}
        {result && <div style={{ marginTop: 10 }}><RouteNote route={result} open showSkipped /></div>}
      </div>
    </>
  );
}
