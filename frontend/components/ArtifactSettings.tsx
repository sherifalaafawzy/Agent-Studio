"use client";
// Artifacts: whether the files agents produce are kept, which ones, WHERE copies go (this PC, S3, Google Cloud, Azure, WebDAV...), which kinds of file
// go where, the secret scan that runs before a copy leaves this PC, and how long files are kept. Credentials are write-only.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { TYPE_LABEL } from "@/lib/artifacts";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import { NumField } from "./SettingsKit";

type Field = { name: string; label: string; secret: boolean; required: boolean; placeholder: string };
type Kind = { id: string; label: string; note: string; cloud: boolean; fields: Field[] };
type Storage = { id: string; kind: string; label: string; enabled: boolean; config: Record<string, string>; secrets_set: string[]; where: string; cloud: boolean };
type Rule = { types: string[]; max_mb: number; storages: string[] };
type Cfg = { enabled: boolean; capture: string; max_file_mb: number; max_per_turn: number; secret_scan: string; retention_days: number; ignore: string[]; storages: Storage[]; policy: Rule[];
  default_storages: string[]; capture_modes: Record<string, string>; scan_modes: Record<string, string>; types: string[]; default_ignore: string[]; kinds: Kind[] };

const strip = ({ id, kind, label, enabled, config }: Storage) => ({ id, kind, label, enabled, config });
const toggle = (l: string[], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v]);

