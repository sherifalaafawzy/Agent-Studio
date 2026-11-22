"use client";
// Memory settings: kinds of memory and how long they last, where memories are kept (this PC, a vector database, a cloud service),
// how new memories are found, reviewed and cleaned up. Each control saves as soon as you change it; backend details have a Save button.
import { useCallback, useEffect, useState } from "react";
import { api, Provider } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import { NumField } from "./SettingsKit";
import { useDirty } from "@/lib/dirty";

type KindField = { name: string; label: string; secret: boolean; required: boolean; help: string };
type Kind = { id: string; label: string; description: string; cloud: boolean; notes: string; verified: string; fields: KindField[] };
type TypeCfg = { enabled: boolean; ttl_days: number; max_items: number };
export type MemConfig = {
  enabled: boolean; backend: string; backend_config: Record<string, string>; embedding: { provider: string; model: string }; review: boolean; redact_pii: boolean;
  types: Record<string, TypeCfg>; auto_extract: string; extract_model: { provider: string; model: string }; recall_budget_chars: number; recall_k: number; recall_min_score: number;
  consolidation: { dedupe: boolean; merge_contradictions: boolean; similarity: number; interval_hours: number }; notes_file: string; notes_max_chars: number;
  secrets_set: string[]; backend_cloud: boolean; backend_label: string; type_info: Record<string, { label: string; description: string }>;
};

const KIND_ORDER = ["semantic", "episodic", "entity", "procedural"];

