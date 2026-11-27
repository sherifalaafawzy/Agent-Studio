"use client";
// "OpenAPI steps": the APIs you imported and their endpoints. Upload a file, give an address or paste the description; then create, edit, test and delete
// APIs and endpoints. Every endpoint becomes a box you can drop into a workflow. Credentials are write-only.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, API_BASE } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete, Modal } from "@/components/Modal";

export type Param = { name: string; in: string; required?: boolean; type?: string; default?: unknown; description?: string };
export type Field = { name: string; required?: boolean; type?: string; description?: string };
export type Op = { id: string; method: string; path: string; summary: string; description?: string; parameters: Param[]; body_fields?: Field[]; tags: string[]; origin: string;
  enabled: boolean; hidden: boolean; risk: string; confirm_required: boolean; defaults: Record<string, unknown>; deprecated?: boolean; used_by?: string[] };
export type Src = { id: string; name: string; base: string; allow_write: boolean; has_token: boolean; origin: string; auth: { type: string; name: string }; servers: { url: string; description: string }[];
  refreshed: number | null; updated: number | null; title: string; version: string; warnings: string[]; counts: { total: number; enabled: number; write: number; hidden: number }; used_by: string[]; operations: Op[] };
type Preview = { title: string; version: string; spec_version: string; base: string; servers: { url: string }[]; warnings: string[]; suggested_id: string; suggested_auth: { type: string; header?: string };
  counts: { total: number; read: number; write: number; tags: Record<string, number> }; operations: { id: string; method: string; path: string; summary: string }[] };

const AUTHS: [string, string][] = [["none", "No sign-in"], ["bearer", "Bearer token"], ["header", "A header (name + value)"], ["query", "A query parameter"], ["basic", "Username:password"]];
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "").slice(0, 30);
const riskPill = (r: string) => (r === "read" ? "ok" : r === "delete" ? "bad" : "warn");

