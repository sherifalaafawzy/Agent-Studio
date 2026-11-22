"use client";
// AI-BOM page > Data products and agents: name, own and classify the data your sources hold, and see which agent does what and which data each can reach
// (Agent Studio's own agents and the external coding agents). Products are descriptions: they travel in the AI-BOM and the discovery catalog.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

const CLASSES = ["public", "internal", "confidential", "restricted"];
type Product = { id: string; name: string; description: string; source: string; objects: string[]; classification: string; owner: string; contains_pii: boolean; tags: string[] };
type Row = { id: string; name: string; kind: string; purpose: string; framework?: string; runtime?: string; sandbox?: string; data_sources: string[]; data_products: string[]; used_by?: string[]; launcher?: string; note?: string };
type Desc = { agents: Row[]; products: Product[]; sources: { id: string; label: string; kind: string; enabled: boolean }[] };
const blank: Product = { id: "", name: "", description: "", source: "", objects: [], classification: "internal", owner: "", contains_pii: false, tags: [] };

function ProductForm({ initial, sources, onSave, onCancel }: { initial: Product; sources: Desc["sources"]; onSave: (p: Product) => void; onCancel: () => void }) {
  const [p, setP] = useState<Product>({ ...initial, source: initial.source || sources[0]?.id || "" });
  const [objs, setObjs] = useState(initial.objects.join(", "));
  const editing = !!initial.id;
  return (
    <div className="card" data-testid="product-form">
      <div className="grid">
        <label>Id<input value={p.id} disabled={editing} placeholder="e.g. customer-orders" onChange={(e) => setP({ ...p, id: e.target.value.toLowerCase() })} /></label>
        <label>Name<input value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
        <label>Data source<select value={p.source} onChange={(e) => setP({ ...p, source: e.target.value })}>{sources.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
        <label>How sensitive is it?<select value={p.classification} onChange={(e) => setP({ ...p, classification: e.target.value })}>{CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <label>Owner<input value={p.owner} placeholder="a team or a person" onChange={(e) => setP({ ...p, owner: e.target.value })} /></label>
        <label>Tables or objects (comma separated)<input value={objs} onChange={(e) => setObjs(e.target.value)} /></label>
      </div>
      <label style={{ marginTop: 6 }}>What it is<input value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} /></label>
      <label className="check" style={{ marginTop: 6, flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={p.contains_pii} onChange={(e) => setP({ ...p, contains_pii: e.target.checked })} /> Contains personal data</label>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="pri" disabled={!p.id.trim() || !p.name.trim() || !p.source} onClick={() => onSave({ ...p, id: p.id.trim(), name: p.name.trim(), objects: objs.split(",").map((x) => x.trim()).filter(Boolean) })}>Save data product</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function DataProducts() {
  const [d, setD] = useState<Desc | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<Product | null>(null);
  const load = useCallback(() => api<Desc>("/api/data/descriptors").then((x) => { setD(x); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!d) return err ? <p className="err" role="alert">{err}</p> : null;
  const save = async (p: Product) => {
    try { await api("/api/data/products", { method: "PUT", body: JSON.stringify(p) }); setEditing(null); await load(); toast("Data product saved"); }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); }
  };
  const remove = async (p: Product) => {
    if (!confirm(`Remove the data product “${p.name}”? The data itself is not touched.`)) return;
    try { await api(`/api/data/products/${p.id}`, { method: "DELETE" }); await load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <section className="card" data-testid="data-products" style={{ marginTop: 14 }}>
      <div className="card-head"><b>Data products and agents</b><InfoTip id="data-products" /></div>
      <p className="small mut">Describe what is in your data sources, who owns it and how sensitive it is. Then see which agent does what and which data it can reach. Agent access is granted per data source in the agent&apos;s editor.</p>
      {err && <p className="err" role="alert">{err}</p>}
      {d.sources.length === 0 && <p className="small mut">Add a data source in <a href="/settings/#data">Settings &gt; Data sources</a> first.</p>}
      {d.products.map((p) => editing?.id === p.id ? <ProductForm key={p.id} initial={p} sources={d.sources} onSave={save} onCancel={() => setEditing(null)} /> : (
        <div key={p.id} className="row" data-testid="product" style={{ flexWrap: "wrap", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
          <b>{p.name}</b><span className={`pill ${p.classification === "restricted" || p.classification === "confidential" ? "warn" : ""}`}>{p.classification}</span>
          {p.contains_pii && <span className="pill warn">personal data</span>}
          <span className="small mut grow">{p.source}{p.objects.length ? ` · ${p.objects.join(", ")}` : ""}{p.owner ? ` · owner: ${p.owner}` : ""}</span>
          <button className="small" onClick={() => setEditing(p)}>Edit</button><button className="ghost small" onClick={() => remove(p)}>Remove</button>
        </div>))}
      {editing && !editing.id ? <ProductForm initial={editing} sources={d.sources} onSave={save} onCancel={() => setEditing(null)} />
        : !editing && d.sources.length > 0 && <button style={{ marginTop: 8 }} onClick={() => setEditing(blank)}>Add a data product</button>}

      <div className="lbl" style={{ marginTop: 16 }}>Which agent does what</div>
      <div style={{ overflowX: "auto" }}>
        <table className="tbl" data-testid="agent-matrix">
          <thead><tr><th>Agent</th><th>What it does</th><th>Runs</th>{d.products.map((p) => <th key={p.id} title={p.name}>{p.name}</th>)}</tr></thead>
          <tbody>{d.agents.map((a) => (
            <tr key={a.id}>
              <td><b>{a.name}</b>{a.kind === "external" && <span className="pill" style={{ marginLeft: 4 }}>external</span>}</td>
              <td className="small" style={{ minWidth: 220 }}>{a.purpose || <span className="mut">no description</span>}{a.kind === "external" && a.used_by && a.used_by.length > 0 && <div className="mut">called by {a.used_by.join(", ")}</div>}</td>
              <td className="small" style={{ minWidth: 170 }}>{a.kind === "external" ? `outside Agent Studio${a.launcher ? ` (${a.launcher})` : ""}` : `${a.runtime === "local" ? "this PC" : a.runtime} · ${a.framework} · ${a.sandbox} sandbox`}</td>
              {d.products.map((p) => <td key={p.id} aria-label={`${a.name} ${p.name}`}>{a.data_products.includes(p.id) ? "✓" : a.kind === "external" ? <span className="mut" title="not governed here">?</span> : "–"}</td>)}
            </tr>))}</tbody>
        </table>
      </div>
      <div className="small mut" style={{ marginTop: 4 }}>✓ can read it · – cannot · ? external agents use their own accounts, so Agent Studio cannot tell.</div>
    </section>
  );
}
