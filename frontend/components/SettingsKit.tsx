"use client";
// Small building blocks for the Settings page: a number field that validates before it saves, a "reset to default" link,
// a search box over every setting, and the backup / history panel.
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

export type RegSetting = { key: string; label: string; tab: string; kind: string; effect: string; help: string; where: string; internal: boolean; secret: boolean;
  min: number | null; max: number | null; choices: string[]; max_len: number | null; default: unknown; value: unknown; changed: boolean; field: string; set?: boolean };
export type Registry = { tabs: string[]; settings: RegSetting[] };

/** The registry of every setting (labels, defaults, limits). Loaded once per page. */
export function useRegistry(reloadKey = 0) {
  const [reg, setReg] = useState<Registry | null>(null);
  useEffect(() => { api<Registry>("/api/settings/registry").then(setReg).catch(() => setReg(null)); }, [reloadKey]);
  const byField = useMemo(() => {
    const m: Record<string, RegSetting> = {};
    for (const s of reg?.settings ?? []) m[s.field || s.key] = s;
    return m;
  }, [reg]);
  return { reg, byField };
}

/** Why `text` is not acceptable as a number between min and max, or "" when it is fine. */
export function numberProblem(text: string, min: number, max: number, integer: boolean): string {
  const t = text.trim();
  const what = `${integer ? "a whole number" : "a number"} between ${min} and ${max}`;
  if (t === "") return `Enter ${what}`;
  const n = Number(t);
  if (!Number.isFinite(n)) return `Enter ${what}`;
  if (integer && !Number.isInteger(n)) return `Enter ${what}`;
  if (n < min || n > max) return `Enter ${what}`;
  return "";
}

/** A number box that checks the value (against the limits the server enforces) before saving it, and says what is wrong. */
export function NumField({ label, value, min, max, integer = true, onSave, sk, info }:
  { label: string; value: number; min: number; max: number; integer?: boolean; onSave: (n: number) => unknown; sk?: string; info?: string }) {
  const [text, setText] = useState(String(value));
  const [problem, setProblem] = useState("");
  useEffect(() => { setText(String(value)); setProblem(""); }, [value]);
  const commit = () => {
    const p = numberProblem(text, min, max, integer);
    setProblem(p);
    if (p) return;
    const n = Number(text);
    if (n !== value) void onSave(n);
  };
  return (
    <label data-setting={sk}>{label}
      <input type="number" min={min} max={max} step={integer ? 1 : "any"} value={text} aria-invalid={!!problem} aria-label={label}
        onChange={(e) => { setText(e.target.value); setProblem(""); }} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") commit(); }} />
      {info && <InfoTip id={info} />}
      {problem && <span className="small err" role="alert">{problem}</span>}
    </label>
  );
}

/** "Reset to default" link, shown only when the setting differs from its default. */
export function ResetLink({ sk, reg, onReset }: { sk: string; reg: RegSetting | undefined; onReset: () => void }) {
  if (!reg || !reg.changed || reg.secret) return null;
  const d = typeof reg.default === "boolean" ? (reg.default ? "on" : "off") : Array.isArray(reg.default) ? "none" : String(reg.default === "" ? "empty" : reg.default);
  return <button className="ghost small reset-link" title="Put this setting back to its original value"
    onClick={async () => { try { await api("/api/settings/reset", { method: "POST", body: JSON.stringify({ keys: [sk] }) }); toast(`${reg.label} is back to its default`); onReset(); } catch (e) { toast((e as Error).message, "err"); } }}>
    Reset to default ({d})</button>;
}

/** Search every setting by name or what it does; picking one opens its tab and highlights it. */
export function SettingsSearch({ reg, tabs, go }: { reg: Registry | null; tabs: [string, string][]; go: (tab: string, key: string) => void }) {
  const [q, setQ] = useState("");
  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t || !reg) return [];
    const label = (tab: string) => tabs.find(([k]) => k === tab)?.[1] ?? tab;
    return reg.settings.filter((s) => !s.internal && s.tab && (`${s.label} ${s.effect} ${s.key} ${label(s.tab)}`).toLowerCase().includes(t)).slice(0, 8)
      .map((s) => ({ s, tab: label(s.tab) }));
  }, [q, reg, tabs]);
  return (
    <div className="settings-search" role="search">
      <input type="search" value={q} placeholder="Search settings…" aria-label="Search settings" onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") setQ(""); }} />
      {q.trim() && <div className="settings-hits" role="listbox" aria-label="Matching settings">
        {hits.map(({ s, tab }) => (
          <button key={s.key} role="option" aria-selected={false} onClick={() => { go(s.tab, s.field || s.key); setQ(""); }}>
            <b>{s.label}</b><span className="small mut"> — {tab}</span><span className="small mut block">{s.effect}</span></button>))}
        {hits.length === 0 && <div className="small mut" style={{ padding: 10 }}>No setting matches “{q}”.</div>}
      </div>}
    </div>
  );
}

