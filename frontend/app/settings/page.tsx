"use client";
import { useEffect, useState } from "react";
import { api, Provider } from "@/lib/api";
import ModelSelect, { forgetModels } from "@/components/ModelSelect";
import { VoiceCard } from "@/components/Voice";
import FolderPicker from "@/components/FolderPicker";
import { toast } from "@/components/Nav";
import OllamaPull from "@/components/OllamaPull";
import CreditsPanel from "@/components/CreditsPanel";
import AutoSettings from "@/components/AutoSettings";
import DiscoverySettings from "@/components/DiscoverySettings";
import InfoTip from "@/components/InfoTip";
import { CustomEndpointForm, CustomProviderCard } from "@/components/CustomEndpoints";
import OllamaSetup from "@/components/OllamaSetup";
import OllamaLibrary from "@/components/OllamaLibrary";
import AgentInstaller from "@/components/AgentInstaller";
import MemorySettings from "@/components/MemorySettings";
import ArtifactSettings from "@/components/ArtifactSettings";
import AccessSettings from "@/components/AccessSettings";
import HarnessRules from "@/components/HarnessRules";
import AppsSettings from "@/components/AppsSettings";
import CanvasSettings from "@/components/CanvasSettings";
import UiKitSettings from "@/components/UiKitSettings";
import FrameworksPanel from "@/components/FrameworksPanel";
import OcsfSettings from "@/components/OcsfSettings";
import GuardrailSettings from "@/components/GuardrailSettings";
import IdentitySettings from "@/components/IdentitySettings";
import RuntimeSettings from "@/components/RuntimeSettings";
import SandboxSettings from "@/components/SandboxSettings";
import DataSources from "@/components/DataSources";
import { BackupPanel, NumField, ResetLink, SettingsSearch, useRegistry } from "@/components/SettingsKit";
import { confirmDiscard, useDirty } from "@/lib/dirty";

