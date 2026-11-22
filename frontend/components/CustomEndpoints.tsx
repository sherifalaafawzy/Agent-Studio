"use client";
// Custom OpenAI-compatible endpoints: add one (find its models, set prices and a spend cap), then test, edit and delete it.
import { useState } from "react";
import { api, Provider } from "@/lib/api";
import ModelSelect, { forgetModels } from "@/components/ModelSelect";
import InfoTip from "@/components/InfoTip";
import { toast } from "@/components/Nav";
import { useDirty } from "@/lib/dirty";

type Prices = Record<string, { input_per_m?: number; output_per_m?: number; context?: number }>;
type Probe = { ok: boolean; models?: string[]; detail?: string; title?: string; kind?: string };
type TestRes = { ok: boolean; title: string; detail: string; latency_ms?: number; reply?: string; scope: string; scope_label: string; model: string };

export const SCOPE: Record<string, { text: string; cls: string; tip: string }> = {
  this_pc: { text: "on this PC", cls: "ok", tip: "The address points at this computer, so prompts stay here." },
  network: { text: "on this network", cls: "", tip: "The address looks like a server on your own network. Prompts leave this PC but not your network." },
  internet: { text: "data leaves this PC", cls: "warn", tip: "The address looks like an internet service. Prompts, tool results and retrieved passages are sent to it." },
};

export function ScopeBadge({ scope }: { scope?: string | null }) {
  const s = SCOPE[scope ?? "internet"] ?? SCOPE.internet;
  return <span className={`pill ${s.cls}`} title={s.tip} data-testid="scope-badge">{s.text}</span>;
}

/** "Name: value" lines -> an object (lines without a colon are ignored). */
export function parseHeaders(text: string): Record<string, string> {
  return Object.fromEntries(text.split("\n").map((l) => l.trim()).filter((l) => l.includes(":"))
    .map((l) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]));
}

const num = (v: string): number | undefined => (v.trim() === "" || Number.isNaN(Number(v)) ? undefined : Number(v));

/** The "*" row plus a row per model: input and output USD per million tokens, and the context size. */
function PriceTable({ prices, models, onChange }: { prices: Prices; models: string[]; onChange: (p: Prices) => void }) {
  const [extra, setExtra] = useState("");
  const rows = ["*", ...Array.from(new Set([...models, ...Object.keys(prices).filter((k) => k !== "*")]))];
  const set = (m: string, k: "input_per_m" | "output_per_m" | "context", v: string) => {
    const e = { ...(prices[m] ?? {}), [k]: num(v) };
    const clean = Object.fromEntries(Object.entries(e).filter(([, x]) => x !== undefined));
    const next = { ...prices };
    if (Object.keys(clean).length) next[m] = clean; else delete next[m];
    onChange(next);
  };
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="small" aria-label="Model prices">
        <thead><tr><th>Model</th><th>Input $ / M tokens</th><th>Output $ / M tokens</th><th>Context (tokens)</th></tr></thead>
        <tbody>{rows.map((m) => (
          <tr key={m}>
            <td>{m === "*" ? <span title="Used for every model that has no row of its own">All other models</span> : m}</td>
            {(["input_per_m", "output_per_m", "context"] as const).map((k) => (
              <td key={k}><input type="number" min="0" step={k === "context" ? "1" : "0.01"} style={{ width: 110 }} aria-label={`${m === "*" ? "default" : m} ${k.replace("_per_m", "")}`}
                value={prices[m]?.[k] ?? ""} placeholder={k === "context" ? "unknown" : "unknown"} onChange={(e) => set(m, k, e.target.value)} /></td>))}
          </tr>))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 6 }}>
        <input placeholder="Price another model: its name" aria-label="Another model name" value={extra} onChange={(e) => setExtra(e.target.value)} style={{ maxWidth: 260 }} />
        <button disabled={!extra.trim()} onClick={() => { onChange({ ...prices, [extra.trim()]: prices[extra.trim()] ?? { input_per_m: 0, output_per_m: 0 } }); setExtra(""); }}>Add row</button>
      </div>
      <div className="small mut" style={{ marginTop: 4 }}>Blank = unknown: a model with no price is treated as expensive so the spend cap stays safe. Type <b>0</b> and <b>0</b> for a model you run yourself.</div>
    </div>
  );
}

