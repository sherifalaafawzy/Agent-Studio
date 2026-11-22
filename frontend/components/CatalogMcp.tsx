"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ICheck, IPlug } from "@/components/Icons";

type Field = { name: string; label: string; secret: boolean; required: boolean; where: string; help: string; placeholder: string; default: string };
export type CatalogServer = {
  id: string; label: string; domains: string[]; publisher: string; official: boolean; description: string; kind: string; package: string;
  read_only: "always" | "enforced" | "partial" | "credential" | "none"; writes: string; data_leaves: string; license: string; docs: string; verified: string;
  transport: string; command_line: string; fields: Field[]; setup: string; can_be_read_only: boolean; installed: boolean; allow_writes: boolean; ready: boolean;
};

export const MODE_LABEL: Record<CatalogServer["read_only"], { text: string; tone: string }> = {
  always: { text: "Read-only", tone: "ok" },
  enforced: { text: "Read-only switch", tone: "ok" },
  partial: { text: "Limited writes", tone: "warn" },
  credential: { text: "Limited by the key", tone: "warn" },
  none: { text: "Can change things", tone: "bad" },
};
const DOMAIN_LABEL: Record<string, string> = { accounting: "Accounting", sales: "Sales", marketing: "Marketing", manufacturing: "Manufacturing", engineering: "Engineering", data: "Data", "machine-learning": "Machine learning", "deep-learning": "Deep learning" };

/** The settings form for one catalog server: what it can change, where data goes, its fields and the read-only confirm. Used by the Catalog page and the MCP page. */
export function CatalogForm({ s, onDone }: { s: CatalogServer; onDone: (s: CatalogServer) => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(s.fields.filter((f) => f.default).map((f) => [f.name, f.default])));
  const [confirm, setConfirm] = useState(s.installed && s.allow_writes);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const needsTick = s.read_only !== "always" && s.read_only !== "enforced";
  async function install() {
    setBusy(true); setErr("");
    try {
      await api(`/api/catalog/mcp/${s.id}/install`, { method: "POST", body: JSON.stringify({ values, allow_writes: confirm }) });
      toast(`${s.label} added`); onDone(s);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="cat-panel">
      {s.installed && <p className="small mut" style={{ margin: "0 0 8px" }}>Saving replaces the stored settings, so enter the values again.</p>}
      <div className="kv">
        <span>What it can change</span><span>{s.writes}</span>
        <span>Where your data goes</span><span>{s.data_leaves}</span>
        <span>Licence</span><span>{s.license}</span>
        {s.setup && <><span>Before you start</span><span>{s.setup}</span></>}
      </div>
      {s.fields.map((f) => (
        <label key={f.name} style={{ marginTop: 8 }}>{f.label}{f.required ? "" : " (optional)"}
          <input type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.name] ?? ""} placeholder={f.placeholder || (f.secret ? "stored on this PC, never shown again" : "")}
            onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
          {f.help && <span className="small mut">{f.help}</span>}
        </label>))}
      {s.read_only !== "always" && (
        <label style={{ flexDirection: "row", gap: 8, alignItems: "flex-start", marginTop: 10 }}>
          <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          <span className="small">{needsTick ? "I understand this server can change things and I want to add it." : "Allow it to change things (leave off to keep it read-only)."}</span>
        </label>)}
      <details className="small mut" style={{ marginTop: 8 }}><summary>How it is started and checked</summary>
        <div><code>{s.command_line}</code></div><div>{s.verified}</div>
        <a href={s.docs} target="_blank" rel="noreferrer">Documentation</a></details>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="row" style={{ marginTop: 10, justifyContent: "flex-end" }}>
        <button className="acc" disabled={busy || (needsTick && !confirm)} onClick={install}>{s.installed ? "Save" : "Add server"}</button>
      </div>
    </div>
  );
}

