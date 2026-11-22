"use client";
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api, ApiError, Provider } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IBook, IEdit, IPlay, IPlus, ISearch, IServer, ITrash, IX } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";

// Knowledge bases (RAG): where vectors live (stores), what they hold (knowledge bases and documents), and how to test retrieval.
// Store kinds and Docker stacks come from the API, so new backends show up here without a UI change.

type Field = { name: string; label: string; secret: boolean; required: boolean; placeholder: string; default: string; help?: string; description?: string };
type Kind = { id: string; label: string; description: string; docker_stack: string | null; fields: Field[] };
type Store = { id: string; label: string; kind: string; kind_label: string; builtin: boolean; config: Record<string, string>; secrets_set: Record<string, boolean>; docker_stack: string | null; kbs: number };
type Kb = { id: string; label: string; description: string; store_id: string; store_label: string; store_kind: string; provider: string; model: string; dim: number;
  chunk_size: number; chunk_overlap: number; docs: number; chunks: number; managed?: boolean };
type Doc = { doc_id: string; source: string; chunks: number; bytes: number; added: number };
type Hit = { kb: string; kb_label: string; doc_id: string; source: string; chunk: number; text: string; score: number };
type Outcome = { name: string; status: string; chunks?: number; error?: string };
type Stack = { id: string; label: string; image: string; port: number; store_kind: string; notes: string; docker: boolean; state: "unavailable" | "missing" | "stopped" | "running"; detail: string };
type EmbModel = { model: string; dim: number | null; input_per_m: number | null };

const MIN_CHUNK = 200, MAX_CHUNK = 8000;
const msg = (e: unknown) => (e as Error).message;
const bytes = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);

/** Multipart upload: api() forces a JSON content type, which would break the boundary, so this talks to fetch directly. */
async function upload(path: string, files: File[]): Promise<Outcome[]> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const res = await fetch(API_BASE + path, { method: "POST", body: fd });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as Outcome[];
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="backdrop" onClick={onClose}><div className="modal kn-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
      <div className="row"><h3 style={{ margin: 0 }}>{title}</h3><span className="grow" /><button className="icon-btn" aria-label="Close" onClick={onClose}><IX size={16} /></button></div>
      {children}
    </div></div>
  );
}

