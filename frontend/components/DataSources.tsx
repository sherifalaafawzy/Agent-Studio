"use client";
// Settings > Data sources: databases, warehouses and Salesforce orgs agents may READ. Read-only by design: the server refuses anything that is not a
// plain query, limits the result and logs every access. Credentials are write-only. Each source can be tested, browsed and queried from here.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Field = { name: string; label: string; secret: boolean; required: boolean; help: string; default: string };
type Kind = { kind: string; label: string; description: string; docs: string; fields: Field[]; note: string; managed_mcp: { url: string; tools: string; status: string; docs: string; advice: string } | null };
type Limits = { max_rows: number; max_bytes: number; timeout_s: number };
type Source = { id: string; kind: string; label: string; description: string; config: Record<string, string>; limits: Limits; redact: boolean; enabled: boolean; secrets_set: string[] };
type Result = { columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean; redacted: boolean };
type Audit = { id: number; ts: number; agent: string; kind: string; statement: string; rows: number; ms: number; ok: number; error: string };

function SourceForm({ kinds, initial, onSave, onCancel }: { kinds: Kind[]; initial?: Source; onSave: (body: Record<string, unknown>) => Promise<boolean>; onCancel: () => void }) {
  const [kind, setKind] = useState(initial?.kind ?? kinds[0]?.kind ?? "sqlite");
  const [id, setId] = useState(initial?.id ?? "");
  const [label, setLabel] = useState(initial?.label ?? "");
  const [conf, setConf] = useState<Record<string, string>>(initial?.config ?? {});
  const [sec, setSec] = useState<Record<string, string>>({});
  const [rows, setRows] = useState(String(initial?.limits.max_rows ?? 100));
  const k = kinds.find((x) => x.kind === kind);
  return (
    <div className="card" data-testid="source-form">
      <div className="grid">
        <label>Name<input value={id} disabled={!!initial} placeholder="e.g. crm-prod" onChange={(e) => setId(e.target.value.toLowerCase())} /></label>
        <label>Kind<select value={kind} disabled={!!initial} onChange={(e) => { setKind(e.target.value); setConf({}); setSec({}); }}>{kinds.map((x) => <option key={x.kind} value={x.kind}>{x.label}</option>)}</select></label>
        <label>Label (optional)<input value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label>Most rows per query<input type="number" min={1} max={5000} value={rows} onChange={(e) => setRows(e.target.value)} /></label>
      </div>
      {k && <div className="small mut" style={{ margin: "6px 0" }}>{k.description} {k.note}</div>}
      <div className="grid">
        {k?.fields.map((f) => (
          <label key={f.name}>{f.label}{!f.required && " (optional)"}
            {f.secret
              ? <input type="password" autoComplete="off" value={sec[f.name] ?? ""} placeholder={initial?.secrets_set.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"} onChange={(e) => setSec({ ...sec, [f.name]: e.target.value })} />
              : <input value={conf[f.name] ?? ""} placeholder={f.default || f.help} onChange={(e) => setConf({ ...conf, [f.name]: e.target.value })} />}
          </label>))}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!id.trim()} onClick={() => onSave({ id: id.trim(), kind, label, config: Object.fromEntries(Object.entries(conf).filter(([, v]) => v !== "")),
          secrets: Object.fromEntries(Object.entries(sec).filter(([, v]) => v)), limits: { max_rows: Number(rows) } })}>{initial ? "Save source" : "Add source"}</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function Explorer({ s }: { s: Source }) {
  const [tables, setTables] = useState<{ name: string; kind: string }[] | null>(null);
  const [cols, setCols] = useState<{ table: string; items: { name: string; type: string }[] } | null>(null);
  const [sql, setSql] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [audit, setAudit] = useState<Audit[] | null>(null);
  const [err, setErr] = useState("");
  const guard = async <T,>(fn: () => Promise<T>): Promise<T | null> => { setErr(""); try { return await fn(); } catch (e) { setErr((e as Error).message); return null; } };
  return (
    <div style={{ marginTop: 8 }} data-testid="explorer">
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        <button className="small" onClick={async () => { const r = await guard(() => api<{ tables: { name: string; kind: string }[] }>(`/api/data/sources/${s.id}/schema`)); if (r) { setTables(r.tables); setCols(null); } }}>Browse tables</button>
        <button className="small" onClick={async () => { const r = await guard(() => api<{ audit: Audit[] }>(`/api/data/audit?source=${s.id}&limit=10`)); if (r) setAudit(r.audit); }}>Recent activity</button>
      </div>
      {err && <p className="err small" role="alert">{err}</p>}
      {tables && <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 6 }}>{tables.length === 0 ? <span className="small mut">No tables found.</span> : tables.map((t) => (
        <button key={t.name} className="chip small" onClick={async () => { const r = await guard(() => api<{ columns: { name: string; type: string }[] }>(`/api/data/sources/${s.id}/schema?table=${encodeURIComponent(t.name)}`)); if (r) { setCols({ table: t.name, items: r.columns }); setSql((q) => q || `SELECT * FROM ${t.name} LIMIT 10`); } }}>{t.name}</button>))}</div>}
      {cols && <div className="small" data-testid="columns" style={{ marginTop: 4 }}><b>{cols.table}</b>: {cols.items.map((c) => `${c.name} (${c.type || "?"})`).join(", ")}</div>}
      <label style={{ marginTop: 8 }}>Try a read-only query
        <textarea rows={2} aria-label="Query" value={sql} placeholder={s.kind === "salesforce" ? "SELECT Id, Name FROM Account LIMIT 10" : "SELECT * FROM my_table LIMIT 10"} onChange={(e) => setSql(e.target.value)} /></label>
      <div className="row" style={{ marginTop: 4 }}>
        <button className="small" disabled={!sql.trim()} onClick={async () => { const r = await guard(() => api<Result>(`/api/data/sources/${s.id}/query`, { method: "POST", body: JSON.stringify({ sql }) })); if (r) setRes(r); }}>Run</button>
        <span className="small mut">Only reads are accepted. Results are limited and may have personal data masked.</span>
      </div>
      {res && (
        <div style={{ overflowX: "auto", marginTop: 6 }} data-testid="result">
          <table className="tbl"><thead><tr>{res.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{res.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? <span className="mut">null</span> : String(v)}</td>)}</tr>)}</tbody></table>
          <div className="small mut">{res.row_count} row{res.row_count === 1 ? "" : "s"}{res.truncated ? " (more were cut off by the limits)" : ""}{res.redacted ? " · some values were masked" : ""}</div>
        </div>)}
      {audit && (
        <ul className="small" data-testid="audit" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {audit.length === 0 ? <li className="mut">Nothing yet.</li> : audit.map((a) => <li key={a.id} style={a.ok ? undefined : { color: "var(--bad)" }}>{new Date(a.ts * 1000).toLocaleString()} · {a.agent || "system"} · {a.kind} · {a.statement || "(no statement)"} · {a.ok ? `${a.rows} rows, ${a.ms} ms` : a.error}</li>)}
        </ul>)}
    </div>
  );
}