/** The public MCP servers: browse by domain, see what each can change and where data goes, add with one click. */
export default function CatalogMcp({ domain, q }: { domain: string; q: string }) {
  const [list, setList] = useState<CatalogServer[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [test, setTest] = useState<Record<string, { ok: boolean; text: string }>>({});

  const load = useCallback(async () => {
    try { setList(await api<CatalogServer[]>("/api/catalog/mcp")); setErr(""); } catch (e) { setErr("Backend offline: " + (e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const begin = (s: CatalogServer) => { setOpen(open === s.id ? null : s.id); setErr(""); };
  async function mode(s: CatalogServer, allow: boolean) {
    if (allow && !window.confirm(`Allow ${s.label} to change things?\n\n${s.writes}`)) return;
    try { await api(`/api/catalog/mcp/${s.id}/mode`, { method: "PUT", body: JSON.stringify({ allow_writes: allow }) }); await load(); toast(allow ? "Writes allowed" : "Back to read-only"); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function check(s: CatalogServer) {
    setTest((t) => ({ ...t, [s.id]: { ok: true, text: "Starting… the first run may download the server." } }));
    try {
      const r = await api<{ ok: boolean; tools?: { name: string }[]; error?: string }>(`/api/mcp/${s.id}/test`, { method: "POST" });
      setTest((t) => ({ ...t, [s.id]: r.ok ? { ok: true, text: `Works: ${r.tools?.length ?? 0} tools (${(r.tools ?? []).slice(0, 5).map((x) => x.name).join(", ")}${(r.tools?.length ?? 0) > 5 ? ", …" : ""})` } : { ok: false, text: r.error ?? "did not start" } }));
    } catch (e) { setTest((t) => ({ ...t, [s.id]: { ok: false, text: (e as Error).message } })); }
  }

  const shown = list.filter((s) => (!domain || s.domains.includes(domain)) && (!q || `${s.label} ${s.description} ${s.publisher}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <div>
      <p className="small mut" style={{ margin: "0 0 10px" }}>Each server was checked on its package registry or by contacting it. Versions are pinned. <InfoTip id="catalog-readonly" /></p>
      {err && <p className="err">{err}</p>}
      <div className="cat-grid" data-testid="mcp-grid">
        {shown.map((s) => {
          const mode_ = MODE_LABEL[s.read_only];
          return (
            <div key={s.id} className="card cat-card" data-server={s.id}>
              <div className="card-head"><IPlug size={16} /><b>{s.label}</b><span className="grow" />
                <span className={`pill ${mode_.tone}`}>{mode_.text}</span></div>
              <div className="small mut">{s.publisher}{s.official ? " · official" : " · community"}</div>
              <p className="small" style={{ margin: "8px 0" }}>{s.description}</p>
              <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>{s.domains.map((d) => <span key={d} className="pill">{DOMAIN_LABEL[d] ?? d}</span>)}</div>
              <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
                {s.installed
                  ? <><span className={`pill ${s.ready ? "ok" : "warn"}`}>{s.ready ? <><ICheck size={12} /> Added</> : "Added · needs setup"}</span>
                    <button onClick={() => check(s)}>Test</button>
                    <a className="small" href={`/mcp/?server=${s.id}`}>Open on the MCP page</a>
                    {s.read_only === "enforced" && <button className={s.allow_writes ? "ghost" : ""} onClick={() => mode(s, !s.allow_writes)}>{s.allow_writes ? "Make read-only" : "Allow writes…"}</button>}
                    <button className="ghost" onClick={() => begin(s)}>{open === s.id ? "Close" : "Change settings"}</button></>
                  : <button className="acc" onClick={() => begin(s)}>{open === s.id ? "Close" : "Add"}</button>}
              </div>
              {test[s.id] && <p className={`small ${test[s.id].ok ? "mut" : "err"}`} role="status">{test[s.id].text}</p>}
              {open === s.id && <CatalogForm key={s.id} s={s} onDone={async () => { setOpen(null); await load(); }} />}
            </div>
          );
        })}
      </div>
      {shown.length === 0 && !err && <div className="empty">No servers match.</div>}
      {domain === "manufacturing" && <p className="small mut">Manufacturing has few public servers: Excel and DuckDB cover spreadsheets and data files, and FreeCAD covers CAD.</p>}
    </div>
  );
}