function StorageForm({ kinds, initial, onSave, onCancel }: { kinds: Kind[]; initial?: Storage; onSave: (s: ReturnType<typeof strip>, secrets: Record<string, string>) => void; onCancel: () => void }) {
  const [kind, setKind] = useState(initial?.kind ?? kinds.find((k) => k.id !== "local")?.id ?? "local");
  const [id, setId] = useState(initial?.id ?? "");
  const [label, setLabel] = useState(initial?.label ?? "");
  const [conf, setConf] = useState<Record<string, string>>(initial?.config ?? {});
  const [sec, setSec] = useState<Record<string, string>>({});
  const k = kinds.find((x) => x.id === kind);
  return (
    <div className="card" data-testid="storage-form">
      <div className="grid">
        <label>Name<input value={id} disabled={!!initial} placeholder="e.g. team-bucket" onChange={(e) => setId(e.target.value.toLowerCase())} /></label>
        <label>Kind<select value={kind} disabled={!!initial} onChange={(e) => { setKind(e.target.value); setConf({}); setSec({}); }}>{kinds.filter((x) => x.id !== "local" || initial?.id === "local").map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
        <label>Label (optional)<input value={label} onChange={(e) => setLabel(e.target.value)} /></label>
      </div>
      {k && <div className="small mut" style={{ margin: "6px 0" }}>{k.note}{k.cloud && " Files you send here leave this PC."}</div>}
      <div className="grid">
        {k?.fields.map((f) => (
          <label key={f.name}>{f.label}{!f.required && " (optional)"}
            {f.secret
              ? <input type="password" autoComplete="off" value={sec[f.name] ?? ""} placeholder={initial?.secrets_set.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"} onChange={(e) => setSec({ ...sec, [f.name]: e.target.value })} />
              : <input value={conf[f.name] ?? ""} placeholder={f.placeholder} onChange={(e) => setConf({ ...conf, [f.name]: e.target.value })} />}
          </label>))}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!id.trim()} onClick={() => onSave({ id: id.trim(), kind, label, enabled: initial?.enabled ?? true, config: Object.fromEntries(Object.entries(conf).filter(([, v]) => v !== "")) },
          Object.fromEntries(Object.entries(sec).filter(([, v]) => v)))}>{initial ? "Save storage" : "Add storage"}</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function ArtifactSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [ignore, setIgnore] = useState("");
  const load = useCallback(() => api<Cfg>("/api/artifacts/config").then((c) => { setCfg(c); setIgnore(c.ignore.join("\n")); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const save = async (patch: Record<string, unknown>, ok = "Saved") => {
    try { const c = await api<Cfg>("/api/artifacts/config", { method: "PUT", body: JSON.stringify(patch) }); setCfg(c); setIgnore(c.ignore.join("\n")); setErr(""); toast(ok); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!cfg) return err ? <p className="err" role="alert">{err}</p> : null;

  const upsert = async (s: ReturnType<typeof strip>, secrets: Record<string, string>) => {
    const others = cfg.storages.filter((x) => x.id !== s.id).map(strip);
    if (await save({ storages: [...others, s], ...(Object.keys(secrets).length ? { secrets: { [s.id]: secrets } } : {}) }, "Storage saved")) setEditing(null);
  };
  const test = async (id: string) => { try { setTests({ ...tests, [id]: await api(`/api/artifacts/storages/${id}/test`, { method: "POST" }) }); } catch (e) { setTests({ ...tests, [id]: { ok: false, detail: (e as Error).message } }); } };
  const setRule = (i: number, r: Rule) => save({ policy: cfg.policy.map((x, j) => (j === i ? r : x)) });
  const names = cfg.storages.map((s) => s.id);

  return (
    <div className="card" data-tour="artifacts-settings">
      <div className="card-head"><b>Artifacts</b><InfoTip id="artifacts" /><span className={`pill ${cfg.enabled && cfg.capture !== "off" ? "ok" : ""}`}>{cfg.enabled ? "on" : "off"}</span></div>
      <p className="mut small">Files and results your agents produce are kept so you can find, preview and download them later. A copy always stays on this PC unless a rule below sends a kind of file somewhere else.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="row" style={{ padding: "8px 0" }}>
        <div className="grow"><b>Keep what agents produce</b><div className="small mut">Off = agents can no longer save artifacts and nothing is collected.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Keep what agents produce" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} /><i /></label>
      </div>
      <div className="grid">
        <label>Collect files automatically
          <select value={cfg.capture} onChange={(e) => save({ capture: e.target.value })}>{Object.entries(cfg.capture_modes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <NumField label="Largest file (MB)" value={cfg.max_file_mb} min={1} max={500} onSave={(n) => save({ max_file_mb: n })} />
        <NumField label="Files collected per turn" value={cfg.max_per_turn} min={1} max={500} onSave={(n) => save({ max_per_turn: n })} />
        <NumField label="Delete after (days, 0 = keep)" value={cfg.retention_days} min={0} max={36500} onSave={(n) => save({ retention_days: n })} />
        <label>Secret scan before a copy leaves this PC
          <select value={cfg.secret_scan} onChange={(e) => save({ secret_scan: e.target.value })}>{Object.entries(cfg.scan_modes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select><InfoTip id="artifact-scan" /></label>
      </div>
      <label style={{ marginTop: 10 }}>Never collect these (one pattern per line)
        <textarea rows={4} value={ignore} onChange={(e) => setIgnore(e.target.value)} onBlur={() => { const l = ignore.split("\n").map((x) => x.trim()).filter(Boolean); if (JSON.stringify(l) !== JSON.stringify(cfg.ignore)) void save({ ignore: l }); }} /></label>

      <div className="lbl" style={{ marginTop: 14 }}>Where copies are kept <InfoTip id="artifact-storage" /></div>
      {cfg.storages.map((s) => editing === s.id ? <StorageForm key={s.id} kinds={cfg.kinds} initial={s} onSave={upsert} onCancel={() => setEditing(null)} /> : (
        <div key={s.id} className="card sink-card" data-testid="storage">
          <div className="row" style={{ flexWrap: "wrap" }}>
            <b>{s.label || s.id}</b><span className="pill">{cfg.kinds.find((k) => k.id === s.kind)?.label ?? s.kind}</span>{s.cloud && <span className="pill warn">leaves this PC</span>}<span className="grow" />
            {s.id !== "local" && <label className="switch"><input type="checkbox" aria-label={`${s.id} on`} checked={s.enabled} onChange={(e) => save({ storages: cfg.storages.map((x) => (x.id === s.id ? { ...strip(x), enabled: e.target.checked } : strip(x))) })} /><i /></label>}
          </div>
          <div className="small mut">{s.where}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="small" onClick={() => test(s.id)}>Test</button>
            <button className="small" onClick={() => setEditing(s.id)}>Edit</button>
            {s.id !== "local" && <button className="ghost small" onClick={() => { if (confirm(`Remove “${s.id}”? Its stored credentials are deleted. Files already copied there stay there.`)) save({ storages: cfg.storages.filter((x) => x.id !== s.id).map(strip) }, "Storage removed"); }}>Remove</button>}
            {tests[s.id] && <span className={`pill ${tests[s.id].ok ? "ok" : "bad"}`} role="status">{tests[s.id].detail}</span>}
          </div>
        </div>))}
      {editing === "" ? <StorageForm kinds={cfg.kinds} onSave={upsert} onCancel={() => setEditing(null)} /> : <button style={{ marginTop: 8 }} onClick={() => setEditing("")}>Add a storage</button>}

      <div className="lbl" style={{ marginTop: 16 }}>Which files go where</div>
      <div className="small mut">The first rule that matches a file decides. Files that match no rule go to the default storages.</div>
      {cfg.policy.map((r, i) => (
        <div key={i} className="card" data-testid="rule" style={{ marginTop: 8 }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}><span className="small mut">Kinds:</span>
            {cfg.types.map((t) => <label key={t} className="chip small" style={{ flexDirection: "row", gap: 4 }}><input type="checkbox" checked={r.types.includes(t)} disabled={r.types.length === 1 && r.types.includes(t)} onChange={() => setRule(i, { ...r, types: toggle(r.types, t) })} />{TYPE_LABEL[t] ?? t}</label>)}</div>
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 6 }}><span className="small mut">Send to:</span>
            {names.map((n) => <label key={n} className="chip small" style={{ flexDirection: "row", gap: 4 }}><input type="checkbox" checked={r.storages.includes(n)} disabled={r.storages.length === 1 && r.storages.includes(n)} onChange={() => setRule(i, { ...r, storages: toggle(r.storages, n) })} />{n}</label>)}</div>
          <div className="row" style={{ marginTop: 6 }}><NumField label="Only files up to (MB, 0 = any size)" value={r.max_mb} min={0} max={500} onSave={(n) => setRule(i, { ...r, max_mb: n })} />
            <span className="grow" /><button className="ghost small" onClick={() => save({ policy: cfg.policy.filter((_, j) => j !== i) }, "Rule removed")}>Remove rule</button></div>
        </div>))}
      <button style={{ marginTop: 8 }} onClick={() => save({ policy: [...cfg.policy, { types: ["document"], max_mb: 0, storages: ["local"] }] }, "Rule added")}>Add a rule</button>
      <div className="lbl" style={{ marginTop: 14 }}>Everything else goes to</div>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>{names.map((n) => <label key={n} className="chip small" style={{ flexDirection: "row", gap: 4 }}>
        <input type="checkbox" checked={cfg.default_storages.includes(n)} disabled={cfg.default_storages.length === 1 && cfg.default_storages.includes(n)} onChange={() => save({ default_storages: toggle(cfg.default_storages, n) })} />{n}</label>)}</div>
    </div>
  );
}