type Change = { key: string; label: string; from: unknown; to: unknown };
type Plan = { applied: boolean; changes: Change[]; errors: { key: string; error: string }[] };
const show = (v: unknown) => (typeof v === "string" ? (v === "" ? "empty" : v) : JSON.stringify(v));

/** Export / import (with a preview) / reset everything / what changed recently. Secrets are never included. */
export function BackupPanel({ onChanged }: { onChanged: () => void }) {
  const [text, setText] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [hist, setHist] = useState<{ ts: number; key: string; label: string; from: string | null; to: string | null }[]>([]);
  const [err, setErr] = useState("");
  const loadHist = () => api<typeof hist>("/api/settings/history?limit=30").then(setHist).catch(() => setHist([]));
  useEffect(() => { loadHist(); }, []);
  const download = async () => {
    try {
      const data = await api("/api/settings/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = "agent-studio-settings.json"; a.click();
      URL.revokeObjectURL(url);
      toast("Settings exported (no keys or tokens are included)");
    } catch (e) { setErr((e as Error).message); }
  };
  const preview = async () => {
    setErr(""); setPlan(null);
    let data: unknown;
    try { data = JSON.parse(text); } catch { setErr("That is not valid JSON. Paste the contents of an exported settings file."); return; }
    try { setPlan(await api<Plan>("/api/settings/import", { method: "POST", body: JSON.stringify({ data, dry_run: true }) })); } catch (e) { setErr((e as Error).message); }
  };
  const apply = async () => {
    try {
      const r = await api<Plan>("/api/settings/import", { method: "POST", body: JSON.stringify({ data: JSON.parse(text), dry_run: false }) });
      toast(`${r.changes.length} setting${r.changes.length === 1 ? "" : "s"} imported`); setPlan(null); setText(""); loadHist(); onChanged();
    } catch (e) { setErr((e as Error).message); }
  };
  const resetAll = async () => {
    if (!confirm("Put every setting on this page back to its default? Your keys, providers, agents and chats are not touched.")) return;
    try { await api("/api/settings/reset", { method: "POST", body: JSON.stringify({ all: true }) }); toast("All settings are back to their defaults"); loadHist(); onChanged(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div>
      <p className="page-sub">Keep a copy of your settings, move them to another PC, or start again. API keys, tokens and passwords are never exported and never imported.</p>
      <div className="card">
        <div className="card-head"><b>Export</b></div>
        <div className="row"><button className="pri" onClick={download}>Download settings file</button>
          <button onClick={resetAll}>Reset all settings to defaults</button></div>
      </div>
      <div className="card">
        <div className="card-head"><b>Import</b></div>
        <textarea rows={5} value={text} placeholder="Paste the contents of an exported settings file" aria-label="Settings file contents" onChange={(e) => { setText(e.target.value); setPlan(null); }} />
        <div className="row" style={{ marginTop: 8 }}><button disabled={!text.trim()} onClick={preview}>Preview changes</button>
          {plan && plan.changes.length > 0 && <button className="pri" onClick={apply}>Apply {plan.changes.length} change{plan.changes.length === 1 ? "" : "s"}</button>}</div>
        {err && <p className="err small" role="alert">{err}</p>}
        {plan && <div style={{ marginTop: 8 }}>
          {plan.changes.length === 0 && plan.errors.length === 0 && <p className="mut small">Nothing would change.</p>}
          {plan.changes.map((c) => <div key={c.key} className="small"><b>{c.label}</b>: {show(c.from)} → {show(c.to)}</div>)}
          {plan.errors.map((e) => <div key={e.key + e.error} className="small err">{e.key || "file"}: {e.error}</div>)}
        </div>}
      </div>
      <div className="card">
        <div className="card-head"><b>Recent changes</b></div>
        {hist.length === 0 && <p className="mut small">Nothing changed yet.</p>}
        {hist.map((h, i) => <div key={i} className="small" style={{ padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
          <span className="mut">{new Date(h.ts * 1000).toLocaleString()}</span> · <b>{h.label}</b>: {h.from === null ? "not set" : h.from} → {h.to}</div>)}
      </div>
    </div>
  );
}