// ------------------------------------------------------------------ create a knowledge base
function CreateKb({ stores, providers, onClose, onCreated }: { stores: Store[]; providers: Provider[]; onClose: () => void; onCreated: (id: string) => void }) {
  const enabled = providers.filter((p) => p.enabled);
  const [f, setF] = useState({ id: "", label: "", description: "", store_id: stores[0]?.id ?? "local", provider: enabled[0]?.id ?? "", model: "", chunk_size: "1000", chunk_overlap: "150" });
  const [models, setModels] = useState<EmbModel[]>([]);
  const [modelNote, setModelNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));

  useEffect(() => {
    if (!f.provider) return;
    let live = true;
    setModels([]); setModelNote("");
    api<{ models: EmbModel[] }>(`/api/rag/embedding-models/${f.provider}`)
      .then((r) => { if (live) { setModels(r.models); if (!r.models.length) setModelNote("No embedding models are listed for this provider: type the model name."); } })
      .catch((e) => { if (live) setModelNote(msg(e)); });
    return () => { live = false; };
  }, [f.provider]);

  const size = Number(f.chunk_size), overlap = Number(f.chunk_overlap);
  const chunkErr = !Number.isInteger(size) || size < MIN_CHUNK || size > MAX_CHUNK ? `Chunk size must be between ${MIN_CHUNK} and ${MAX_CHUNK} characters.`
    : !Number.isInteger(overlap) || overlap < 0 || overlap > Math.floor(size / 2) ? "Overlap must be between 0 and half the chunk size." : "";
  const picked = models.find((m) => m.model === f.model);

  async function create() {
    setBusy(true); setErr("");
    try {
      await api("/api/rag/kbs", { method: "POST", body: JSON.stringify({ id: f.id, label: f.label, description: f.description, store_id: f.store_id, provider: f.provider,
        model: f.model, chunk_size: size, chunk_overlap: overlap }) });
      toast(`Created ${f.label || f.id}`);
      onCreated(f.id);
    } catch (e) { setErr(msg(e)); }
    setBusy(false);
  }

  return (
    <Modal title="New knowledge base" onClose={onClose}>
      <div className="section-note"><span>Creating a knowledge base makes one small paid embedding call (a probe to learn the vector size). Adding documents later is billed the same way and counts against the provider&apos;s spend cap.</span></div>
      <div className="grid">
        <label>Id<input value={f.id} placeholder="product-docs" onChange={(e) => set("id", e.target.value)} /></label>
        <label>Name<input value={f.label} placeholder="Product docs" onChange={(e) => set("label", e.target.value)} /></label>
      </div>
      <label style={{ marginTop: 10 }}>What it holds (agents read this)<input value={f.description} onChange={(e) => set("description", e.target.value)} /></label>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Store
          <select value={f.store_id} onChange={(e) => set("store_id", e.target.value)}>{stores.map((s) => <option key={s.id} value={s.id}>{s.label} ({s.kind_label})</option>)}</select></label>
        <label>Embedding provider
          <select value={f.provider} onChange={(e) => { set("provider", e.target.value); set("model", ""); }}>
            {enabled.length === 0 && <option value="">No enabled provider</option>}
            {enabled.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        <label>Embedding model
          <input value={f.model} list="kn-models" placeholder="choose or type a model" onChange={(e) => set("model", e.target.value)} />
          <datalist id="kn-models">{models.map((m) => <option key={m.model} value={m.model} />)}</datalist><InfoTip id="embeddings" /></label>
      </div>
      {enabled.length === 0 && <div className="small err" style={{ marginTop: 6 }}>Enable a model provider in Settings first: embeddings run through it.</div>}
      {modelNote && <div className="small mut" style={{ marginTop: 6 }}>{modelNote}</div>}
      {picked && <div className="small mut" style={{ marginTop: 6 }}>{picked.dim ? `${picked.dim} dimensions` : "dimensions unknown"} · {picked.input_per_m === null ? "price unknown" : picked.input_per_m === 0 ? "free" : `$${picked.input_per_m} per million tokens`}</div>}
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Chunk size (characters, {MIN_CHUNK} to {MAX_CHUNK})<input type="number" min={MIN_CHUNK} max={MAX_CHUNK} value={f.chunk_size} onChange={(e) => set("chunk_size", e.target.value)} /></label>
        <label>Overlap (0 to half the size)<input type="number" min={0} value={f.chunk_overlap} onChange={(e) => set("chunk_overlap", e.target.value)} /></label>
      </div>
      {chunkErr && <div className="small err" style={{ marginTop: 6 }}>{chunkErr}</div>}
      {err && <div className="small err" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
        <button className="ghost" onClick={onClose}>Cancel</button>
        <button className="acc" disabled={busy || !f.id || !f.provider || !f.model.trim() || !!chunkErr} onClick={create}>{busy ? "Creating…" : "Create"}</button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ one knowledge base
function KbDetail({ kb, kbs, onClose, onChanged }: { kb: Kb; kbs: Kb[]; onClose: () => void; onChanged: () => void }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [docErr, setDocErr] = useState("");
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [addErr, setAddErr] = useState("");
  const [busy, setBusy] = useState("");
  const [drag, setDrag] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const [k, setK] = useState("5");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [searchErr, setSearchErr] = useState("");
  const [searching, setSearching] = useState(false);

  const loadDocs = useCallback(() => api<Doc[]>(`/api/rag/kbs/${kb.id}/docs`).then((d) => { setDocs(d); setDocErr(""); }).catch((e) => setDocErr(msg(e))), [kb.id]);
  useEffect(() => { setDocs(null); setHits(null); setOutcomes([]); loadDocs(); }, [loadDocs]);

  async function run<T>(what: string, job: () => Promise<T>, after: (r: T) => void) {
    setBusy(what); setAddErr("");
    try { after(await job()); } catch (e) { setAddErr(msg(e)); }
    setBusy(""); loadDocs(); onChanged();
  }
  const files = (list: FileList | File[] | null) => { const arr = Array.from(list ?? []); if (arr.length) run("upload", () => upload(`/api/rag/kbs/${kb.id}/docs`, arr), setOutcomes); };
  const addText = () => run("text", () => api<Outcome>(`/api/rag/kbs/${kb.id}/text`, { method: "POST", body: JSON.stringify({ title, text }) }), (r) => { setOutcomes([{ ...r, name: title }]); setText(""); setTitle(""); });
  const addPath = () => run("path", () => api<Outcome[]>(`/api/rag/kbs/${kb.id}/path`, { method: "POST", body: JSON.stringify({ path }) }), setOutcomes);

  async function removeDoc(d: Doc) {
    if (!confirm(`Remove “${d.source}” from this knowledge base?`)) return;
    try { await api(`/api/rag/kbs/${kb.id}/docs/${d.doc_id}`, { method: "DELETE" }); toast(`Removed ${d.source}`); loadDocs(); onChanged(); }
    catch (e) { toast(msg(e), "err"); }
  }
  async function search(ids: string[]) {
    setSearching(true); setSearchErr(""); setHits(null);
    try { setHits(await api<Hit[]>("/api/rag/search", { method: "POST", body: JSON.stringify({ query, kbs: ids, k: Number(k) }) })); }
    catch (e) { setSearchErr(msg(e)); }
    setSearching(false);
  }

  return (
    <div className="card kn-detail">
      <div className="card-head"><IBook /><b>{kb.label}</b><span className="pill">{kb.store_label}</span>{kb.managed && <span className="pill ok">managed</span>}<span className="grow" />
        <button className="ghost small" onClick={onClose}>Close</button></div>
      <p className="card-desc">{kb.description || "No description."}</p>
      <div className="small mut">{kb.provider} · {kb.model} · {kb.dim} dimensions · chunks of {kb.chunk_size} characters, {kb.chunk_overlap} overlap · {kbs.length} knowledge base{kbs.length === 1 ? "" : "s"} in total</div>

      <h4 className="kn-h">Documents</h4>
      {docErr && <div className="small err">{docErr}</div>}
      {docs === null && !docErr && <div className="small mut">Loading…</div>}
      {docs && docs.length === 0 && <div className="small mut">No documents yet. Add some below.</div>}
      {docs && docs.map((d) => (
        <div key={d.doc_id} className="kn-row">
          <span className="kn-grow"><b>{d.source}</b> <span className="mut small">{d.chunks} chunk{d.chunks === 1 ? "" : "s"} · {bytes(d.bytes)}</span></span>
          <button className="icon-btn" title="Remove document" aria-label={`Remove ${d.source}`} onClick={() => removeDoc(d)}><ITrash size={15} /></button>
        </div>))}

      <h4 className="kn-h">Add content</h4>
      <div className="small mut">Each document is embedded with {kb.provider}/{kb.model}: a paid call that counts against the spend cap. Unchanged files are skipped.</div>
      <div className={`kn-drop ${drag ? "on" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); files(e.dataTransfer.files); }}>
        <label className="kn-file">Upload files, or drop them here
          <input type="file" multiple aria-label="Upload files" disabled={!!busy} onChange={(e) => { files(e.target.files); e.target.value = ""; }} /></label>
        {busy === "upload" && <span className="small mut">Uploading and embedding…</span>}
      </div>
      <div className="kn-form">
        <input value={title} placeholder="Title for pasted text" aria-label="Title" onChange={(e) => setTitle(e.target.value)} />
        <textarea rows={3} value={text} placeholder="Paste text here" aria-label="Text" onChange={(e) => setText(e.target.value)} />
        <button className="acc small" disabled={!!busy || !title.trim() || !text.trim()} onClick={addText}>{busy === "text" ? "Adding…" : "Add text"}</button>
      </div>
      <div className="kn-form">
        <input value={path} placeholder="Workspace folder or file, e.g. docs" aria-label="Workspace path" onChange={(e) => setPath(e.target.value)} />
        <button className="acc small" disabled={!!busy || !path.trim()} onClick={addPath}>{busy === "path" ? "Ingesting…" : "Ingest from workspace"}</button>
      </div>
      {addErr && <div className="small err" style={{ marginTop: 8 }}>{addErr}</div>}
      {outcomes.length > 0 && <div className="kn-results" aria-label="Ingest results">
        {outcomes.map((o, i) => <div key={i} className="kn-row"><span className="kn-grow">{o.name}</span>
          <span className={`pill ${o.status === "skipped" ? "warn" : "ok"}`}>{o.status}</span>
          {o.status === "skipped" ? <span className="small err">{o.error}</span> : o.chunks !== undefined && <span className="small mut">{o.chunks} chunk{o.chunks === 1 ? "" : "s"}</span>}</div>)}</div>}

      <h4 className="kn-h">Test retrieval</h4>
      <div className="kn-form">
        <input value={query} placeholder="Ask a question the documents should answer" aria-label="Search query" onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && query.trim()) search([kb.id]); }} />
        <label className="kn-k">Results<input type="number" min={1} max={20} value={k} aria-label="Number of results" onChange={(e) => setK(e.target.value)} /></label>
        <button className="acc small" disabled={searching || !query.trim()} onClick={() => search([kb.id])}><ISearch size={14} />{searching ? "Searching…" : "Search"}</button>
      </div>
      <div className="small mut">One search embeds the query: a tiny paid call.</div>
      {searchErr && <div className="small err" style={{ marginTop: 6 }}>{searchErr}</div>}
      {hits && hits.length === 0 && <div className="small mut" style={{ marginTop: 6 }}>No matches.</div>}
      {hits && hits.map((h, i) => (
        <div key={i} className="kn-hit"><div className="row"><b>{h.source}</b><span className="pill">score {h.score}</span><span className="mut small">chunk {h.chunk}</span></div>
          <div className="small">{h.text}</div></div>))}
    </div>
  );
}

// ------------------------------------------------------------------ add / edit a store
function StoreForm({ kinds, store, onClose, onSaved }: { kinds: Kind[]; store: Store | null; onClose: () => void; onSaved: () => void }) {
  const [id, setId] = useState("");
  const [label, setLabel] = useState(store?.label ?? "");
  const [kindId, setKindId] = useState(store?.kind ?? kinds[0]?.id ?? "");
  const kind = kinds.find((k) => k.id === kindId);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const value = (f: Field) => vals[f.name] ?? (f.secret ? "" : store?.config[f.name] ?? f.default ?? "");

  async function save() {
    setBusy(true); setErr("");
    const config: Record<string, string> = {}, secrets: Record<string, string> = {};
    for (const f of kind!.fields) {   // Save is disabled while no kind is known, so kind is set here
      const v = value(f);
      if (f.secret) { if (clear[f.name]) secrets[f.name] = ""; else if (v) secrets[f.name] = v; }
      else if (v || store) config[f.name] = v;
    }
    try {
      if (store) await api(`/api/rag/stores/${store.id}`, { method: "PUT", body: JSON.stringify({ label, config, secrets }) });
      else await api("/api/rag/stores", { method: "POST", body: JSON.stringify({ id, label, kind: kindId, config, secrets }) });
      toast(`Saved ${label || id || store?.id}`);
      onSaved();
    } catch (e) { setErr(msg(e)); }
    setBusy(false);
  }

  return (
    <Modal title={store ? `Edit ${store.label}` : "Add a store"} onClose={onClose}>
      <div className="grid">
        {!store && <label>Id<input value={id} placeholder="my-qdrant" onChange={(e) => setId(e.target.value)} /></label>}
        <label>Name<input value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label>Kind
          <select value={kindId} disabled={!!store} onChange={(e) => { setKindId(e.target.value); setVals({}); }}>{kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label>
      </div>
      {kind && <p className="small mut">{kind.description}</p>}
      <div className="grid">
        {(kind?.fields ?? []).map((f) => (
          <div key={f.name} className="kn-field"><label>{f.label}{f.required ? " *" : ""}
            <input type={f.secret ? "password" : "text"} autoComplete="off" value={value(f)} placeholder={f.secret && store?.secrets_set[f.name] ? "set: leave blank to keep" : f.placeholder}
              onChange={(e) => setVals((s) => ({ ...s, [f.name]: e.target.value }))} />
            {(f.help || f.description) && <span className="small mut">{f.help || f.description}</span>}</label>
            {f.secret && store?.secrets_set[f.name] && <span className="small kn-check">
              <input type="checkbox" checked={!!clear[f.name]} aria-label={`Clear ${f.label}`} onChange={(e) => setClear((x) => ({ ...x, [f.name]: e.target.checked }))} />clear the stored value</span>}
          </div>))}
      </div>
      {err && <div className="small err" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
        <button className="ghost" onClick={onClose}>Cancel</button>
        <button className="acc" disabled={busy || (!store && !id) || !kind} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ stores
function Stores({ kinds, stores, onChanged }: { kinds: Kind[]; stores: Store[]; onChanged: () => void }) {
  const [form, setForm] = useState<{ store: Store | null } | null>(null);
  const [health, setHealth] = useState<Record<string, { ok: boolean; detail: string } | "busy">>({});

  async function test(s: Store) {
    setHealth((h) => ({ ...h, [s.id]: "busy" }));
    try { const r = await api<{ ok: boolean; detail: string }>(`/api/rag/stores/${s.id}/test`, { method: "POST" }); setHealth((h) => ({ ...h, [s.id]: r })); }
    catch (e) { setHealth((h) => ({ ...h, [s.id]: { ok: false, detail: msg(e) } })); }
  }
  async function remove(s: Store) {
    if (!confirm(`Delete the store “${s.label}”? Its settings are forgotten; vectors it holds stay where they are.`)) return;
    try { await api(`/api/rag/stores/${s.id}`, { method: "DELETE" }); toast(`Deleted ${s.label}`); onChanged(); }
    catch (e) { toast(msg(e), "err"); }
  }

  return (
    <div className="card">
      <div className="card-head"><IServer size={18} /><b>Stores</b><span className="grow" /><button className="acc small" onClick={() => setForm({ store: null })}><IPlus size={14} />Add store</button></div>
      <p className="card-desc">A store holds the vectors. One store can host many knowledge bases.</p>
      {stores.map((s) => {
        const h = health[s.id];
        const settings = Object.entries(s.config).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`);
        const secrets = Object.entries(s.secrets_set);
        return (
          <div key={s.id} className="kn-store">
            <div className="row"><b>{s.label}</b><span className="pill">{s.kind_label}</span>{s.builtin && <span className="pill">built-in</span>}<span className="mut small">{s.kbs} knowledge base{s.kbs === 1 ? "" : "s"}</span></div>
            {(settings.length > 0 || secrets.length > 0) && <div className="small mut kn-wrap">{settings.join(" · ")}{secrets.map(([k, on]) => ` · ${k}: ${on ? "set" : "not set"}`)}</div>}
            <div className="row">
              <button className="ghost small" disabled={h === "busy"} onClick={() => test(s)}><IPlay size={13} />{h === "busy" ? "Testing…" : "Test"}</button>
              <button className="ghost small" onClick={() => setForm({ store: s })}><IEdit size={13} />Edit</button>
              {!s.builtin && <button className="ghost small" onClick={() => remove(s)}><ITrash size={13} />Delete</button>}
              {h && h !== "busy" && <span className={`pill ${h.ok ? "ok" : "bad"}`}>{h.ok ? "healthy" : "unreachable"}</span>}
              {h && h !== "busy" && <span className="small mut kn-wrap">{h.detail}</span>}
            </div>
          </div>);
      })}
      {form && <StoreForm kinds={kinds} store={form.store} onClose={() => setForm(null)} onSaved={() => { setForm(null); onChanged(); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ Docker
function Docker({ stacks, err, onChanged, onReload }: { stacks: Stack[] | null; err: string; onChanged: () => void; onReload: () => void }) {
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [wipe, setWipe] = useState<Record<string, boolean>>({});
  const [errs, setErrs] = useState<Record<string, string>>({});

  async function act(s: Stack, what: string, path: string, method: string, done: string) {
    setBusy((b) => ({ ...b, [s.id]: what })); setErrs((x) => ({ ...x, [s.id]: "" }));
    try { await api(path, { method }); toast(done); }
    catch (e) { setErrs((x) => ({ ...x, [s.id]: msg(e) })); }
    setBusy((b) => ({ ...b, [s.id]: "" }));
    onReload(); if (what === "use") onChanged();
  }

  return (
    <div className="card">
      <div className="card-head"><IServer size={18} /><b>Docker</b><span className="grow" /><button className="ghost small" onClick={onReload}>Refresh</button></div>
      <p className="card-desc">Run a vector database on this PC with one click. Your data lives in a Docker volume and survives stop and remove.</p>
      {err && <div className="small err">{err}</div>}
      {stacks === null && !err && <div className="small mut">Checking Docker…</div>}
      {(stacks ?? []).map((s) => {
        const b = busy[s.id];
        return (
          <div key={s.id} className="kn-store">
            <div className="row"><b>{s.label}</b><span className={`pill ${s.state === "running" ? "ok" : s.state === "unavailable" ? "bad" : ""}`}>{s.state}</span><span className="small mut kn-wrap">{s.detail}</span></div>
            {s.notes && <div className="small mut">{s.notes}</div>}
            {s.state !== "unavailable" && <div className="row">
              {s.state !== "running" && <button className="acc small" disabled={!!b} onClick={() => act(s, "start", `/api/rag/stacks/${s.id}/start`, "POST", `${s.label} started`)}>
                {b === "start" ? "Starting…" : "Start"}</button>}
              {s.state === "running" && <button className="ghost small" disabled={!!b} onClick={() => act(s, "stop", `/api/rag/stacks/${s.id}/stop`, "POST", `${s.label} stopped`)}>{b === "stop" ? "Stopping…" : "Stop"}</button>}
              {s.state === "running" && <button className="acc small" disabled={!!b} onClick={() => act(s, "use", `/api/rag/stacks/${s.id}/register`, "POST", `${s.label} added as a store`)}>{b === "use" ? "Adding…" : "Use it"}</button>}
              {s.state !== "missing" && <>
                <button className="ghost small" disabled={!!b} onClick={() => act(s, "remove", `/api/rag/stacks/${s.id}${wipe[s.id] ? "?delete_data=true" : ""}`, "DELETE", `${s.label} removed`)}>{b === "remove" ? "Removing…" : "Remove"}</button>
                <label className="small kn-check"><input type="checkbox" checked={!!wipe[s.id]} onChange={(e) => setWipe((w) => ({ ...w, [s.id]: e.target.checked }))} />also delete its data</label></>}
            </div>}
            {s.state === "missing" && <div className="small mut">The first start downloads the image ({s.image}); that can take a few minutes.</div>}
            {errs[s.id] && <div className="small err">{errs[s.id]}</div>}
          </div>);
      })}
    </div>
  );
}

// ------------------------------------------------------------------ page
export default function KnowledgePage() {
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [kbs, setKbs] = useState<Kb[] | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [stacks, setStacks] = useState<Stack[] | null>(null);
  const [stackErr, setStackErr] = useState("");
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => Promise.all([api<Kind[]>("/api/rag/kinds"), api<Store[]>("/api/rag/stores"), api<Kb[]>("/api/rag/kbs"), api<Provider[]>("/api/providers")])
    .then(([k, s, b, p]) => { setKinds(k); setStores(s); setKbs(b); setProviders(p); setErr(""); })
    .catch((e) => setErr("Backend offline: " + msg(e))), []);
  const loadStacks = useCallback(() => api<Stack[]>("/api/rag/stacks").then((s) => { setStacks(s); setStackErr(""); }).catch((e) => { setStackErr(msg(e)); setStacks([]); }), []);
  useEffect(() => { load(); loadStacks(); }, [load, loadStacks]);

  async function remove(kb: Kb, force = false) {
    if (!force && !confirm(`Delete the knowledge base “${kb.label}” and its ${kb.docs} document${kb.docs === 1 ? "" : "s"}?`)) return;
    try {
      await api(`/api/rag/kbs/${kb.id}${force ? "?force=true" : ""}`, { method: "DELETE" });
      toast(`Deleted ${kb.label}`);
      if (sel === kb.id) setSel(null);
      load();
    } catch (e) {
      if (!force && e instanceof ApiError && e.status === 422 && /force/i.test(e.message) && confirm(`${e.message}\n\nDelete anyway? The vectors stay in the store.`)) return remove(kb, true);
      toast(msg(e), "err");
    }
  }

  const open = (kbs ?? []).find((k) => k.id === sel);
  return (
    <div>
      <div className="page-head"><h2>Knowledge</h2><InfoTip id="rag" /><span className="grow" />
        <button className="acc" onClick={() => setCreating(true)} disabled={!kbs} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IPlus size={15} />New knowledge base</button></div>
      <p className="page-sub">Give agents your documents. Files are split into chunks, embedded with the model you choose and kept in a vector store; agents search them with the <code>rag_search</code> tool when you attach a knowledge base in Agent Studio.</p>
      {err && <p className="err">{err}</p>}

      <div className="card" data-tour="knowledge-list">
        <div className="card-head"><IBook size={18} /><b>Knowledge bases</b></div>
        {kbs === null && !err && <div className="small mut">Loading…</div>}
        {kbs && kbs.length === 0 && <div className="empty" style={{ marginTop: 10 }}>No knowledge bases yet. Create one to start adding documents.</div>}
        {(kbs ?? []).map((kb) => (
          <div key={kb.id} className={`kn-row kn-kb ${sel === kb.id ? "on" : ""}`}>
            <div className="kn-grow"><b>{kb.label}</b> {kb.managed && <span className="pill ok">managed</span>}
              <div className="small mut kn-wrap">{kb.store_label} · {kb.provider}/{kb.model} · {kb.docs} doc{kb.docs === 1 ? "" : "s"} · {kb.chunks} chunk{kb.chunks === 1 ? "" : "s"}</div></div>
            <button className="ghost small" onClick={() => setSel(kb.id === sel ? null : kb.id)}>{kb.id === sel ? "Hide" : "Open"}</button>
            <button className="icon-btn" title="Delete knowledge base" aria-label={`Delete ${kb.label}`} onClick={() => remove(kb)}><ITrash size={15} /></button>
          </div>))}
      </div>

      {open && kbs && <KbDetail kb={open} kbs={kbs} onClose={() => setSel(null)} onChanged={load} />}

      <div data-tour="knowledge-stores">
        <Stores kinds={kinds} stores={stores} onChanged={load} />
        <Docker stacks={stacks} err={stackErr} onChanged={load} onReload={loadStacks} />
      </div>

      {creating && <CreateKb stores={stores} providers={providers} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setSel(id); load(); }} />}
    </div>
  );
}