function ProviderCard({ p, onSaved }: { p: Provider; onSaved: () => void }) {
  const [f, setF] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState("");
  const val = (k: keyof Provider) => (k in f ? f[k] : (p[k] ?? "")) as any;
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));
  const mode: string = f.auth_mode ?? p.auth_mode;
  const [mv, setMv] = useState(0);            // bump to reload model lists (after pulling an Ollama model)

  async function save() {
    try {
      const patch = { ...f };
      if ("cap_usd" in patch) patch.cap_usd = Number(patch.cap_usd);
      if ("max_output_tokens" in patch) patch.max_output_tokens = Number(patch.max_output_tokens);
      if ("_headers" in patch) {
        const t = String(patch._headers).trim();
        patch.extra_headers = Object.fromEntries(t.split("\n").map((l: string) => l.trim()).filter((l: string) => l.includes(":"))
          .map((l: string) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]));
        delete patch._headers;
      }
      await api(`/api/providers/${p.id}`, { method: "PUT", body: JSON.stringify(patch) });
      forgetModels(p.id);                  // credentials may have changed -> reload the model list
      setF({}); setMsg(""); toast(`Saved ${p.label}`); onSaved();
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  const dirty = Object.keys(f).length > 0;
  useDirty(`provider:${p.id}`, dirty);

  return (
    <div className="card">
      <div className="row">
        <b>{p.label}</b>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={!!val("enabled")} onChange={(e) => set("enabled", e.target.checked)} /> enabled
        </label>
        <span className="mut small grow">{p.catalog.note}</span>
      </div>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Spend cap (USD)<input type="number" min="0" step="0.01" value={val("cap_usd")} onChange={(e) => set("cap_usd", e.target.value)} /><InfoTip id="spend-caps" /></label>
        <label>Cap window
          <select value={val("cap_period")} onChange={(e) => set("cap_period", e.target.value)}>
            <option value="total">lifetime total</option><option value="monthly">per month (UTC)</option><option value="daily">per day (UTC)</option>
          </select></label>
        <label>Max output tokens / call<input type="number" min="16" value={val("max_output_tokens")} onChange={(e) => set("max_output_tokens", e.target.value)} /></label>
        <label>Default model<ModelSelect key={"d" + mv} provider={p.id} value={val("default_model")} onChange={(v) => set("default_model", v)} emptyLabel="Choose a model" showStatus /></label>
        <label>Pro-tier model (planning, judgement)<ModelSelect key={"p" + mv} provider={p.id} value={val("pro_model")} onChange={(v) => set("pro_model", v)} emptyLabel="Same as default" /></label>
        <label>Flash-tier model (fast, cheap)<ModelSelect key={"f" + mv} provider={p.id} value={val("flash_model")} onChange={(v) => set("flash_model", v)} emptyLabel="Same as default" /></label>
        {p.kind === "ollama" && <label>Context window (tokens)
          <select value={String(val("context_window") || 16384)} onChange={(e) => set("context_window", Number(e.target.value))}>
            {[4096, 8192, 16384, 32768, 65536, 131072].map((n) => <option key={n} value={n}>{n.toLocaleString()}{n === 16384 ? " (default)" : ""}</option>)}</select><InfoTip id="context-window" />
          <span className="small mut">Bigger = longer chats but much more GPU/RAM. If Ollama reports “bad_alloc” / out of memory, lower this.</span></label>}
        {p.kind === "ollama" && <OllamaPull pid={p.id} onPulled={() => setMv((x) => x + 1)} />}
        {p.kind !== "ollama" && (p as any).credits && <CreditsPanel pid={p.id} credits={(p as any).credits} onChange={onSaved} />}
        <label>Authentication
          <select value={mode} onChange={(e) => set("auth_mode", e.target.value)}>
            {p.auth_modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select></label>
        {(mode === "api_key") && <label>API key {p.has_api_key && <span className="pill ok">stored {p.api_key_hint}</span>}
          <input type="password" autoComplete="off" placeholder={p.has_api_key ? "(unchanged)" : "paste key"} value={f.api_key ?? ""} onChange={(e) => set("api_key", e.target.value)} /></label>}
        {p.kind === "bedrock" && <>
          <label>AWS region
            <input list="aws-regions" value={val("aws_region")} placeholder="us-east-1" onChange={(e) => set("aws_region", e.target.value)} />
            <datalist id="aws-regions">{["us-east-1", "us-east-2", "us-west-2", "eu-central-1", "eu-west-1", "eu-west-3", "eu-north-1", "ap-northeast-1", "ap-southeast-1", "ap-southeast-2", "ap-south-1", "ca-central-1", "sa-east-1"].map((r) => <option key={r} value={r} />)}</datalist></label>
          {mode === "aws_keys" && <>
            <label>Access key ID {p.aws_access_key_hint && <span className="pill ok">stored {p.aws_access_key_hint}</span>}
              <input autoComplete="off" value={f.aws_access_key_id ?? ""} placeholder={p.aws_access_key_hint ? "(unchanged)" : "AKIA…"} onChange={(e) => set("aws_access_key_id", e.target.value)} /></label>
            <label>Secret access key {p.has_aws_secret && <span className="pill ok">stored</span>}
              <input type="password" autoComplete="off" value={f.aws_secret_access_key ?? ""} placeholder={p.has_aws_secret ? "(unchanged)" : "paste secret"} onChange={(e) => set("aws_secret_access_key", e.target.value)} /></label>
            <label>Session token (temporary credentials only) {p.has_aws_session_token && <span className="pill ok">stored</span>}
              <input type="password" autoComplete="off" value={f.aws_session_token ?? ""} placeholder={p.has_aws_session_token ? "(unchanged)" : "optional"} onChange={(e) => set("aws_session_token", e.target.value)} /></label>
          </>}
          {mode === "aws_profile" && <label>Profile name (from ~/.aws/config)
            <input value={val("aws_profile")} placeholder="default" onChange={(e) => set("aws_profile", e.target.value)} /></label>}
          {mode === "aws_default" && <div className="small mut" style={{ gridColumn: "1 / -1" }}>Uses whatever AWS credentials the Agent Studio server can find: AWS_ACCESS_KEY_ID / AWS_PROFILE env vars, ~/.aws (incl. <code>aws sso login</code>), or an instance role.</div>}
          {["api_key", "env"].includes(mode) && <div className="small mut" style={{ gridColumn: "1 / -1" }}>A Bedrock API key works for running models; listing models then falls back to LiteLLM&apos;s catalog (listing needs AWS credentials).</div>}
        </>}
        {(mode === "env") && <label>Environment variable holding the key
          <input value={val("api_key_env")} placeholder={p.key_env_default} onChange={(e) => set("api_key_env", e.target.value)} /></label>}
        {p.kind === "vertex_ai" && !["api_key", "env"].includes(mode) && <>
          <label>GCP project ID<input value={val("vertex_project")} placeholder={mode === "adc" ? "my-project" : "blank = project_id from the JSON"} onChange={(e) => set("vertex_project", e.target.value)} /></label>
          <label>Location<input value={val("vertex_location")} placeholder="global" onChange={(e) => set("vertex_location", e.target.value)} /></label>
        </>}
        {p.kind === "vertex_ai" && mode === "service_account_file" && <label>Service-account JSON path
          <input value={val("vertex_credentials_path")} placeholder="C:/keys/sa.json" onChange={(e) => set("vertex_credentials_path", e.target.value)} /></label>}
        {p.kind === "vertex_ai" && mode === "service_account_json" && <label>Service-account JSON {p.has_service_account_json && <span className="pill ok">stored {p.service_account_email ?? ""}</span>}
          <textarea rows={3} autoComplete="off" placeholder={p.has_service_account_json ? "(unchanged) — paste to replace" : '{"type": "service_account", ...}'} value={f.vertex_credentials_json ?? ""}
            onChange={(e) => set("vertex_credentials_json", e.target.value)} /></label>}
        {p.kind !== "mock" && <label>API base / endpoint (optional){p.kind === "vertex_ai" && ["api_key", "env"].includes(mode) ? " — default: Vertex express endpoint" : ""}
          <input value={val("api_base")} placeholder={p.kind === "ollama" ? "http://127.0.0.1:11434" : "default"} onChange={(e) => set("api_base", e.target.value)} /></label>}
        {p.kind === "openai" && <label>Organization (optional)<input value={val("organization")} onChange={(e) => set("organization", e.target.value)} /></label>}
        {p.kind !== "mock" && <label>Extra headers (Name: value per line — write-only{p.extra_header_names.length ? `; stored: ${p.extra_header_names.join(", ")}` : ""})
          <textarea rows={2} autoComplete="off" placeholder="X-Proxy-Token: …" value={f._headers ?? ""} onChange={(e) => set("_headers", e.target.value)} /></label>}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!dirty} onClick={save}>Save</button><span className="small mut">{msg}</span>
        {p.kind !== "mock" && <span className={`pill ${p.auth_ready ? "ok" : "bad"}`} title={p.auth_detail}>{p.auth_ready ? "auth ready" : "auth incomplete"}</span>}
        <span className="small mut">{p.auth_detail}</span>
        {p.extra_header_names.length > 0 && <button onClick={async () => { try { await api(`/api/providers/${p.id}`, { method: "PUT", body: JSON.stringify({ extra_headers: {} }) }); onSaved(); } catch (e) { setMsg("⚠ " + (e as Error).message); } }}>Clear headers</button>}
      </div>
    </div>
  );
}