export function CustomEndpointForm({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState({ label: "", api_base: "", auth_mode: "api_key", api_key: "", api_key_env: "", headers: "", organization: "", default_model: "",
    in_price: "", out_price: "", context: "", cap_usd: "1", cap_period: "total" });
  const [probe, setProbe] = useState<Probe | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));
  useDirty("custom-endpoint-new", !!(f.label || f.api_base || f.api_key));
  const auth = () => ({ api_base: f.api_base, auth_mode: f.auth_mode, api_key: f.api_key || null, api_key_env: f.api_key_env || null, headers: parseHeaders(f.headers), organization: f.organization || null });

  async function find() {
    setBusy(true); setProbe(null); setMsg("");
    try { setProbe(await api<Probe>("/api/providers/custom/discover", { method: "POST", body: JSON.stringify(auth()) })); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
    setBusy(false);
  }
  async function create() {
    const { in_price, out_price, context } = f;
    if ((in_price.trim() === "") !== (out_price.trim() === "")) { setMsg("⚠ Give both the input and the output price, or leave both blank."); return; }
    const star: Record<string, number> = {};
    if (num(in_price) !== undefined && num(out_price) !== undefined) { star.input_per_m = num(in_price)!; star.output_per_m = num(out_price)!; }
    if (num(context) !== undefined) star.context = num(context)!;
    const typed = f.default_model.trim() && !probe?.models?.includes(f.default_model.trim()) ? [f.default_model.trim()] : [];
    try {
      const p = await api<Provider>("/api/providers/custom", { method: "POST", body: JSON.stringify({
        ...auth(), label: f.label, default_model: f.default_model, cap_usd: Number(f.cap_usd) || 0, cap_period: f.cap_period,
        model_prices: Object.keys(star).length ? { "*": star } : {}, manual_models: typed }) });
      toast(`Added ${p.label}`); onCreated(p.id);
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  return (
    <div className="card" data-testid="custom-endpoint-form">
      <div className="card-head"><b>Add an OpenAI-compatible endpoint</b><InfoTip id="custom-endpoint" /></div>
      <p className="small mut">Any server that speaks the OpenAI chat API: vLLM, LM Studio, a LiteLLM proxy, OpenRouter, Together, Groq, Fireworks or your company gateway. Agents can then use it like any other provider.</p>
      <div className="grid">
        <label>Name<input value={f.label} placeholder="Office GPU" onChange={(e) => set("label", e.target.value)} /></label>
        <label>Address<input value={f.api_base} placeholder="http://192.168.1.20:8000/v1" onChange={(e) => set("api_base", e.target.value)} />
          <span className="small mut">Usually ends in <code>/v1</code>. If you give only a host, <code>/v1</code> is added.</span></label>
        <label>Sign-in
          <select value={f.auth_mode} onChange={(e) => set("auth_mode", e.target.value)}>
            <option value="api_key">API key (stored on this PC)</option><option value="env">API key from an environment variable</option><option value="none">No key (local server or a proxy that adds it)</option>
          </select></label>
        {f.auth_mode === "api_key" && <label>API key<input type="password" autoComplete="off" value={f.api_key} placeholder="paste key" onChange={(e) => set("api_key", e.target.value)} /></label>}
        {f.auth_mode === "env" && <label>Environment variable holding the key<input value={f.api_key_env} placeholder="MY_ENDPOINT_KEY" onChange={(e) => set("api_key_env", e.target.value)} /></label>}
        <label>Extra headers (Name: value per line, optional)<textarea rows={2} autoComplete="off" value={f.headers} placeholder="X-Team: ml" onChange={(e) => set("headers", e.target.value)} /></label>
        <label>Organization (optional)<input value={f.organization} onChange={(e) => set("organization", e.target.value)} /></label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={find} disabled={busy || !f.api_base.trim()}>{busy ? "Looking…" : "Find models"}</button>
        {probe && (probe.ok ? <span className="pill ok">{probe.detail}</span> : <span className="small err" role="alert">{probe.title}: {probe.detail}</span>)}
      </div>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Default model
          <input list="ce-models" value={f.default_model} placeholder={probe?.models?.length ? "pick or type" : "type the model name"} onChange={(e) => set("default_model", e.target.value)} />
          <datalist id="ce-models">{(probe?.models ?? []).map((m) => <option key={m} value={m} />)}</datalist></label>
        <label>Input price, USD per million tokens<input type="number" min="0" step="0.01" value={f.in_price} placeholder="unknown" onChange={(e) => set("in_price", e.target.value)} /><InfoTip id="endpoint-prices" /></label>
        <label>Output price, USD per million tokens<input type="number" min="0" step="0.01" value={f.out_price} placeholder="unknown" onChange={(e) => set("out_price", e.target.value)} /></label>
        <label>Context size (tokens, optional)<input type="number" min="512" value={f.context} placeholder="unknown" onChange={(e) => set("context", e.target.value)} /></label>
        <label>Spend cap (USD)<input type="number" min="0" step="0.01" value={f.cap_usd} onChange={(e) => set("cap_usd", e.target.value)} /><InfoTip id="spend-caps" /></label>
        <label>Cap window
          <select value={f.cap_period} onChange={(e) => set("cap_period", e.target.value)}>
            <option value="total">lifetime total</option><option value="monthly">per month (UTC)</option><option value="daily">per day (UTC)</option></select></label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!f.label.trim() || !f.api_base.trim() || !f.default_model.trim()} onClick={create}>Add endpoint</button>
        <button onClick={onCancel}>Cancel</button>
        <span className="small err" role="alert">{msg}</span>
      </div>
    </div>
  );
}

export function CustomProviderCard({ p, onSaved, onDeleted }: { p: Provider; onSaved: () => void; onDeleted: () => void }) {
  const [f, setF] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState("");
  const [test, setTest] = useState<TestRes | null>(null);
  const [busy, setBusy] = useState(false);
  const val = (k: string) => (k in f ? f[k] : (p as any)[k] ?? "");
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));
  const mode: string = f.auth_mode ?? p.auth_mode;
  const prices: Prices = f.model_prices ?? p.model_prices ?? {};
  const manual: string[] = f.manual_models ?? p.manual_models ?? [];
  const dirty = Object.keys(f).length > 0;
  useDirty(`provider:${p.id}`, dirty);

  async function save() {
    try {
      const patch = { ...f };
      if ("cap_usd" in patch) patch.cap_usd = Number(patch.cap_usd);
      if ("max_output_tokens" in patch) patch.max_output_tokens = Number(patch.max_output_tokens);
      if ("_headers" in patch) { patch.extra_headers = parseHeaders(String(patch._headers)); delete patch._headers; }
      await api(`/api/providers/${p.id}`, { method: "PUT", body: JSON.stringify(patch) });
      forgetModels(p.id); setF({}); setMsg(""); toast(`Saved ${p.label}`); onSaved();
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function runTest() {
    setBusy(true); setTest(null);
    try { setTest(await api<TestRes>(`/api/providers/${p.id}/test`, { method: "POST", body: JSON.stringify({ model: val("default_model") || undefined }) })); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
    setBusy(false);
  }
  async function remove() {
    if (!confirm(`Delete the endpoint "${p.label}"? Its key and prices are removed. Spend history stays in the ledger.`)) return;
    try { await api(`/api/providers/${p.id}`, { method: "DELETE" }); forgetModels(p.id); toast(`Deleted ${p.label}`); onDeleted(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  return (
    <div className="card" data-testid="custom-provider-card">
      <div className="row">
        <b>{p.label}</b><span className="pill">custom</span><ScopeBadge scope={p.network_scope} /><InfoTip id="custom-endpoint" />
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={!!val("enabled")} onChange={(e) => set("enabled", e.target.checked)} /> enabled</label>
        <span className="grow" />
        <button onClick={runTest} disabled={busy || dirty} title={dirty ? "Save your changes first" : "Sends one tiny request (a fraction of a cent, counted against this endpoint's cap)"}>{busy ? "Testing…" : "Test"}</button>
        <button onClick={remove}>Delete</button>
      </div>
      {test && (
        <div className="small" style={{ marginTop: 8 }} role="status" data-testid="test-result">
          <span className={`pill ${test.ok ? "ok" : "bad"}`}>{test.ok ? "works" : "failed"}</span> <b>{test.title}</b> — {test.detail}
          {test.ok && test.reply && <> Reply: “{test.reply}”.</>} <span className="mut">({test.scope_label})</span>
        </div>)}
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Name<input value={val("label")} onChange={(e) => set("label", e.target.value)} /></label>
        <label>Address<input value={val("api_base")} onChange={(e) => set("api_base", e.target.value)} /></label>
        <label>Sign-in
          <select value={mode} onChange={(e) => set("auth_mode", e.target.value)}>{p.auth_modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
        {mode === "api_key" && <label>API key {p.has_api_key && <span className="pill ok">stored {p.api_key_hint}</span>}
          <input type="password" autoComplete="off" placeholder={p.has_api_key ? "(unchanged)" : "paste key"} value={f.api_key ?? ""} onChange={(e) => set("api_key", e.target.value)} /></label>}
        {mode === "env" && <label>Environment variable holding the key<input value={val("api_key_env")} onChange={(e) => set("api_key_env", e.target.value)} /></label>}
        <label>Extra headers (Name: value per line — write-only{p.extra_header_names.length ? `; stored: ${p.extra_header_names.join(", ")}` : ""})
          <textarea rows={2} autoComplete="off" placeholder="X-Team: ml" value={f._headers ?? ""} onChange={(e) => set("_headers", e.target.value)} /></label>
        <label>Organization (optional)<input value={val("organization")} onChange={(e) => set("organization", e.target.value)} /></label>
        <label>Default model<ModelSelect provider={p.id} value={val("default_model")} onChange={(v) => set("default_model", v)} emptyLabel="Choose a model" showStatus /></label>
        <label>Pro-tier model<ModelSelect provider={p.id} value={val("pro_model")} onChange={(v) => set("pro_model", v)} emptyLabel="Same as default" /></label>
        <label>Flash-tier model<ModelSelect provider={p.id} value={val("flash_model")} onChange={(v) => set("flash_model", v)} emptyLabel="Same as default" /></label>
        <label>Spend cap (USD)<input type="number" min="0" step="0.01" value={val("cap_usd")} onChange={(e) => set("cap_usd", e.target.value)} /><InfoTip id="spend-caps" /></label>
        <label>Cap window
          <select value={val("cap_period")} onChange={(e) => set("cap_period", e.target.value)}>
            <option value="total">lifetime total</option><option value="monthly">per month (UTC)</option><option value="daily">per day (UTC)</option></select></label>
        <label>Max output tokens / call<input type="number" min="16" value={val("max_output_tokens")} onChange={(e) => set("max_output_tokens", e.target.value)} /></label>
        <label style={{ gridColumn: "1 / -1" }}>Models typed in by hand (one per line; use for servers that do not list their models)
          <textarea rows={2} value={manual.join("\n")} onChange={(e) => set("manual_models", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} /></label>
      </div>
      <h4 style={{ marginTop: 12 }}>Prices and context <InfoTip id="endpoint-prices" /></h4>
      <PriceTable prices={prices} models={[...manual, ...(val("default_model") ? [String(val("default_model"))] : [])]} onChange={(m) => set("model_prices", m)} />
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!dirty} onClick={save}>Save</button><span className="small err" role="alert">{msg}</span>
        <span className={`pill ${p.auth_ready ? "ok" : "bad"}`} title={p.auth_detail}>{p.auth_ready ? "ready" : "incomplete"}</span><span className="small mut">{p.auth_detail}</span>
      </div>
    </div>
  );
}