// ------------------------------------------------------------------ add an API
function AddApi({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [mode, setMode] = useState<"upload" | "url" | "paste">("upload");
  const [content, setContent] = useState(""), [filename, setFilename] = useState(""), [url, setUrl] = useState("");
  const [pv, setPv] = useState<Preview | null>(null);
  const [f, setF] = useState({ id: "", name: "", base: "", token: "", allow_write: false, auth: "none", auth_name: "" });
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const body = () => ({ content: mode === "url" ? "" : content, filename, src: mode === "url" ? url : "" });
  const read = async () => {
    setBusy(true); setErr("");
    try {
      const p = await api<Preview>("/api/workflows/apis/preview", { method: "POST", body: JSON.stringify(body()) });
      setPv(p);
      setF({ id: slug(p.suggested_id || p.title), name: p.title, base: p.base, token: "", allow_write: false, auth: p.suggested_auth.type, auth_name: p.suggested_auth.header === "Authorization" ? "" : p.suggested_auth.header ?? "" });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const pick = async (file?: File) => {
    if (!file) return;
    if (file.size > 5_000_000) { setErr("That file is larger than 5 MB."); return; }
    setContent(await file.text()); setFilename(file.name); setErr("");
  };
  const add = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api<Src>("/api/workflows/apis", { method: "POST", body: JSON.stringify({ ...body(), id: f.id, name: f.name, base: f.base, token: f.token, allow_write: f.allow_write, auth: { type: f.auth, name: f.auth_name } }) });
      toast("API imported"); onDone(r.id);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const ready = mode === "url" ? !!url.trim() : !!content.trim();
  return (
    <Modal title="Add an API" onClose={onClose} wide>
      {!pv && (<>
        <div className="seg" role="tablist" style={{ margin: "10px 0" }}>{([["upload", "Upload file"], ["url", "From address"], ["paste", "Paste"]] as const).map(([k, t]) => <button key={k} role="tab" aria-selected={mode === k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{t}</button>)}</div>
        {mode === "upload" && <label>OpenAPI or Swagger file (.json, .yaml, .yml; up to 5 MB)<input type="file" accept=".json,.yaml,.yml,application/json" aria-label="OpenAPI file" onChange={(e) => pick(e.target.files?.[0])} /></label>}
        {mode === "upload" && filename && <div className="small mut">{filename} ready ({Math.round(content.length / 1024)} KB)</div>}
        {mode === "url" && <label>Address of the description or a workspace file<input value={url} placeholder="https://api.example.com/openapi.json" aria-label="Address" onChange={(e) => setUrl(e.target.value)} /></label>}
        {mode === "paste" && <label>The description (JSON or YAML)<textarea rows={10} value={content} aria-label="Pasted description" onChange={(e) => setContent(e.target.value)} /></label>}
        {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 8 }}>{err}</div>}
        <div className="row" style={{ marginTop: 12 }}><button className="pri" disabled={!ready || busy} onClick={read}>{busy ? "Reading…" : "Read it"}</button><button onClick={onClose}>Cancel</button></div>
      </>)}
      {pv && (<>
        <div className="card" data-testid="preview" style={{ margin: "10px 0" }}>
          <b>{pv.title}</b> <span className="mut small">v{pv.version || "?"} · OpenAPI {pv.spec_version}</span>
          <div className="small">{pv.counts.total} operations: {pv.counts.read} read, <b>{pv.counts.write} change data</b> (they stay off until you allow writes).</div>
          <div className="small mut">{Object.entries(pv.counts.tags).slice(0, 8).map(([t, n]) => `${t} (${n})`).join(" · ")}</div>
          {pv.warnings.map((w, i) => <div key={i} className="small" style={{ color: "var(--warn-text)" }}>⚠ {w}</div>)}
        </div>
        <div className="grid">
          <label>Id<input value={f.id} aria-label="API id" onChange={(e) => setF({ ...f, id: e.target.value.toLowerCase() })} /></label>
          <label>Name<input value={f.name} aria-label="API name" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label>Address calls go to<input list="servers" value={f.base} aria-label="Base address" onChange={(e) => setF({ ...f, base: e.target.value })} /><datalist id="servers">{pv.servers.map((s) => <option key={s.url} value={s.url} />)}</datalist></label>
          <label>Sign-in<select value={f.auth} aria-label="Sign-in type" onChange={(e) => setF({ ...f, auth: e.target.value })}>{AUTHS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
          {(f.auth === "header" || f.auth === "query") && <label>Header or parameter name<input value={f.auth_name} aria-label="Key name" placeholder="X-Api-Key" onChange={(e) => setF({ ...f, auth_name: e.target.value })} /></label>}
          {f.auth !== "none" && <label>Key or token (write-only)<input type="password" autoComplete="off" value={f.token} aria-label="API key" onChange={(e) => setF({ ...f, token: e.target.value })} /></label>}
        </div>
        <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={f.allow_write} onChange={(e) => setF({ ...f, allow_write: e.target.checked })} /> Allow endpoints that change data (each box still needs &quot;I confirm&quot;)</label>
        {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 8 }}>{err}</div>}
        <div className="row" style={{ marginTop: 12 }}><button className="pri" disabled={!f.id || busy} onClick={add}>{busy ? "Importing…" : "Import"}</button><button onClick={() => setPv(null)}>Back</button></div>
      </>)}
    </Modal>
  );
}

// ------------------------------------------------------------------ one endpoint: create / edit
function EndpointForm({ sid, op, onClose, onSaved }: { sid: string; op: Op | null; onClose: () => void; onSaved: () => void }) {
  const custom = !op || op.origin === "custom";
  const [f, setF] = useState({ id: op?.id ?? "", label: op?.summary ?? "", description: op?.description ?? "", method: op?.method ?? "get", path: op?.path ?? "/", enabled: op?.enabled ?? true,
    confirm_required: op?.confirm_required ?? false, defaults: JSON.stringify(op?.defaults ?? {}) });
  const [params, setParams] = useState<Param[]>(op?.parameters.filter((p) => p.in === "query") ?? []);
  const [fields, setFields] = useState<Field[]>(op?.body_fields ?? []);
  const [err, setErr] = useState("");
  const save = async () => {
    let defaults: Record<string, unknown> = {};
    try { defaults = JSON.parse(f.defaults || "{}"); } catch { setErr("Default arguments must be valid JSON."); return; }
    try {
      if (!op) await api(`/api/workflows/apis/${sid}/operations`, { method: "POST", body: JSON.stringify({ id: f.id, label: f.label, description: f.description, method: f.method, path: f.path, parameters: params, body_fields: fields }) });
      else await api(`/api/workflows/apis/${sid}/operations/${op.id}`, { method: "PUT", body: JSON.stringify({ label: f.label, description: f.description, enabled: f.enabled, confirm_required: f.confirm_required, defaults,
        ...(custom ? { method: f.method, path: f.path, parameters: params, body_fields: fields } : {}) }) });
      toast(op ? "Endpoint saved" : "Endpoint added"); onSaved();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title={op ? `Edit ${op.id}` : "Add an endpoint"} onClose={onClose} wide>
      <div className="grid" style={{ marginTop: 10 }}>
        {!op && <label>Id<input value={f.id} aria-label="Endpoint id" placeholder="list_orders" onChange={(e) => setF({ ...f, id: e.target.value })} /></label>}
        <label>Name<input value={f.label} aria-label="Endpoint name" onChange={(e) => setF({ ...f, label: e.target.value })} /></label>
        <label>Method<select value={f.method} disabled={!custom} aria-label="Method" onChange={(e) => setF({ ...f, method: e.target.value })}>{["get", "post", "put", "patch", "delete", "head", "options"].map((m) => <option key={m}>{m}</option>)}</select></label>
        <label>Path (use {"{id}"} for parts that change)<input value={f.path} disabled={!custom} aria-label="Path" onChange={(e) => setF({ ...f, path: e.target.value })} /></label>
      </div>
      <label style={{ marginTop: 8 }}>What it does<textarea rows={2} value={f.description} aria-label="Description" onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
      {custom && (<>
        <div style={{ marginTop: 8 }}><b className="small">Query parameters</b></div>
        {params.map((p, i) => <div key={i} className="row" style={{ marginTop: 4 }}>
          <input value={p.name} aria-label={`Parameter ${i + 1} name`} placeholder="name" onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <select value={p.type ?? "string"} aria-label={`Parameter ${i + 1} type`} onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>{["string", "integer", "number", "boolean"].map((t) => <option key={t}>{t}</option>)}</select>
          <label className="check"><input type="checkbox" checked={!!p.required} onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)))} /> required</label>
          <button className="small" onClick={() => setParams(params.filter((_, j) => j !== i))} aria-label={`Remove parameter ${i + 1}`}>Remove</button></div>)}
        <button className="small" style={{ marginTop: 4 }} onClick={() => setParams([...params, { name: "", in: "query", type: "string" }])}>Add a parameter</button>
        <div style={{ marginTop: 8 }}><b className="small">Body fields (sent as JSON)</b></div>
        {fields.map((p, i) => <div key={i} className="row" style={{ marginTop: 4 }}>
          <input value={p.name} aria-label={`Field ${i + 1} name`} placeholder="name" onChange={(e) => setFields(fields.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <select value={p.type ?? "string"} aria-label={`Field ${i + 1} type`} onChange={(e) => setFields(fields.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>{["string", "integer", "number", "boolean", "array", "object"].map((t) => <option key={t}>{t}</option>)}</select>
          <button className="small" onClick={() => setFields(fields.filter((_, j) => j !== i))} aria-label={`Remove field ${i + 1}`}>Remove</button></div>)}
        <button className="small" style={{ marginTop: 4 }} onClick={() => setFields([...fields, { name: "", type: "string" }])}>Add a body field</button>
      </>)}
      {op && (<>
        <label style={{ marginTop: 8 }}>Default arguments (JSON, used when a box leaves them empty)<input value={f.defaults} aria-label="Default arguments" onChange={(e) => setF({ ...f, defaults: e.target.value })} /></label>
        <div className="row" style={{ marginTop: 6 }}>
          <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Switched on</label>
          <label className="check"><input type="checkbox" checked={f.confirm_required} onChange={(e) => setF({ ...f, confirm_required: e.target.checked })} /> Always ask for &quot;I confirm&quot;</label></div>
        {!custom && <div className="small mut">Method and path come from the API description. Refresh the API to pick up changes there.</div>}
      </>)}
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 12 }}><button className="pri" disabled={!op && !f.id.trim() && !f.label.trim()} onClick={save}>Save</button><button onClick={onClose}>Cancel</button></div>
    </Modal>
  );
}

