"use client";
// Guardrails: checks on what goes into a model, what comes out and what tools are asked to do. Profiles (a guard + where it looks +
// record-only or block), a master switch, a "try it" box, and the latest things they caught.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import KindFields, { type KField } from "./KindFields";

export type Profile = { id: string; name: string; kind: string; params: Record<string, any>; stages: Record<string, boolean>; mode: string; fail: string; default: boolean;
  secrets_set: string[]; leaves_pc: boolean; stored: boolean };
export type GKind = { id: string; label: string; summary: string; where: string; leaves_pc: boolean; cost: string; fields: KField[] };
type Cfg = { enabled: boolean; profiles: Profile[]; kinds: GKind[] };
type Ev = { ts: number; stage: string; agent: string; profile: string; action: string; category: string; reason: string };
type Stats = { days: number; flagged: number; blocked: number; errors: number; top_categories: { category: string; count: number }[] };
type TestResult = { ok: boolean; error?: string; flagged?: boolean; category?: string; reason?: string; would?: string; ms?: number };

const MODES: [string, string][] = [["off", "Off"], ["monitor", "Record only"], ["block", "Block"]];
const STAGES: [string, string][] = [["input", "Messages and tool results going in"], ["output", "Answers coming out"], ["tool", "Tool calls"]];
const blank = (kind: string): Draft => ({ id: "", name: "", kind, params: {}, stages: { input: true, output: true, tool: true }, mode: "monitor", fail: "open", default: false, secrets: {}, secretsSet: [], isNew: true });
type Draft = { id: string; name: string; kind: string; params: Record<string, any>; stages: Record<string, boolean>; mode: string; fail: string; default: boolean; secrets: Record<string, string>; secretsSet: string[]; isNew: boolean };