export default function DataSources() {
  const [data, setData] = useState<{ sources: Source[]; kinds: Kind[] } | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const load = useCallback(() => api<{ sources: Source[]; kinds: Kind[] }>("/api/data/sources").then((d) => { setData(d); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!data) return err ? <p className="err" role="alert">{err}</p> : null;
  const save = async (body: Record<string, unknown>, ok = "Saved") => {
    try { await api("/api/data/sources", { method: "PUT", body: JSON.stringify(body) }); await load(); setErr(""); toast(ok); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  const test = async (id: string) => { try { setTests({ ...tests, [id]: await api(`/api/data/sources/${id}/test`, { method: "POST" }) }); } catch (e) { setTests({ ...tests, [id]: { ok: false, detail: (e as Error).message } }); } };
  const remove = async (s: Source) => {
    if (!confirm(`Remove “${s.id}”? Its stored credentials and the data products that describe it are deleted. The data itself is not touched.`)) return;
    try { await api(`/api/data/sources/${s.id}`, { method: "DELETE" }); await load(); toast("Removed"); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <p className="page-sub">Databases, warehouses and Salesforce orgs your agents may <b>read</b>. Tick the ones each agent may use in its editor. Describe what is in them under <a href="/discovery/">AI-BOM &gt; Data products</a>.</p>
      <div className="card" data-testid="data-sources">
        <div className="card-head"><b>Data sources</b><InfoTip id="data-sources" /></div>
        <p className="small mut">Read-only: anything that is not a plain query is refused, results are limited in rows, size and time, personal data can be masked and every access is logged. Use an account that can only see what the agent needs: permissions are the real protection.</p>
        {err && <p className="err" role="alert">{err}</p>}
        {data.sources.map((s) => editing === s.id ? <SourceForm key={s.id} kinds={data.kinds} initial={s} onSave={async (b) => { const ok = await save(b); if (ok) setEditing(null); return ok; }} onCancel={() => setEditing(null)} /> : (
          <div key={s.id} className="card sink-card" data-testid="source">
            <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
              <b>{s.label}</b><span className="pill">{data.kinds.find((k) => k.kind === s.kind)?.label ?? s.kind}</span><span className="small mut">{s.id}</span><span className="grow" />
              <label className="switch"><input type="checkbox" aria-label={`${s.id} on`} checked={s.enabled} onChange={(e) => save({ id: s.id, kind: s.kind, enabled: e.target.checked })} /><i /></label>
            </div>
            <label className="check small" style={{ marginTop: 4, flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" aria-label={`${s.id} mask personal data`} checked={s.redact} onChange={(e) => save({ id: s.id, kind: s.kind, redact: e.target.checked })} /> Mask personal data and sensitive columns in results</label>
            {data.kinds.find((k) => k.kind === s.kind)?.managed_mcp && (() => { const m = data.kinds.find((k) => k.kind === s.kind)!.managed_mcp!; return <div className="small mut" style={{ marginTop: 4 }}>The vendor also offers an official MCP server ({m.status}): <code>{m.url}</code>. {m.advice} It needs an OAuth sign-in Agent Studio cannot do for you yet, so this source uses its own read-only connection. <a href={m.docs} target="_blank" rel="noreferrer">Docs</a></div>; })()}
            <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
              <button className="small" onClick={() => test(s.id)}>Test</button>
              <button className="small" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Close" : "Explore"}</button>
              <button className="small" onClick={() => setEditing(s.id)}>Edit</button>
              <button className="ghost small" onClick={() => remove(s)}>Remove</button>
              {tests[s.id] && <span className={`pill ${tests[s.id].ok ? "ok" : "bad"}`} role="status">{tests[s.id].detail}</span>}
            </div>
            {open === s.id && <Explorer s={s} />}
          </div>))}
        {editing === "" ? <SourceForm kinds={data.kinds} onSave={async (b) => { const ok = await save(b, "Source added"); if (ok) setEditing(null); return ok; }} onCancel={() => setEditing(null)} /> : <button style={{ marginTop: 8 }} onClick={() => setEditing("")}>Add a data source</button>}
      </div>
    </>
  );
}