// ------------------------------------------------------------------ test an endpoint
type TestRes = { ok: boolean; status?: number; body?: unknown; error?: string; truncated?: boolean; request?: { method: string; url: string; query: Record<string, unknown>; headers: Record<string, string> } };
function TestEndpoint({ sid, op, onClose }: { sid: string; op: Op; onClose: () => void }) {
  const names = op.parameters.map((p) => p.name);
  const [args, setArgs] = useState<Record<string, string>>({});
  const [json, setJson] = useState("{}");
  const [confirmed, setConfirmed] = useState(false);
  const [res, setRes] = useState<TestRes | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    let extra: Record<string, unknown> = {};
    try { extra = JSON.parse(json || "{}"); } catch { setRes({ ok: false, error: "The extra arguments must be valid JSON." }); return; }
    setBusy(true);
    try { setRes(await api<TestRes>(`/api/workflows/apis/${sid}/operations/${op.id}/test`, { method: "POST", body: JSON.stringify({ args: { ...Object.fromEntries(Object.entries(args).filter(([, v]) => v !== "")), ...extra }, confirmed }) })); }
    catch (e) { setRes({ ok: false, error: (e as Error).message }); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Test ${op.method.toUpperCase()} ${op.path}`} onClose={onClose} wide>
      {op.risk !== "read" && <div className="small" style={{ color: "var(--warn-text)", margin: "8px 0" }}>This endpoint changes data. It only runs if the API allows writes and you tick the box below.</div>}
      <div className="grid">{names.map((n) => <label key={n}>{n}<input value={args[n] ?? ""} aria-label={n} onChange={(e) => setArgs({ ...args, [n]: e.target.value })} /></label>)}</div>
      <label style={{ marginTop: 6 }}>Other arguments (JSON, for the body)<input value={json} aria-label="Other arguments" onChange={(e) => setJson(e.target.value)} /></label>
      {(op.risk !== "read" || op.confirm_required) && <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm this call may change data</label>}
      <div className="row" style={{ marginTop: 10 }}><button className="pri" disabled={busy} onClick={go}>{busy ? "Calling…" : "Send test call"}</button></div>
      {res && (<div data-testid="test-result" style={{ marginTop: 10 }}>
        {res.error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{res.error}</div>}
        {res.request && <div className="small mut">Sent {res.request.method} {res.request.url}{Object.keys(res.request.query).length ? ` ?${new URLSearchParams(res.request.query as Record<string, string>).toString()}` : ""} (keys hidden)</div>}
        {res.status !== undefined && <div className="small"><span className={`pill ${res.ok ? "ok" : "bad"}`}>{res.status}</span>{res.truncated ? " (cut at 20 000 characters)" : ""}</div>}
        {res.body !== undefined && <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>{typeof res.body === "string" ? res.body : JSON.stringify(res.body, null, 2)}</pre>}
      </div>)}
    </Modal>
  );
}

// ------------------------------------------------------------------ refresh with a diff
type Diff = { diff: { added: string[]; removed: string[]; changed: string[] }; affected: string[]; applied: boolean; warnings: string[] };
function RefreshApi({ src, onClose, onDone }: { src: Src; onClose: () => void; onDone: () => void }) {
  const fromFile = src.origin === "upload" || src.origin === "paste";
  const [content, setContent] = useState(""), [d, setD] = useState<Diff | null>(null), [err, setErr] = useState("");
  const body = (dry: boolean) => JSON.stringify({ content, dry_run: dry });
  const check = async () => { try { setErr(""); setD(await api<Diff>(`/api/workflows/apis/${src.id}/refresh`, { method: "POST", body: body(true) })); } catch (e) { setErr((e as Error).message); } };
  const apply = async () => { try { await api(`/api/workflows/apis/${src.id}/refresh`, { method: "POST", body: body(false) }); toast("API refreshed"); onDone(); } catch (e) { setErr((e as Error).message); } };
  const none = d && !d.diff.added.length && !d.diff.removed.length && !d.diff.changed.length;
  return (
    <Modal title={`Refresh ${src.name}`} onClose={onClose}>
      {fromFile ? <label style={{ marginTop: 8 }}>Upload the new version<input type="file" accept=".json,.yaml,.yml" aria-label="New file" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setContent(await f.text()); }} /></label>
        : <p className="small">Reads the description again from where it came from and shows what changed before anything is saved.</p>}
      <div className="row" style={{ marginTop: 8 }}><button disabled={fromFile && !content} onClick={check}>Check for changes</button></div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 8 }}>{err}</div>}
      {d && (<div className="small" data-testid="diff" style={{ marginTop: 10 }}>
        {none ? "Nothing changed." : <>
          {d.diff.added.length > 0 && <div><b>New:</b> {d.diff.added.join(", ")}</div>}
          {d.diff.changed.length > 0 && <div><b>Changed:</b> {d.diff.changed.join(", ")}</div>}
          {d.diff.removed.length > 0 && <div style={{ color: "var(--warn-text)" }}><b>Gone:</b> {d.diff.removed.join(", ")}</div>}
          {d.affected.length > 0 && <div style={{ color: "var(--warn-text)" }}>Workflows affected: {d.affected.join(", ")}. They will show a problem on those steps.</div>}</>}
        <div className="row" style={{ marginTop: 10 }}><button className="pri" disabled={!!none} onClick={apply}>Apply</button><button onClick={onClose}>Close</button></div>
      </div>)}
    </Modal>
  );
}

// ------------------------------------------------------------------ one API in detail
function SourceDetail({ sid, onBack, onChanged }: { sid: string; onBack: () => void; onChanged: () => void }) {
  const [src, setSrc] = useState<Src | null>(null), [ops, setOps] = useState<Op[]>([]);
  const [q, setQ] = useState(""), [method, setMethod] = useState(""), [hidden, setHidden] = useState(false);
  const [edit, setEdit] = useState<Op | "new" | null>(null), [test, setTest] = useState<Op | null>(null), [del, setDel] = useState<Op | null>(null), [refresh, setRefresh] = useState(false);
  const [settings, setSettings] = useState({ name: "", base: "", auth: "none", auth_name: "", token: "", allow_write: false });
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try {
      const s = await api<Src>(`/api/workflows/apis/${sid}`);
      setSrc(s); setSettings({ name: s.name, base: s.base, auth: s.auth.type, auth_name: s.auth.name, token: "", allow_write: s.allow_write });
      const qs = new URLSearchParams({ ...(q ? { q } : {}), ...(method ? { method } : {}), ...(hidden ? { hidden: "true" } : {}) });
      setOps((await api<{ operations: Op[] }>(`/api/workflows/apis/${sid}/operations?${qs}`)).operations);
    } catch (e) { setErr((e as Error).message); }
  }, [sid, q, method, hidden]);
  useEffect(() => { load(); }, [load]);
  if (!src) return <div className="small mut">{err || "Loading…"}</div>;
  const saveSettings = async () => {
    try { await api(`/api/workflows/apis/${sid}`, { method: "PUT", body: JSON.stringify({ name: settings.name, base: settings.base, allow_write: settings.allow_write, auth: { type: settings.auth, name: settings.auth_name }, ...(settings.token ? { token: settings.token } : {}) }) }); toast("Saved"); setErr(""); load(); onChanged(); }
    catch (e) { setErr((e as Error).message); }
  };
  const toggle = async (o: Op) => { await api(`/api/workflows/apis/${sid}/operations/${o.id}`, { method: "PUT", body: JSON.stringify({ enabled: !o.enabled }) }); load(); };
  const unhide = async (o: Op) => { await api(`/api/workflows/apis/${sid}/operations/${o.id}`, { method: "PUT", body: JSON.stringify({ hidden: false }) }); load(); };
  const remove = async (o: Op) => { try { await api(`/api/workflows/apis/${sid}/operations/${o.id}?force=true`, { method: "DELETE" }); toast(o.origin === "custom" ? "Endpoint deleted" : "Endpoint hidden"); setDel(null); load(); onChanged(); } catch (e) { setErr((e as Error).message); } };
  return (
    <div data-testid="source-detail">
      <div className="row"><button onClick={onBack}>← All APIs</button><h3 style={{ margin: 0 }}>{src.name}</h3><span className="grow" />
        <button onClick={() => setRefresh(true)}>Refresh from source</button><a className="btn" href={`${API_BASE}/api/workflows/apis/${sid}/spec`} download>Export description</a></div>
      {src.warnings.map((w, i) => <div key={i} className="small" style={{ color: "var(--warn-text)", marginTop: 4 }}>⚠ {w}</div>)}
      <div className="card" style={{ marginTop: 10 }}>
        <div className="card-head"><b>Connection</b><InfoTip id="openapi-source" /></div>
        <div className="grid" style={{ marginTop: 6 }}>
          <label>Name<input value={settings.name} aria-label="Name" onChange={(e) => setSettings({ ...settings, name: e.target.value })} /></label>
          <label>Address calls go to<input value={settings.base} aria-label="Address" onChange={(e) => setSettings({ ...settings, base: e.target.value })} /></label>
          <label>Sign-in<select value={settings.auth} aria-label="Sign-in" onChange={(e) => setSettings({ ...settings, auth: e.target.value })}>{AUTHS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
          {(settings.auth === "header" || settings.auth === "query") && <label>Header or parameter name<input value={settings.auth_name} aria-label="Key name" onChange={(e) => setSettings({ ...settings, auth_name: e.target.value })} /></label>}
          {settings.auth !== "none" && <label>{src.has_token ? "Replace the key (write-only)" : "Key or token (write-only)"}<input type="password" autoComplete="off" value={settings.token} aria-label="Key" placeholder={src.has_token ? "•••••• stored" : ""} onChange={(e) => setSettings({ ...settings, token: e.target.value })} /></label>}
        </div>
        <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={settings.allow_write} onChange={(e) => setSettings({ ...settings, allow_write: e.target.checked })} /> Allow endpoints that change data</label>
        {err && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{err}</div>}
        <div className="row" style={{ marginTop: 8 }}><button className="pri" onClick={saveSettings}>Save connection</button><span className="small mut">Imported {when(src.refreshed)}{src.used_by.length ? ` · used by ${src.used_by.join(", ")}` : ""}</span></div>
      </div>
      <div className="card" style={{ marginTop: 10 }}>
        <div className="card-head"><b>Endpoints</b><InfoTip id="openapi-endpoint" /><span className="small mut">{src.counts.enabled} of {src.counts.total} on</span><span className="grow" /><button className="pri" onClick={() => setEdit("new")}>Add an endpoint</button></div>
        <div className="row" style={{ margin: "8px 0" }}>
          <input value={q} placeholder="Search endpoints" aria-label="Search endpoints" onChange={(e) => setQ(e.target.value)} />
          <select value={method} aria-label="Method filter" onChange={(e) => setMethod(e.target.value)}><option value="">All methods</option>{["get", "post", "put", "patch", "delete"].map((m) => <option key={m}>{m}</option>)}</select>
          <label className="check"><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> show hidden</label></div>
        <table className="tbl" data-testid="ops-table"><thead><tr><th>On</th><th>Method</th><th>Path</th><th>Name</th><th>Used in</th><th /></tr></thead><tbody>
          {ops.length === 0 && <tr><td colSpan={6} className="mut small">No endpoints match.</td></tr>}
          {ops.map((o) => (<tr key={o.id} style={o.hidden ? { opacity: 0.55 } : undefined}>
            <td><input type="checkbox" checked={o.enabled} aria-label={`Switch ${o.id} on`} onChange={() => toggle(o)} /></td>
            <td><span className={`pill ${riskPill(o.risk)}`}>{o.method.toUpperCase()}</span></td>
            <td className="small" style={{ overflowWrap: "anywhere" }}>{o.path}{o.origin === "custom" && <span className="pill" style={{ marginLeft: 6 }}>custom</span>}{o.deprecated && <span className="pill warn" style={{ marginLeft: 6 }}>deprecated</span>}</td>
            <td className="small">{o.summary || o.id}<div className="mut">{o.id}</div></td>
            <td className="small">{(o.used_by ?? []).join(", ") || "-"}</td>
            <td style={{ whiteSpace: "nowrap" }}>{o.hidden ? <button className="small" onClick={() => unhide(o)}>Show</button> : <>
              <button className="small" onClick={() => setTest(o)} aria-label={`Test ${o.id}`}>Test</button> <button className="small" onClick={() => setEdit(o)} aria-label={`Edit ${o.id}`}>Edit</button> <button className="small" onClick={() => setDel(o)} aria-label={`Delete ${o.id}`}>{o.origin === "custom" ? "Delete" : "Hide"}</button></>}</td></tr>))}
        </tbody></table>
      </div>
      {edit && <EndpointForm sid={sid} op={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); onChanged(); }} />}
      {test && <TestEndpoint sid={sid} op={test} onClose={() => setTest(null)} />}
      {del && <ConfirmDelete what={`${del.origin === "custom" ? "the endpoint" : "(hide) the endpoint"} ${del.id}`} usedBy={del.used_by} onClose={() => setDel(null)} onConfirm={() => remove(del)} />}
      {refresh && <RefreshApi src={src} onClose={() => setRefresh(false)} onDone={() => { setRefresh(false); load(); onChanged(); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ the tab
export default function OpenApiTab({ onChanged }: { onChanged?: () => void }) {
  const [srcs, setSrcs] = useState<Src[] | null>(null);
  const [open, setOpen] = useState<string | null>(null), [adding, setAdding] = useState(false), [del, setDel] = useState<Src | null>(null), [dup, setDup] = useState<Src | null>(null), [refresh, setRefresh] = useState<Src | null>(null);
  const [dupId, setDupId] = useState(""), [err, setErr] = useState("");
  const load = useCallback(() => api<{ apis: Src[] }>("/api/workflows/apis").then((r) => setSrcs(r.apis)).catch((e) => { setErr(e.message); setSrcs([]); }), []);
  useEffect(() => { load(); }, [load]);
  const changed = useMemo(() => () => { load(); onChanged?.(); }, [load, onChanged]);
  if (open) return <SourceDetail sid={open} onBack={() => { setOpen(null); changed(); }} onChanged={changed} />;
  const remove = async (s: Src) => { try { await api(`/api/workflows/apis/${s.id}?force=true`, { method: "DELETE" }); toast("API deleted"); setDel(null); changed(); } catch (e) { setErr((e as Error).message); } };
  const duplicate = async (dup: Src) => { try { await api(`/api/workflows/apis/${dup.id}/duplicate`, { method: "POST", body: JSON.stringify({ id: dupId }) }); toast("API duplicated (the key is not copied)"); setDup(null); changed(); } catch (e) { setErr((e as Error).message); } };
  return (
    <div data-testid="openapi-tab">
      <div className="row"><p className="small mut grow" style={{ margin: 0 }}>Upload an OpenAPI file, give an address or paste one. Every endpoint becomes a box you can use in a workflow. Only that API&apos;s own server is called, and anything that changes data needs your confirmation.</p>
        <button className="pri" onClick={() => setAdding(true)}>Add an API</button></div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", margin: "8px 0" }}>{err}</div>}
      {srcs && srcs.length === 0 && <div className="card" style={{ marginTop: 12 }}>No APIs yet. Add one to use its endpoints as workflow steps.</div>}
      {srcs && srcs.length > 0 && (
        <table className="tbl" data-testid="api-table" style={{ marginTop: 12 }}><thead><tr><th>Name</th><th>Address</th><th>Sign-in</th><th>Endpoints</th><th>Writes</th><th>Refreshed</th><th>Used by</th><th /></tr></thead><tbody>
          {srcs.map((s) => (<tr key={s.id}>
            <td><button className="link" onClick={() => setOpen(s.id)}><b>{s.name}</b></button><div className="small mut">{s.id} · {s.origin}</div></td>
            <td className="small" style={{ overflowWrap: "anywhere" }}>{s.base}</td>
            <td className="small">{s.auth.type}{s.has_token ? " · key stored" : ""}</td>
            <td className="small">{s.counts.enabled}/{s.counts.total}</td>
            <td><span className={`pill ${s.allow_write ? "warn" : "ok"}`}>{s.allow_write ? "allowed" : "read only"}</span></td>
            <td className="small">{when(s.refreshed)}</td>
            <td className="small">{s.used_by.length ? `${s.used_by.length} workflow${s.used_by.length > 1 ? "s" : ""}` : "-"}</td>
            <td style={{ whiteSpace: "nowrap" }}><button className="small" onClick={() => setOpen(s.id)}>Open</button> <button className="small" onClick={() => setRefresh(s)} aria-label={`Refresh ${s.id}`}>Refresh</button> <button className="small" onClick={() => { setDup(s); setDupId(`${s.id}_copy`); }} aria-label={`Duplicate ${s.id}`}>Duplicate</button> <button className="small" onClick={() => setDel(s)} aria-label={`Delete ${s.id}`}>Delete</button></td></tr>))}
        </tbody></table>)}
      {adding && <AddApi onClose={() => setAdding(false)} onDone={(id) => { setAdding(false); changed(); setOpen(id); }} />}
      {del && <ConfirmDelete what={`the API ${del.name}`} usedBy={del.used_by} onClose={() => setDel(null)} onConfirm={() => remove(del)} />}
      {refresh && <RefreshApi src={refresh} onClose={() => setRefresh(null)} onDone={() => { setRefresh(null); changed(); }} />}
      {dup && <Modal title={`Duplicate ${dup.name}`} onClose={() => setDup(null)}><p className="small">Copies the description and your edits, not the key.</p><label>New id<input value={dupId} aria-label="New id" onChange={(e) => setDupId(e.target.value.toLowerCase())} /></label>
        <div className="row" style={{ marginTop: 10 }}><button className="pri" onClick={() => duplicate(dup)}>Duplicate</button><button onClick={() => setDup(null)}>Cancel</button></div></Modal>}
    </div>
  );
}