function TryIt({ body }: { body: () => Record<string, unknown> }) {
  const [text, setText] = useState("");
  const [stage, setStage] = useState("input");
  const [res, setRes] = useState<TestResult | null>(null);
  const run = async () => {
    try { setRes(await api<TestResult>("/api/guardrails/test", { method: "POST", body: JSON.stringify({ profile: body(), text, stage }) })); }
    catch (e) { setRes({ ok: false, error: (e as Error).message }); }
  };
  return (
    <div className="cat-panel" data-testid="try-it">
      <div className="lbl">Try it on some text</div>
      <textarea rows={2} value={text} aria-label="Text to check" placeholder="Paste something a user or a tool might send" onChange={(e) => { setText(e.target.value); setRes(null); }} />
      <div className="row" style={{ marginTop: 6 }}>
        <select aria-label="Where to check it" value={stage} onChange={(e) => setStage(e.target.value)}>{STAGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <button disabled={!text.trim()} onClick={run}>Check it</button>
        {res && (res.ok
          ? <span className={`pill ${res.flagged ? "warn" : "ok"}`} role="status">{res.flagged ? `Flagged (${res.category}): ${res.reason} - would ${res.would}` : "Nothing found - it would be let through"} · {res.ms} ms</span>
          : <span className="pill bad" role="status">{res.error}</span>)}
      </div>
    </div>
  );
}

function ProfileForm({ kinds, initial, onSave, onCancel }: { kinds: GKind[]; initial: Draft; onSave: (d: Draft) => void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>(initial);
  const [ack, setAck] = useState(false);
  const kind = kinds.find((k) => k.id === d.kind)!;
  const body = () => ({ id: d.id, name: d.name, kind: d.kind, params: d.params, mode: d.mode, fail: d.fail, secrets: d.secrets });
  return (
    <div className="card" data-testid="profile-form">
      <div className="grid">
        <label>Name<input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value, id: d.isNew ? e.target.value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[^a-z]+/, "").slice(0, 30) : d.id })} /></label>
        <label>Id<input value={d.id} disabled={!d.isNew} onChange={(e) => setD({ ...d, id: e.target.value })} /></label>
        <label>Kind of check<select value={d.kind} disabled={!d.isNew} onChange={(e) => setD({ ...d, kind: e.target.value, params: {}, secrets: {} })}>{kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label>
      </div>
      <p className="small mut" style={{ margin: "8px 0" }}>{kind.summary} <b>{kind.where}.</b></p>
      {kind.leaves_pc && <div className="warn-box" role="note">Text checked by this guard is sent to a cloud service. Leave it out of profiles for anything you must keep on this PC.</div>}
      <KindFields fields={kind.fields} params={d.params} onParams={(p) => setD({ ...d, params: p })} secrets={d.secrets} onSecrets={(s) => setD({ ...d, secrets: s })} secretsSet={d.secretsSet} />
      <div className="lbl" style={{ marginTop: 12 }}>Where it looks</div>
      {STAGES.map(([k, l]) => (
        <label key={k} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={d.stages[k]} onChange={(e) => setD({ ...d, stages: { ...d.stages, [k]: e.target.checked } })} />{l}</label>))}
      <div className="grid" style={{ marginTop: 10 }}>
        <label>When it finds something
          <select value={d.mode} onChange={(e) => setD({ ...d, mode: e.target.value })}>{MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select><InfoTip id="guardrail-mode" /></label>
        <label>If the check cannot answer
          <select value={d.fail} onChange={(e) => setD({ ...d, fail: e.target.value })}><option value="open">Carry on (and record it)</option><option value="closed">Stop (treat as a block)</option></select><InfoTip id="guardrail-fail" /></label>
      </div>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 }}>
        <input type="checkbox" checked={d.default} onChange={(e) => setD({ ...d, default: e.target.checked })} />Use for every agent that has not chosen its own</label>
      {kind.leaves_pc && <label style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 }}>
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />I understand the checked text leaves this PC</label>}
      <TryIt body={body} />
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!d.name.trim() || !d.id.trim() || (kind.leaves_pc && d.isNew && !ack)} onClick={() => onSave(d)}>{d.isNew ? "Add profile" : "Save profile"}</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function GuardrailSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<Draft | null>(null);
  const [testing, setTesting] = useState("");

  const load = useCallback(() => Promise.all([api<Cfg>("/api/guardrails"), api<{ events: Ev[] }>("/api/guardrails/events?limit=6"), api<Stats>("/api/guardrails/stats")])
    .then(([c, e, s]) => { setCfg(c); setEvents(e.events); setStats(s); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const put = async (path: string, init: RequestInit, ok: string) => {
    try { setCfg(await api<Cfg>(path, init)); setErr(""); toast(ok); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!cfg) return err ? <p className="err" role="alert">{err}</p> : null;

  const save = async (d: Draft) => {
    const secrets = Object.fromEntries(Object.entries(d.secrets).filter(([, v]) => v));
    if (await put("/api/guardrails/profiles", { method: "PUT", body: JSON.stringify({ id: d.id, name: d.name, kind: d.kind, params: d.params, stages: d.stages, mode: d.mode, fail: d.fail, default: d.default, secrets }) }, "Profile saved")) {
      setEditing(null); load();
    }
  };
  const edit = (p: Profile) => setEditing({ id: p.id, name: p.name, kind: p.kind, params: p.params, stages: p.stages, mode: p.mode, fail: p.fail, default: p.default, secrets: {}, secretsSet: p.secrets_set, isNew: false });
  const kindLabel = (k: string) => cfg.kinds.find((x) => x.id === k)?.label ?? k;

  return (
    <div className="card" data-tour="guardrails">
      <div className="card-head"><b>Guardrails</b><InfoTip id="guardrails" /><span className={`pill ${cfg.enabled ? "ok" : ""}`}>{cfg.enabled ? "on" : "off"}</span></div>
      <p className="mut small">Safety checks on what goes into a model (including what tools return), what comes out, and what tools are asked to do. Start in <b>record only</b> to see what they would catch, then switch a profile to <b>block</b>.
        Text streamed to the screen before the end of an answer cannot be taken back, so an answer that fails the output check is replaced in the saved chat and flagged.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="row" style={{ padding: "8px 0" }}>
        <div className="grow"><b>Check inputs, answers and tool calls</b><div className="small mut">Off by default. Nothing is checked until you turn this on.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Guardrails on" checked={cfg.enabled} onChange={(e) => put("/api/guardrails/enabled", { method: "PUT", body: JSON.stringify({ enabled: e.target.checked }) }, e.target.checked ? "Guardrails are on" : "Guardrails are off")} /><i /></label>
      </div>
      {stats && <div className="small mut" style={{ marginBottom: 8 }}>Last {stats.days} days: {stats.flagged} flagged · {stats.blocked} blocked · {stats.errors} checks that could not answer{stats.top_categories[0] ? ` · most common: ${stats.top_categories[0].category}` : ""}</div>}

      <div className="lbl">Profiles</div>
      {cfg.profiles.map((p) => editing && !editing.isNew && editing.id === p.id ? <ProfileForm key={p.id} kinds={cfg.kinds} initial={editing} onSave={save} onCancel={() => setEditing(null)} /> : (
        <div key={p.id} className="card" data-testid="profile">
          <div className="row" style={{ flexWrap: "wrap" }}>
            <b>{p.name}</b><span className="pill">{kindLabel(p.kind)}</span>
            <span className={`pill ${p.mode === "block" ? "bad" : p.mode === "monitor" ? "warn" : ""}`}>{MODES.find(([k]) => k === p.mode)?.[1]}</span>
            {p.default && <span className="pill acc">default</span>}{p.leaves_pc && <span className="pill warn">sends text to the cloud</span>}
          </div>
          <div className="small mut">Looks at: {STAGES.filter(([k]) => p.stages[k]).map(([, l]) => l.toLowerCase()).join(", ")} · if it cannot answer: {p.fail === "open" ? "carries on" : "stops"}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="small" onClick={() => setTesting(testing === p.id ? "" : p.id)}>Try it</button>
            <button className="small" onClick={() => edit(p)}>Edit</button>
            {p.stored && <button className="ghost small" onClick={() => { if (confirm(`Remove “${p.name}”? Its stored credentials are deleted too.`)) put(`/api/guardrails/profiles/${p.id}`, { method: "DELETE" }, "Profile removed"); }}>Remove</button>}
          </div>
          {testing === p.id && <TryIt body={() => ({ id: p.id })} />}
        </div>))}
      {editing?.isNew ? <ProfileForm kinds={cfg.kinds} initial={editing} onSave={save} onCancel={() => setEditing(null)} /> : <button style={{ marginTop: 8 }} onClick={() => setEditing(blank(cfg.kinds[0].id))}>Add a profile</button>}

      <div className="lbl" style={{ marginTop: 16 }}>Latest catches</div>
      {events.length === 0 && <div className="small mut">Nothing yet.</div>}
      {events.map((e, i) => <div key={i} className="small" style={{ padding: "3px 0" }}><span className="mut">{new Date(e.ts * 1000).toLocaleString()}</span> · <span className={`pill ${e.action === "blocked" ? "bad" : "warn"}`}>{e.action}</span> {e.stage} · {e.profile}{e.agent ? ` · ${e.agent}` : ""} · {e.reason}</div>)}
    </div>
  );
}