function Switch({ checked, onChange, label, hint, info }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; info?: string }) {
  return (
    <div className="row" style={{ alignItems: "flex-start", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
      <div className="grow"><div style={{ fontWeight: 500 }}>{label}{info && <> <InfoTip id={info} /></>}</div>{hint && <div className="small mut" style={{ marginTop: 2 }}>{hint}</div>}</div>
      <label className="switch"><input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} /><i /></label>
    </div>
  );
}

export default function MemorySettings() {
  const [cfg, setCfg] = useState<MemConfig | null>(null);
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [bc, setBc] = useState<Record<string, string>>({});
  const [sec, setSec] = useState<Record<string, string>>({});
  const [health, setHealth] = useState<{ ok: boolean; detail: string } | null>(null);
  const [backend, setBackend] = useState("local");

  const load = useCallback(() => Promise.all([api<MemConfig>("/api/memories/config"), api<Kind[]>("/api/memories/kinds"), api<Provider[]>("/api/providers")])
    .then(([c, k, p]) => { setCfg(c); setKinds(k); setProviders(p); setBc(c.backend_config); setBackend(c.backend); setSec({}); setErr(""); })
    .catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const prov = cfg?.embedding.provider ?? "";
  useEffect(() => {
    if (!prov) { setModels([]); return; }
    api<{ models: { model: string }[] }>(`/api/rag/embedding-models/${prov}`).then((r) => setModels(r.models.map((m) => m.model))).catch(() => setModels([]));
  }, [prov]);

  const kind = kinds.find((k) => k.id === backend);
  const backendDirty = !!cfg && (backend !== cfg.backend || JSON.stringify(bc) !== JSON.stringify(cfg.backend_config) || Object.values(sec).some(Boolean));
  useDirty("memory-backend", backendDirty);

  const save = async (patch: Record<string, unknown>, ok = "Saved") => {
    try { setCfg(await api<MemConfig>("/api/memories/config", { method: "PUT", body: JSON.stringify(patch) })); setErr(""); toast(ok); return true; }
    catch (e) { toast((e as Error).message, "err"); setErr((e as Error).message); return false; }
  };
  const saveBackend = async () => {
    const secrets = Object.fromEntries(Object.entries(sec).filter(([, v]) => v));
    if (await save({ backend, backend_config: bc, ...(Object.keys(secrets).length ? { secrets } : {}) }, "Memory backend saved")) { setSec({}); setHealth(null); }
  };
  const test = async () => { try { setHealth(await api<{ ok: boolean; detail: string }>("/api/memories/health")); } catch (e) { setHealth({ ok: false, detail: (e as Error).message }); } };

  if (!cfg) return err ? <p className="err" role="alert">{err}</p> : null;
  const patchType = (t: string, p: Partial<TypeCfg>) => save({ types: { [t]: p } });
  const patchCons = (p: Record<string, unknown>) => save({ consolidation: p });

  return (
    <div>
      <p className="page-sub">Choose what agents may remember, how long, and where. Memories stay on this PC unless you pick a cloud service below.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="card">
        <Switch checked={cfg.enabled} onChange={(v) => save({ enabled: v })} label="Memory is on" hint="Master switch. Off = no agent saves or recalls anything." info="memory" />
        <Switch checked={cfg.redact_pii} onChange={(v) => save({ redact_pii: v })} label="Remove personal data before saving"
          hint="Emails, phone numbers, card numbers, ID numbers and keys are replaced with a note before anything is stored." />
        <Switch checked={cfg.review} onChange={(v) => save({ review: v })} label="Always ask me before a new memory is used" info="memory-review"
          hint="Even agents allowed to save straight away must wait for your approval in the review queue." />
      </div>

      <div className="card">
        <div className="card-head"><b>Kinds of memory</b><InfoTip id="memory-types" /></div>
        {KIND_ORDER.map((t) => (
          <div key={t} className="row" style={{ alignItems: "flex-end", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--line)", flexWrap: "wrap" }} data-setting={`types-${t}`}>
            <div className="grow" style={{ minWidth: 200 }}><div style={{ fontWeight: 500 }}>{cfg.type_info[t]?.label ?? t}</div><div className="small mut">{cfg.type_info[t]?.description}</div></div>
            <label className="switch"><input type="checkbox" aria-label={`${cfg.type_info[t]?.label ?? t} on`} checked={cfg.types[t].enabled} onChange={(e) => patchType(t, { enabled: e.target.checked })} /><i /></label>
            <NumField label="Keep (days, 0 = forever)" value={cfg.types[t].ttl_days} min={0} max={3650} onSave={(n) => patchType(t, { ttl_days: n })} info="memory-retention" />
            <NumField label="Most to keep" value={cfg.types[t].max_items} min={10} max={100000} onSave={(n) => patchType(t, { max_items: n })} />
          </div>))}
      </div>

      <div className="card">
        <div className="card-head"><b>Where memories are kept</b><InfoTip id="memory-backend" /></div>
        <label>Backend
          <select value={backend} onChange={(e) => { setBackend(e.target.value); setBc({}); setSec({}); }}>{kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label>
        {kind && <div className="small mut" style={{ margin: "6px 0" }}>{kind.description}{kind.notes ? ` ${kind.notes}` : ""}</div>}
        {kind?.cloud && <div className="small warn-box" role="note">⚠ Memory text is sent to this cloud service. Only approved memories are sent, after personal data is removed.</div>}
        {kind?.fields.map((f) => (
          <label key={f.name} style={{ marginTop: 8 }}>{f.label}{!f.required && " (optional)"}
            {f.secret
              ? <input type="password" autoComplete="off" value={sec[f.name] ?? ""} placeholder={cfg.secrets_set.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"} onChange={(e) => setSec({ ...sec, [f.name]: e.target.value })} />
              : <input value={bc[f.name] ?? ""} onChange={(e) => setBc({ ...bc, [f.name]: e.target.value })} />}
            {f.help && <span className="small mut">{f.help}</span>}</label>))}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="pri" disabled={!backendDirty} onClick={saveBackend}>Save backend</button>
          <button onClick={test}>Test connection</button>
          {health && <span className={`pill ${health.ok ? "ok" : "bad"}`} role="status">{health.detail}</span>}
        </div>
        {kind?.verified && <div className="small mut" style={{ marginTop: 6 }}>API checked against: {kind.verified}</div>}
      </div>

      <div className="card">
        <div className="card-head"><b>Finding memories</b><InfoTip id="embeddings" /></div>
        <div className="small mut" style={{ marginBottom: 8 }}>An embedding model lets agents find memories by meaning. Without one, memories are found by matching words. Its calls count against your spend caps.</div>
        <div className="grid">
          <label>Embedding provider
            <select value={cfg.embedding.provider} onChange={(e) => save({ embedding: { provider: e.target.value, model: "" } })}>
              <option value="">None (match words)</option>{providers.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          {cfg.embedding.provider && <label>Embedding model
            <input list="mem-models" defaultValue={cfg.embedding.model} key={cfg.embedding.provider + cfg.embedding.model} placeholder="choose or type"
              onBlur={(e) => e.target.value !== cfg.embedding.model && save({ embedding: { provider: cfg.embedding.provider, model: e.target.value.trim() } })} />
            <datalist id="mem-models">{models.map((m) => <option key={m} value={m} />)}</datalist></label>}
          <NumField label="Memories to bring up" value={cfg.recall_k} min={1} max={20} onSave={(n) => save({ recall_k: n })} />
          <NumField label="Most characters added to a prompt" value={cfg.recall_budget_chars} min={200} max={8000} onSave={(n) => save({ recall_budget_chars: n })} />
          <NumField label="Weakest match to use (0 to 1)" value={cfg.recall_min_score} min={0} max={1} integer={false} onSave={(n) => save({ recall_min_score: n })} />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><b>Learning from chats</b></div>
        <label>How new memories are picked up
          <select value={cfg.auto_extract} onChange={(e) => save({ auto_extract: e.target.value })}>
            <option value="off">Only when I say “remember that…” or the agent saves one</option>
            <option value="rules">Also from simple phrases (“my timezone is…”): free</option>
            <option value="model">Also let a model suggest memories: costs one small model call per turn</option></select></label>
        {cfg.auto_extract === "model" && <div className="grid" style={{ marginTop: 8 }}>
          <label>Model provider
            <select value={cfg.extract_model.provider} onChange={(e) => save({ extract_model: { provider: e.target.value, model: "" } })}>
              <option value="">The chat's own provider</option>{providers.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          <label>Model<input defaultValue={cfg.extract_model.model} key={cfg.extract_model.provider + cfg.extract_model.model} placeholder="provider default"
            onBlur={(e) => e.target.value !== cfg.extract_model.model && save({ extract_model: { provider: cfg.extract_model.provider, model: e.target.value.trim() } })} /></label>
        </div>}
        <Switch checked={cfg.consolidation.dedupe} onChange={(v) => patchCons({ dedupe: v })} label="Merge memories that say the same thing" />
        <Switch checked={cfg.consolidation.merge_contradictions} onChange={(v) => patchCons({ merge_contradictions: v })} label="A newer fact replaces an older one with the same key"
          hint="For example a new timezone replaces the old one. The old one is kept out of searches." />
        <div className="grid" style={{ marginTop: 8 }}>
          <NumField label="Treat as the same when this similar (0.5 to 1)" value={cfg.consolidation.similarity} min={0.5} max={1} integer={false} onSave={(n) => patchCons({ similarity: n })} />
          <NumField label="Tidy up every (hours)" value={cfg.consolidation.interval_hours} min={1} max={720} onSave={(n) => patchCons({ interval_hours: n })} />
        </div>
      </div>

      <div className="card">
        <div className="card-head"><b>Project notes</b></div>
        <div className="grid">
          <label>Notes file name<input defaultValue={cfg.notes_file} key={cfg.notes_file} onBlur={(e) => e.target.value !== cfg.notes_file && save({ notes_file: e.target.value.trim() })} />
            <span className="small mut">Lives in each workspace folder. Agents read it at the start of a chat.</span></label>
          <NumField label="Most characters read per chat" value={cfg.notes_max_chars} min={500} max={20000} onSave={(n) => save({ notes_max_chars: n })} />
        </div>
      </div>
    </div>
  );
}