type AcpCfg = { id: string; label: string; command: string; args: string[]; enabled: boolean; builtin: boolean; max_turns: number; notes: string;
  installed: boolean; resolved_path: string | null; env_keys: string[]; env_set: Record<string, boolean>;
  month_cost_cap: number; month_turn_cap: number; month_minutes_cap: number;
  month: { turns: number; minutes: number; reported_cost: number | null; cost_reported_turns: number } };

function AcpRow({ a, onSaved }: { a: AcpCfg; onSaved: () => void }) {
  const [cmd, setCmd] = useState(a.command);
  const [args, setArgs] = useState(a.args.join("\n"));
  const [turns, setTurns] = useState(String(a.max_turns));
  const [caps, setCaps] = useState({ month_cost_cap: String(a.month_cost_cap ?? 0), month_turn_cap: String(a.month_turn_cap ?? 0), month_minutes_cap: String(a.month_minutes_cap ?? 0) });
  const [env, setEnv] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const capsDirty = caps.month_cost_cap !== String(a.month_cost_cap ?? 0) || caps.month_turn_cap !== String(a.month_turn_cap ?? 0) || caps.month_minutes_cap !== String(a.month_minutes_cap ?? 0);
  const dirty = cmd !== a.command || args !== a.args.join("\n") || turns !== String(a.max_turns) || Object.values(env).some(Boolean) || capsDirty;
  useDirty(`acp:${a.id}`, dirty);
  const save = async (patch: any) => {
    try { await api(`/api/acp/agents/${a.id}`, { method: "PUT", body: JSON.stringify(patch) }); setEnv({}); setMsg(""); toast(`Saved ${a.label}`); onSaved(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const doProbe = async () => { setBusy(true); setProbe(null); try { setProbe(await api(`/api/acp/agents/${a.id}/probe`, { method: "POST" })); } catch (e) { setProbe({ ok: false, error: (e as Error).message }); } setBusy(false); };
  return (
    <div className="card">
      <div className="row">
        <b>{a.label}</b>
        <span className={`pill ${a.installed ? "ok" : "bad"}`}>{a.installed ? "installed" : "not found on PATH"}</span>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={a.enabled} onChange={(e) => save({ enabled: e.target.checked })} /> enabled</label>
        <span className="mut small grow">{a.notes}</span>
        <button onClick={doProbe} disabled={busy || !a.installed}>{busy ? "Starting…" : "Test connection"}</button>
        {!a.builtin && <button onClick={async () => { try { await api(`/api/acp/agents/${a.id}`, { method: "DELETE" }); onSaved(); } catch (e) { setMsg("⚠ " + (e as Error).message); } }}>Delete</button>}
      </div>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Command<input value={cmd} onChange={(e) => setCmd(e.target.value)} /></label>
        <label>Arguments (one per line)<textarea rows={2} value={args} onChange={(e) => setArgs(e.target.value)} /></label>
        <label>Max turns per session<input type="number" min={1} value={turns} onChange={(e) => setTurns(e.target.value)} /></label>
        <label>Monthly turn limit (0 = none)<input type="number" min={0} value={caps.month_turn_cap} onChange={(e) => setCaps({ ...caps, month_turn_cap: e.target.value })} /></label>
        <label>Monthly time limit, minutes (0 = none)<input type="number" min={0} value={caps.month_minutes_cap} onChange={(e) => setCaps({ ...caps, month_minutes_cap: e.target.value })} /></label>
        <label>Monthly cost limit, USD of reported cost (0 = none)<input type="number" min={0} step="0.01" value={caps.month_cost_cap} onChange={(e) => setCaps({ ...caps, month_cost_cap: e.target.value })} /></label>
        {a.month && <div className="small mut" style={{ gridColumn: "1 / -1" }}>This month: {a.month.turns} turns · {a.month.minutes} min ·{" "}
          {a.month.cost_reported_turns ? `$${Number(a.month.reported_cost ?? 0).toFixed(4)} reported by the agent` : "no cost reported (use turn/time limits for this agent)"}. Limits are checked before each turn starts.</div>}
        {a.env_keys.map((k) => (
          <label key={k}>{k} {a.env_set[k] && <span className="pill ok">stored</span>}
            <input type="password" placeholder={a.env_set[k] ? "(unchanged)" : "optional"} value={env[k] ?? ""} onChange={(e) => setEnv({ ...env, [k]: e.target.value })} /></label>))}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!dirty} onClick={() => save({ command: cmd, args: args.split("\n").map((x) => x.trim()).filter(Boolean), max_turns: Number(turns),
          month_cost_cap: Number(caps.month_cost_cap), month_turn_cap: Number(caps.month_turn_cap), month_minutes_cap: Number(caps.month_minutes_cap),
          env: Object.fromEntries(Object.entries(env).filter(([, v]) => v)) })}>Save</button>
        <span className="small mut">{msg}</span>
        {a.resolved_path && <span className="small mut">→ {a.resolved_path}</span>}
      </div>
      {probe && <div className="small" style={{ marginTop: 8 }}>
        {probe.ok ? <span className="pill ok">connected</span> : <span className="pill bad">failed</span>}{" "}
        {probe.ok ? <>{probe.title ?? probe.name} {probe.version} · ACP v{probe.protocol} · modes: {(probe.modes?.available ?? []).map((m: any) => m.id).join(", ") || "none"}
          {probe.auth_methods?.length ? ` · auth: ${probe.auth_methods.map((m: any) => m.id).join(", ")}` : ""}</> : <span className="err">{probe.error}</span>}</div>}
    </div>
  );
}

function AddAcp({ onSaved }: { onSaved: () => void }) {
  const [f, setF] = useState({ id: "", label: "", command: "", args: "" });
  const [msg, setMsg] = useState("");
  return (
    <div className="card">
      <b>Add a custom ACP agent</b> <span className="mut small">any CLI that speaks ACP over stdio (it will run as your user)</span>
      <div className="grid" style={{ marginTop: 8 }}>
        <label>Id<input value={f.id} placeholder="my-agent" onChange={(e) => setF({ ...f, id: e.target.value })} /></label>
        <label>Label<input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></label>
        <label>Command<input value={f.command} placeholder="my-agent-cli" onChange={(e) => setF({ ...f, command: e.target.value })} /></label>
        <label>Arguments (one per line)<textarea rows={2} value={f.args} onChange={(e) => setF({ ...f, args: e.target.value })} /></label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="pri" disabled={!f.id || !f.command} onClick={async () => {
          try { await api("/api/acp/agents", { method: "POST", body: JSON.stringify({ ...f, args: f.args.split("\n").map((x) => x.trim()).filter(Boolean) }) }); setF({ id: "", label: "", command: "", args: "" }); setMsg(""); onSaved(); }
          catch (e) { setMsg("⚠ " + (e as Error).message); } }}>Add</button><span className="small err">{msg}</span>
      </div>
    </div>
  );
}


function OtelCard() {
  const [c, setC] = useState<any>(null);
  const [sum, setSum] = useState<any>(null);
  const [fwd, setFwd] = useState("");
  const [hdrs, setHdrs] = useState("");
  const [recv, setRecv] = useState("");
  const [ret, setRet] = useState("30");
  const [msg, setMsg] = useState("");
  const load = () => Promise.all([api("/api/otel/settings"), api("/api/otel/summary")]).then(([a, b]) => {
    setC(a); setSum(b); setFwd(a.forward_url); setRecv(a.receiver_url); setRet(String(a.retention_days));
  }).catch((e) => setMsg("⚠ " + e.message));
  useEffect(() => { load(); }, []);
  const put = async (patch: any) => { try { setC(await api("/api/otel/settings", { method: "PUT", body: JSON.stringify(patch) })); setMsg("Saved"); setSum(await api("/api/otel/summary")); } catch (e) { setMsg("⚠ " + (e as Error).message); } };
  const parseHdrs = () => Object.fromEntries(hdrs.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf(":"); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
  if (!c) return msg ? <p className="err">{msg}</p> : null;
  return (
    <div className="card">
      <div className="row">
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={c.enabled} onChange={(e) => put({ enabled: e.target.checked })} /> store OpenTelemetry data</label>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={c.inject_env} onChange={(e) => put({ inject_env: e.target.checked })} /> point external agents at this connector</label>
        <a href="/telemetry/" style={{ textDecoration: "underline" }}>Open telemetry viewer →</a>
      </div>
      <p className="mut small">Records spans for every Agent Studio agent (teams, agent-created sub-agents, model calls, tool calls) and one span per turn / tool call of each external agent, and receives whatever those agents export themselves.
        Receiver (this machine only, OTLP/HTTP <b>JSON</b>): <code>POST {c.receiver_url}/v1/traces | /v1/metrics | /v1/logs</code>. External agents are launched with <code>OTEL_EXPORTER_OTLP_PROTOCOL=http/json</code> and an agent-id resource attribute; for Claude Code, metrics + events are switched on (<b>prompt and tool-content logging are never enabled</b>). Agents that don&apos;t export OpenTelemetry (most don&apos;t) still get the Agent Studio spans.</p>
      <div className="grid">
        <label>Retention (days)<input type="number" min={1} value={ret} onChange={(e) => setRet(e.target.value)} onBlur={() => put({ retention_days: Number(ret) })} /></label>
        <label>This server&apos;s URL as seen by agents<input value={recv} onChange={(e) => setRecv(e.target.value)} onBlur={() => recv !== c.receiver_url && put({ receiver_url: recv })} /></label>
        <label>Forward everything to your collector (OTLP/HTTP base URL, optional)<input value={fwd} placeholder="http://localhost:4318" onChange={(e) => setFwd(e.target.value)} /></label>
        <label>Forward headers (Name: value per line — write-only{c.forward_header_names.length ? `; stored: ${c.forward_header_names.join(", ")}` : ""})
          <textarea rows={2} value={hdrs} placeholder="Authorization: Bearer …" onChange={(e) => setHdrs(e.target.value)} /></label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="pri" onClick={() => put({ forward_url: fwd, ...(hdrs.trim() ? { forward_headers: parseHdrs() } : {}) }).then(() => setHdrs(""))}>Save forwarding</button>
        {c.forward_url && <button onClick={() => put({ forward_url: "", forward_headers: {} })}>Stop forwarding</button>}
        <span className="small mut">{msg}</span>
        {c.forward_url && <span className="small mut">forwarded {c.forwarded} · errors {c.forward_errors}{c.last_forward_error ? ` · last: ${c.last_forward_error}` : ""}</span>}
      </div>
      {sum && <table style={{ marginTop: 10 }}><thead><tr><th>Source (agent)</th><th>Signal</th><th>Records</th></tr></thead><tbody>
        {sum.by_source.map((r: any) => <tr key={r.signal + r.source}><td>{r.source}</td><td>{r.signal}</td><td>{r.n}</td></tr>)}
        {sum.by_source.length === 0 && <tr><td colSpan={3} className="mut">Nothing stored yet.</td></tr>}</tbody></table>}
      <div className="row" style={{ marginTop: 8 }}>
        <span className="small mut">{sum?.total ?? 0} records stored (local SQLite)</span>
        <button onClick={async () => { if (confirm("Delete ALL stored telemetry?")) { try { await api("/api/otel/records", { method: "DELETE" }); load(); } catch (e) { setMsg("⚠ " + (e as Error).message); } } }}>Clear all</button>
      </div>
    </div>
  );
}

type Tab = "providers" | "local" | "auto" | "harness" | "external" | "voice" | "telemetry" | "assistant" | "discovery" | "memory" | "security" | "artifacts" | "access" | "runtimes" | "data" | "backup";
const TABS: [Tab, string][] = [["providers", "Model providers"], ["local", "Local models"], ["auto", "Auto"], ["harness", "Agents & sandbox"], ["external", "External agents"], ["voice", "Voice"], ["telemetry", "Telemetry"], ["assistant", "Assistant"], ["discovery", "Discovery & AI-BOM"], ["memory", "Memory"], ["security", "Security"], ["artifacts", "Artifacts"], ["access", "API & A2A"], ["runtimes", "Runtimes"], ["data", "Data sources"], ["backup", "Backup & history"]];

/** The side agent (AG-UI helper): on/off, and which provider and model it runs on. */
function AssistantCard({ s, ps, onSave }: { s: any; ps: Provider[]; onSave: (patch: Record<string, unknown>) => Promise<void> }) {
  const [prov, setProv] = useState<string>(s.sidebot_provider ?? "");
  const [model, setModel] = useState<string>(s.sidebot_model ?? "");
  const [msg, setMsg] = useState("");
  useEffect(() => { setProv(s.sidebot_provider ?? ""); setModel(s.sidebot_model ?? ""); }, [s.sidebot_provider, s.sidebot_model]);
  const resolved = s.sidebot_resolved ?? { provider: null, model: null };
  const effective = prov || resolved.provider || "";
  const dirty = prov !== (s.sidebot_provider ?? "") || model !== (s.sidebot_model ?? "");
  const save = async () => {
    try { await onSave({ sidebot_provider: prov, sidebot_model: model }); setMsg("Saved"); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const toggle = async (v: boolean) => {
    try { await onSave({ sidebot_enabled: v }); setMsg(v ? "Assistant is on" : "Assistant is off"); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const prompt = s.sidebot_prompt ?? { core: "", default_guidance: "", effective: "", max_guidance: 4000 };
  const [guide, setGuide] = useState<string>(s.sidebot_guidance || prompt.default_guidance);
  useEffect(() => { setGuide(s.sidebot_guidance || prompt.default_guidance); }, [s.sidebot_guidance, prompt.default_guidance]);
  const guideDirty = guide.trim() !== (s.sidebot_guidance || prompt.default_guidance).trim();
  useDirty("assistant-guidance", guideDirty);
  const saveGuide = async (text: string) => {
    try { await onSave({ sidebot_guidance: text === prompt.default_guidance ? "" : text }); setMsg(text.trim() ? "Guidance saved" : "Back to the default guidance"); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  const tools: { name: string; description: string; enabled: boolean }[] = s.sidebot_tools ?? [];
  const setTool = async (name: string, on: boolean) => {
    const off = tools.filter((t) => (t.name === name ? !on : !t.enabled)).map((t) => t.name);
    try { await onSave({ sidebot_tools_off: off }); setMsg(`${name} ${on ? "allowed" : "switched off"}`); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  return (
    <div className="card">
      <div className="card-head"><b>Assistant</b><InfoTip id="assistant" /><span className={`pill ${s.sidebot_enabled ? "ok" : ""}`}>{s.sidebot_enabled ? "on" : "off"}</span></div>
      <p className="card-desc">The side agent. It answers questions about the app, opens pages for you, starts guided tours, and highlights things on screen. It runs on the model you choose here, under the same spend caps as chat.</p>
      <Toggle checked={!!s.sidebot_enabled} onChange={toggle} label="Assistant on" hint="Off hides the assistant and refuses its requests." />
      <div className="grid" style={{ marginTop: 12 }}>
        <label>Provider
          <select value={prov} onChange={(e) => { setProv(e.target.value); setModel(""); }}>
            <option value="">Automatic{resolved.provider ? ` (${resolved.provider})` : ""}</option>
            {ps.filter((p) => p.enabled && p.id !== "mock").map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select></label>
        <label>Model
          <ModelSelect provider={effective} value={model} onChange={setModel} emptyLabel="The provider's default model" /></label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="acc" disabled={!dirty} onClick={save}>Save</button>
        <span className="small mut">{msg}</span>
      </div>
      <div className="small mut" style={{ marginTop: 8 }}>
        Right now it runs on: {resolved.provider ? `${resolved.provider} · ${resolved.model ?? "default model"}` : "no provider available — add one under Model providers"}.
      </div>
      <h4 style={{ margin: "18px 0 4px" }}>What it may do</h4>
      <div className="small mut">Switch off anything you do not want the assistant to do. A switched-off action is not offered to the model and is refused if it tries.</div>
      {tools.map((t) => <Toggle key={t.name} checked={t.enabled} onChange={(v) => setTool(t.name, v)} label={t.name} hint={t.description} />)}
      <h4 style={{ margin: "18px 0 4px" }}>Its instructions <InfoTip id="assistant-rules" /></h4>
      <div className="small mut">The locked safety rules always come first. Your guidance comes after them and can change its style or focus, never override them.</div>
      <label style={{ marginTop: 8 }}>Safety rules (locked)<textarea readOnly rows={7} value={prompt.core} aria-label="Safety rules (locked)" style={{ opacity: 0.8 }} /></label>
      <label style={{ marginTop: 8 }}>Your guidance
        <textarea rows={5} value={guide} maxLength={prompt.max_guidance} aria-label="Your guidance" onChange={(e) => setGuide(e.target.value)} /></label>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="acc" disabled={!guideDirty} onClick={() => saveGuide(guide)}>Save guidance</button>
        <button disabled={!s.sidebot_guidance} onClick={() => saveGuide("")}>Reset to default</button>
        <span className="small mut">{guide.length} / {prompt.max_guidance}</span>
      </div>
      <details style={{ marginTop: 10 }}><summary className="small">See exactly what the model receives</summary>
        <pre className="small" style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{prompt.effective}</pre></details>
    </div>
  );
}

function WorkspaceField({ current, onSave }: { current: string; onSave: (p: string) => Promise<void> }) {
  const [v, setV] = useState(current);
  const [msg, setMsg] = useState("");
  useEffect(() => setV(current), [current]);
  const save = async (p: string) => { try { await onSave(p); setMsg("Saved"); } catch (e) { setMsg("⚠ " + (e as Error).message); } };
  return (
    <div style={{ gridColumn: "1 / -1" }}>
      <label>Default workspace folder <InfoTip id="workspace" /></label>
      <div className="row" style={{ marginTop: 5, alignItems: "flex-start" }}>
        <div className="grow"><FolderPicker value={v} onChange={(p) => { setV(p); setMsg(""); }} placeholder="e.g. H:/htdocs/my-project" /></div>
        <button className="acc" disabled={!v || v === current} onClick={() => save(v)}>Save</button>
      </div>
      <div className="small mut" style={{ marginTop: 6 }}>The folder your agents work in: file tools can only read/edit inside it, sandboxed shell commands see only it, the Filesystem/Git MCP servers and external agents start in it. Point it at the project you want them to work on (you can also pick a different folder per chat under <b>Options</b>). {msg}</div>
    </div>
  );
}

function Toggle({ checked, onChange, label, hint, sk }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; sk?: string }) {
  return (
    <div className="row" data-setting={sk} style={{ alignItems: "flex-start", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
      <div className="grow"><div style={{ fontWeight: 500 }}>{label}</div>{hint && <div className="small mut" style={{ marginTop: 2 }}>{hint}</div>}</div>
      <label className="switch"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><i /></label>
    </div>
  );
}

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("providers");
  const [ps, setPs] = useState<Provider[]>([]);
  const [hs, setHs] = useState<any>(null);
  const [sb, setSb] = useState<any>(null);
  const [acp, setAcp] = useState<AcpCfg[]>([]);
  const [selP, setSelP] = useState<string>("");
  const [selA, setSelA] = useState<string>("");
  const [adding, setAdding] = useState(false);               // the "add an OpenAI-compatible endpoint" form is open
  const [err, setErr] = useState("");
  const [regKey, setRegKey] = useState(0);
  const { reg, byField } = useRegistry(regKey);
  const load = () => Promise.all([api<Provider[]>("/api/providers"), api("/api/settings"), api("/api/sandbox/status"), api<AcpCfg[]>("/api/acp/agents")])
    .then(([p, h, b, x]) => { setPs(p); setHs(h); setSb(b); setAcp(x); setErr("");
      setSelP((c) => c || (p.find((q) => q.enabled && q.id !== "mock") ?? p[0])?.id || ""); setSelA((c) => c || x[0]?.id || ""); })
    .catch((e) => setErr("Backend offline: " + e.message));
  useEffect(() => {
    load();
    const h = window.location.hash.slice(1) as Tab;
    if (TABS.some(([k]) => k === h)) setTab(h);
  }, []);
  const go = (t: Tab) => { if (t !== tab && !confirmDiscard()) return; setTab(t); history.replaceState(null, "", `#${t}`); };
  const reloadAll = () => { load(); setRegKey((k) => k + 1); };
  const jump = (t: string, field: string) => {         // from the search box: open the tab, then flash the setting
    if (t !== tab && !confirmDiscard()) return;
    setTab(t as Tab); history.replaceState(null, "", `#${t}`);
    setTimeout(() => { const el = document.querySelector(`[data-setting="${field}"]`); if (el) { el.scrollIntoView({ block: "center" }); el.classList.remove("flash"); void (el as HTMLElement).offsetWidth; el.classList.add("flash"); } }, 60);
  };
  // saveSettings throws (the workspace field and the Assistant tab show their own inline error); putSettings tells the user via a toast
  const saveSettings = async (patch: any) => { setHs(await api("/api/settings", { method: "PUT", body: JSON.stringify(patch) })); };
  const putSettings = async (patch: any) => { try { await saveSettings(patch); } catch (e) { toast((e as Error).message, "err"); } };
  const prov = ps.find((p) => p.id === selP);
  const ext = acp.find((a) => a.id === selA);
  return (
    <div>
      <div className="page-head"><h2>Settings</h2><span className="grow" /><SettingsSearch reg={reg} tabs={TABS} go={jump} /></div>
      {err && <p className="err">{err}</p>}
      <div className="tabs" data-tour="settings-tabs">{TABS.map(([k, label]) => (
        <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => go(k)}>{label}
          {k === "providers" && <span className="count">{ps.filter((p) => p.enabled).length}/{ps.length}</span>}
          {k === "external" && <span className="count">{acp.filter((a) => a.installed).length}/{acp.length}</span>}</button>))}</div>

      {tab === "providers" && (<>
        <p className="page-sub">Each provider has its own hard spend cap: a call is refused <b>before</b> it is sent if its worst-case cost (estimated input + the full max output) would exceed what is left. Pro / Flash are the models agents resolve their tier to.</p>
        <div className="two">
          <div className="list">{ps.map((p) => (
            <div key={p.id} className={`li ${selP === p.id && !adding ? "on" : ""}`} onClick={() => { setAdding(false); setSelP(p.id); }}>
              <span className={`dot ${p.enabled && (p.auth_ready || p.kind === "mock") ? "ok" : p.enabled ? "bad" : ""}`} />
              <span className="t">{p.label}</span><span className="s">{p.custom ? "custom · " : ""}{p.enabled ? `$${Number(p.cap_usd).toFixed(2)}` : "off"}</span></div>))}
            <button style={{ marginTop: 8, width: "100%" }} onClick={() => { if (confirmDiscard()) setAdding(true); }}>+ Add OpenAI-compatible endpoint</button></div>
          <div style={{ minWidth: 0 }}>{adding ? <CustomEndpointForm onCancel={() => setAdding(false)} onCreated={(id) => { setAdding(false); setSelP(id); load(); }} />
            : prov && (prov.custom ? <CustomProviderCard key={prov.id} p={prov} onSaved={load} onDeleted={() => { setSelP(""); load(); }} /> : <ProviderCard key={prov.id} p={prov} onSaved={load} />)}</div>
        </div>
      </>)}

      {tab === "local" && (<>
        <p className="page-sub">Run AI models on this PC for free. Install Ollama with one click, then pick models that suit your computer.</p>
        <div className="card"><OllamaSetup /></div>
        <div className="card"><div className="card-head"><b>Model library</b></div><OllamaLibrary /></div>
      </>)}
      {tab === "auto" && <AutoSettings providers={ps} />}

      {tab === "harness" && hs && sb && (<>
        <p className="page-sub">How Agent Studio&apos;s own agents work: where they edit, how long they may loop, and whether they may build their own sub-agents.</p>
        <div className="card">
          <div className="card-head"><b>Sandbox</b><InfoTip id="sandbox" /><span className={`pill ${sb.docker && sb.image ? "ok" : "bad"}`}>{sb.detail}</span></div>
          <div className="card-desc">Shell commands never run on your machine directly. If Docker or the sandbox image is missing they are refused (fail-closed).</div>
          <Toggle checked={hs.sandbox_network} onChange={(v) => putSettings({ sandbox_network: v })} sk="sandbox_network" label="Allow network inside the sandbox" hint="Off by default. Turn on only if your tests need to download packages." />
          <div className="grid" style={{ marginTop: 12 }}>
            <WorkspaceField current={hs.workspace_dir} onSave={(p) => saveSettings({ workspace_dir: p })} />
          </div>
        </div>
        <SandboxSettings />
        <div className="card">
          <div className="card-head"><b>Agents building agents</b></div>
          <div className="row" style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
            <div className="grow"><div style={{ fontWeight: 500 }}>Model thinking</div>
              <div className="small mut">Reasoning models (Claude, Gemini, GPT-5…) think before answering; the reasoning is shown collapsed above each answer. Thinking tokens are billed as output and count against the caps.</div></div>
            <select value={hs.thinking_level ?? "medium"} onChange={(e) => putSettings({ thinking_level: e.target.value })}>
              <option value="off">Off</option><option value="low">Low (fast models may skip thinking)</option><option value="medium">Medium (default)</option><option value="high">High (slower, costlier)</option></select>
          </div>
          <Toggle checked={!!hs.auto_debug_mcp} onChange={(v) => putSettings({ auto_debug_mcp: v })} sk="auto_debug_mcp" label="Debug failing tools automatically"
            hint="When an MCP server fails to start, a Super Engineer run investigates in the background and reports in Runs. Uses your model credits." />
          <div style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
            <div style={{ fontWeight: 500 }}>Fallback models</div>
            <div className="small mut" style={{ marginBottom: 8 }}>If a model fails (rate limit, outage, out of memory…), the same request continues on the next one here. Each is still checked against its cap.</div>
            {(hs.model_fallbacks ?? []).map((f: any, i: number) => (
              <div key={i} className="row" style={{ marginBottom: 6, alignItems: "flex-start" }}>
                <select value={f.provider} onChange={(e) => putSettings({ model_fallbacks: (hs.model_fallbacks ?? []).map((x: any, j: number) => j === i ? { provider: e.target.value, model: null } : x) })} style={{ width: 200 }}>
                  {ps.filter((p) => p.enabled || p.id === f.provider).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
                <div className="grow"><ModelSelect provider={f.provider} value={f.model ?? ""} emptyLabel="Default model"
                  onChange={(v) => putSettings({ model_fallbacks: (hs.model_fallbacks ?? []).map((x: any, j: number) => j === i ? { ...x, model: v || null } : x) })} /></div>
                <button className="ghost small" onClick={() => putSettings({ model_fallbacks: (hs.model_fallbacks ?? []).filter((_: any, j: number) => j !== i) })}>Remove</button>
              </div>))}
            {(hs.model_fallbacks ?? []).length < 5 && ps.some((p) => p.enabled) &&
              <button className="ghost small" onClick={() => { const p = ps.find((q) => q.enabled); if (p) putSettings({ model_fallbacks: [...(hs.model_fallbacks ?? []), { provider: p.id, model: null }] }); }}>+ Add a fallback model</button>}
          </div>
          <Toggle checked={hs.agent_spawning_enabled} onChange={(v) => putSettings({ agent_spawning_enabled: v })} sk="agent_spawning_enabled" label="Let agents create their own sub-agents"
            hint="Master switch. Each agent also needs “Create its own sub-agents” ticked in Agent Studio. Created agents only get tools / MCP servers their creator holds, can't create more, and every model call counts against the same caps." />
          <div className="grid" style={{ marginTop: 12 }}>
            <NumField sk="max_spawns_per_turn" label="Max agent-created sub-agents per turn" min={1} max={20} value={hs.max_spawns_per_turn} onSave={(n) => putSettings({ max_spawns_per_turn: n })} />
            <ResetLink sk="max_spawns_per_turn" reg={byField.max_spawns_per_turn} onReset={reloadAll} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><b>Long runs</b></div>
          <div className="card-desc">Every turn runs on the server as a background job: closing the tab or losing the connection doesn&apos;t stop it — reopen the chat to re-attach. It stops when it finishes, when you press Stop, or at these limits. (Restarting the backend stops running turns; their progress is saved.)</div>
          <div className="grid" style={{ marginTop: 12 }}>
            <NumField sk="max_turn_minutes" label="Max minutes per turn (0 = no limit)" min={0} max={100000} integer={false} value={hs.max_turn_minutes} onSave={(n) => putSettings({ max_turn_minutes: n })} />
            <ResetLink sk="max_turn_minutes" reg={byField.max_turn_minutes} onReset={reloadAll} />
            <NumField sk="max_llm_calls_per_turn" label="Max model calls per turn (loop guard)" min={1} max={100000} value={hs.max_llm_calls_per_turn} onSave={(n) => putSettings({ max_llm_calls_per_turn: n })} />
            <ResetLink sk="max_llm_calls_per_turn" reg={byField.max_llm_calls_per_turn} onReset={reloadAll} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><b>Agents calling external agents</b><InfoTip id="acp" /></div>
          <Toggle checked={hs.agents_can_call_external} onChange={(v) => putSettings({ agents_can_call_external: v })} sk="agents_can_call_external" label="Let agents call external agents (Claude Code, Cursor, Kiro…)"
            hint="Master switch. Each agent also lists which external agents it may call (Agent Studio page). External agents bill their own accounts." />
          <div className="grid" style={{ marginTop: 12 }}>
            <NumField sk="max_external_calls_per_turn" label="Max external-agent calls per turn" min={1} max={1000} value={hs.max_external_calls_per_turn} onSave={(n) => putSettings({ max_external_calls_per_turn: n })} />
            <ResetLink sk="max_external_calls_per_turn" reg={byField.max_external_calls_per_turn} onReset={reloadAll} />
            <NumField sk="external_timeout_min" label="Max minutes per external-agent call" min={1} max={100000} integer={false} value={hs.external_timeout_min} onSave={(n) => putSettings({ external_timeout_min: n })} />
            <ResetLink sk="external_timeout_min" reg={byField.external_timeout_min} onReset={reloadAll} />
          </div>
        </div>
        <HarnessRules />
        <CanvasSettings />
        <AppsSettings />
        <UiKitSettings />
        <FrameworksPanel />
        <div className="row small mut">Tools come from <a href="/mcp/">MCP servers</a>; reusable know-how from <a href="/skills/">Skills</a>.</div>
      </>)}

      {tab === "external" && (<>
        <details className="inst-box" data-tour="agent-installer"><summary><b>Install coding agents</b> <span className="small mut">Claude Code, Codex, Gemini CLI and more, in one click</span></summary>
          <div style={{ marginTop: 10 }}><AgentInstaller onChange={() => load()} /></div></details>
        <div className="section-note"><span>These CLIs (Claude Code, Cursor, Kiro, Antigravity, Grok…) use <b>their own logins and billing</b> — Agent Studio&apos;s spend caps do <b>not</b> apply; usage they report shows in Billing. Each runs as your user; its own shell is not sandboxed. Permission requests are shown to you unless you choose an auto policy.</span></div>
        <div className="two">
          <div className="list">
            {acp.map((a) => (
              <div key={a.id} className={`li ${selA === a.id ? "on" : ""}`} onClick={() => setSelA(a.id)}>
                <span className={`dot ${a.installed && a.enabled ? "ok" : a.installed ? "" : "bad"}`} />
                <span className="t">{a.label}</span><span className="s">{!a.installed ? "not installed" : a.enabled ? "" : "off"}</span></div>))}
            <div className={`li ${selA === "__add" ? "on" : ""}`} onClick={() => setSelA("__add")}><span className="t" style={{ color: "var(--acc)" }}>+ Add custom agent</span></div>
          </div>
          <div>{selA === "__add" ? <AddAcp onSaved={load} /> : ext && <AcpRow key={ext.id + ext.command + ext.args.join()} a={ext} onSaved={load} />}</div>
        </div>
      </>)}

      {tab === "assistant" && hs && <AssistantCard s={hs} ps={ps} onSave={async (patch) => { await saveSettings(patch); }} />}
      {tab === "voice" && <VoiceCard />}
      {tab === "discovery" && <DiscoverySettings />}
      {tab === "memory" && <MemorySettings />}
      {tab === "artifacts" && <ArtifactSettings />}
      {tab === "access" && <AccessSettings />}
      {tab === "runtimes" && <RuntimeSettings />}
      {tab === "data" && <DataSources />}
      {tab === "security" && (<>
        <p className="page-sub">Safety checks and identity for your agents. See how you line up with the OWASP lists on the <a href="/security/">Security</a> page.</p>
        <GuardrailSettings />
        <IdentitySettings />
      </>)}
      {tab === "backup" && <BackupPanel onChanged={reloadAll} />}

      {tab === "telemetry" && (<>
        <p className="page-sub">An OpenTelemetry connector that stores traces, metrics and logs from every agent — yours and the external ones. Browse them in <a href="/telemetry/">Telemetry</a>.</p>
        <OtelCard />
        <OcsfSettings />
      </>)}
    </div>
  );
}
