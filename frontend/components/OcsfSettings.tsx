"use client";
// Security export (OCSF): send Agent Studio's events to your security tools in the Open Cybersecurity Schema Framework format.
// Sinks (file, HTTPS / Splunk HEC, S3, syslog), redaction level, which kinds of events, a validated preview, and live counters.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import { NumField } from "./SettingsKit";

type Field = { name: string; label: string; secret: boolean; required: boolean; placeholder: string };
type Kind = { id: string; label: string; note: string; fields: Field[] };
type Stat = { sent?: number; failed?: number; dropped?: number; last_error?: string; last_ok?: number | null };
type Sink = { id: string; kind: string; enabled: boolean; level: string; config: Record<string, string>; secrets_set: string[]; stats: Stat; where: string };
type Cfg = { enabled: boolean; level: string; classes: Record<string, boolean>; sinks: Sink[]; batch_size: number; flush_seconds: number; levels: Record<string, string>;
  class_info: Record<string, string>; schema_version: string; kinds: Kind[] };
type Preview = { level: string; sample: boolean; schema_version: string; events: { event: any; problems: string[] }[] };
type Stats = { queued: number; dropped_queue_full: number; mapped: number; invalid: number; queue: number };

function SinkForm({ kinds, initial, onSave, onCancel }: { kinds: Kind[]; initial?: Sink; onSave: (s: { id: string; kind: string; enabled: boolean; level: string; config: Record<string, string> }, secrets: Record<string, string>) => void; onCancel: () => void }) {
  const [kind, setKind] = useState(initial?.kind ?? kinds[0]?.id ?? "file");
  const [id, setId] = useState(initial?.id ?? "");
  const [level, setLevel] = useState(initial?.level ?? "");
  const [conf, setConf] = useState<Record<string, string>>(initial?.config ?? {});
  const [sec, setSec] = useState<Record<string, string>>({});
  const k = kinds.find((x) => x.id === kind);
  return (
    <div className="card" data-testid="sink-form">
      <div className="grid">
        <label>Name<input value={id} disabled={!!initial} placeholder="e.g. siem" onChange={(e) => setId(e.target.value.toLowerCase())} /></label>
        <label>Kind<select value={kind} disabled={!!initial} onChange={(e) => { setKind(e.target.value); setConf({}); setSec({}); }}>{kinds.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
        <label>Detail level for this destination<select value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="">Same as above</option><option value="public">public</option><option value="internal">internal</option><option value="full">full</option></select></label>
      </div>
      {k && <div className="small mut" style={{ margin: "6px 0" }}>{k.note}</div>}
      <div className="grid">
        {k?.fields.map((f) => (
          <label key={f.name}>{f.label}{!f.required && " (optional)"}
            {f.secret
              ? <input type="password" autoComplete="off" value={sec[f.name] ?? ""} placeholder={initial?.secrets_set.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"} onChange={(e) => setSec({ ...sec, [f.name]: e.target.value })} />
              : <input value={conf[f.name] ?? ""} placeholder={f.placeholder} onChange={(e) => setConf({ ...conf, [f.name]: e.target.value })} />}
          </label>))}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!id.trim()} onClick={() => onSave({ id: id.trim(), kind, enabled: initial?.enabled ?? true, level,
          config: Object.fromEntries(Object.entries(conf).filter(([, v]) => v !== "").map(([a, v]) => [a, /^\d+$/.test(v) && ["max_mb", "keep", "port"].includes(a) ? (Number(v) as unknown as string) : v])) },
          Object.fromEntries(Object.entries(sec).filter(([, v]) => v)))}>{initial ? "Save destination" : "Add destination"}</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function OcsfSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<string | null>(null);      // sink id, or "" for a new one
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  const load = useCallback(() => Promise.all([api<Cfg>("/api/ocsf/config"), api<Stats>("/api/ocsf/stats")]).then(([c, s]) => { setCfg(c); setStats(s); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const save = async (patch: Record<string, unknown>, ok = "Saved") => {
    try { setCfg(await api<Cfg>("/api/ocsf/config", { method: "PUT", body: JSON.stringify(patch) })); setErr(""); toast(ok); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!cfg) return err ? <p className="err" role="alert">{err}</p> : null;

  const strip = ({ id, kind, enabled, level, config }: Sink) => ({ id, kind, enabled, level, config });
  const upsert = async (s: ReturnType<typeof strip>, secrets: Record<string, string>) => {
    const others = cfg.sinks.filter((x) => x.id !== s.id).map(strip);
    if (await save({ sinks: [...others, s], ...(Object.keys(secrets).length ? { secrets: { [s.id]: secrets } } : {}) }, "Destination saved")) setEditing(null);
  };
  const test = async (id: string) => { try { setTests({ ...tests, [id]: await api(`/api/ocsf/sinks/${id}/test`, { method: "POST" }) }); } catch (e) { setTests({ ...tests, [id]: { ok: false, detail: (e as Error).message } }); } };
  const runPreview = async () => { try { setPreview(await api<Preview>(`/api/ocsf/preview?limit=6`)); } catch (e) { setErr((e as Error).message); } };

  return (
    <div className="card" data-tour="ocsf">
      <div className="card-head"><b>Security export (OCSF)</b><InfoTip id="ocsf" />
        <span className={`pill ${cfg.enabled && cfg.sinks.some((s) => s.enabled) ? "ok" : ""}`}>{cfg.enabled ? "on" : "off"}</span></div>
      <p className="mut small">Sends a copy of important events to your security tools in OCSF {cfg.schema_version}, a common format they understand. Prompts and answers are never sent.
        The class, attribute and value names follow the released OCSF schema (including its AI profile). There is no AI event class in OCSF, so <b>tool use is reported as API Activity, following the OWASP Agent Observability Standard</b>,
        and the other choices (agent runs as Application Lifecycle, model calls as API Activity, blocks as Detection Findings) are Agent Studio&apos;s own mapping.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="row" style={{ padding: "8px 0" }}>
        <div className="grow"><b>Send security events</b><div className="small mut">Nothing is sent until you add a destination below.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Send security events" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} /><i /></label>
      </div>
      <div className="grid">
        <label>How much detail
          <select value={cfg.level} onChange={(e) => save({ level: e.target.value })}>{Object.keys(cfg.levels).map((l) => <option key={l} value={l}>{l}</option>)}</select>
          <span className="small mut">{cfg.levels[cfg.level]}</span></label>
        <NumField label="Send in batches of (events)" value={cfg.batch_size} min={1} max={1000} onSave={(n) => save({ batch_size: n })} />
        <NumField label="At least every (seconds)" value={cfg.flush_seconds} min={1} max={3600} onSave={(n) => save({ flush_seconds: n })} />
      </div>
      <div className="lbl" style={{ marginTop: 12 }}>Which events</div>
      {Object.entries(cfg.class_info).map(([k, label]) => (
        <label key={k} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={cfg.classes[k]} onChange={(e) => save({ classes: { [k]: e.target.checked } })} />{label}</label>))}

      <div className="lbl" style={{ marginTop: 14 }}>Destinations</div>
      {cfg.sinks.length === 0 && <div className="small mut">None yet. Start with a file on this PC to see what is sent.</div>}
      {cfg.sinks.map((s) => editing === s.id ? <SinkForm key={s.id} kinds={cfg.kinds} initial={s} onSave={upsert} onCancel={() => setEditing(null)} /> : (
        <div key={s.id} className="card sink-card" data-testid="sink">
          <div className="row" style={{ flexWrap: "wrap" }}>
            <b>{s.id}</b><span className="pill">{cfg.kinds.find((k) => k.id === s.kind)?.label ?? s.kind}</span><span className="pill">{s.level || cfg.level} detail</span><span className="grow" />
            <label className="switch"><input type="checkbox" aria-label={`${s.id} on`} checked={s.enabled} onChange={(e) => save({ sinks: cfg.sinks.map((x) => (x.id === s.id ? { ...strip(x), enabled: e.target.checked } : strip(x))) })} /><i /></label>
          </div>
          <div className="small mut">{s.where}</div>
          <div className="small" style={{ marginTop: 4 }}>sent {s.stats.sent ?? 0} · failed attempts {s.stats.failed ?? 0} · dropped {s.stats.dropped ?? 0}{s.stats.last_error ? ` · last problem: ${s.stats.last_error}` : ""}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="small" onClick={() => test(s.id)}>Send a test event</button>
            <button className="small" onClick={() => setEditing(s.id)}>Edit</button>
            <button className="ghost small" onClick={() => { if (confirm(`Remove “${s.id}”? Its stored credentials are deleted too.`)) save({ sinks: cfg.sinks.filter((x) => x.id !== s.id).map(strip) }, "Destination removed"); }}>Remove</button>
            {tests[s.id] && <span className={`pill ${tests[s.id].ok ? "ok" : "bad"}`} role="status">{tests[s.id].detail}</span>}
          </div>
        </div>))}
      {editing === "" ? <SinkForm kinds={cfg.kinds} onSave={upsert} onCancel={() => setEditing(null)} /> : <button style={{ marginTop: 8 }} onClick={() => setEditing("")}>Add a destination</button>}

      <div className="lbl" style={{ marginTop: 16 }}>Preview</div>
      <div className="row"><button onClick={runPreview}>Show example events</button>
        {stats && <span className="small mut">queued {stats.queue} · mapped {stats.mapped} · dropped when busy {stats.dropped_queue_full} · invalid {stats.invalid}</span>}</div>
      {preview && <div style={{ marginTop: 8 }}>
        <div className="small mut">{preview.sample ? "Nothing has happened yet, so these are examples" : "The latest events"} at “{preview.level}” detail, OCSF {preview.schema_version}.</div>
        <div className={`pill ${preview.events.every((e) => e.problems.length === 0) ? "ok" : "bad"}`} role="status">
          {preview.events.every((e) => e.problems.length === 0) ? `All ${preview.events.length} events are valid OCSF ${preview.schema_version}` : "Some events do not match the schema"}</div>
        {preview.events.map((e, i) => (
          <details key={i} className="small" style={{ marginTop: 4 }}><summary>{e.event.type_name}{e.event.message ? ` — ${e.event.message}` : ""}{e.problems.length ? ` ⚠ ${e.problems.length} problem${e.problems.length === 1 ? "" : "s"}` : ""}</summary>
            {e.problems.map((p) => <div key={p} className="err">{p}</div>)}
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(e.event, null, 2)}</pre></details>))}
      </div>}
    </div>
  );
}
