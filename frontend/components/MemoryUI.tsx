"use client";
// Small memory pieces used in several places: the per-agent memory picker (agent editor and wizard) and the note under a chat answer
// that shows what the agent remembered, recalled or forgot during that turn.
import { useState } from "react";
import InfoTip from "./InfoTip";

export type MemCfg = { types?: string[]; read_scopes?: string[]; write_scopes?: string[]; auto_recall?: boolean; auto_save?: string };
export type MemEvent = { kind: string; id?: string; type?: string; scope?: string; text?: string; score?: number; file?: string; chars?: number };

export const MEMORY_TYPES: [string, string, string][] = [
  ["working", "Working memory", "Summarises the chat automatically when it fills up, so long chats keep going."],
  ["semantic", "Facts", "Facts about you and your projects, with where each came from."],
  ["episodic", "Past chats", "Short summaries of earlier chats, so it can say “last week we decided…”."],
  ["entity", "People & things", "Records for people, projects and places, updated as it learns more."],
  ["procedural", "How-tos", "Steps it learned for doing something. A good one can become a Skill."],
  ["notes", "Project notes", "A notes file (AGENTS.md) in the workspace folder that you can edit too."],
];
export const SCOPE_LABELS: Record<string, string> = { agent: "this agent only", user: "all my agents", workspace: "this workspace folder", global: "everyone" };
const ORDER = ["agent", "user", "workspace", "global"];
const DEFAULT: Required<Omit<MemCfg, "types">> = { read_scopes: ["agent", "user", "workspace", "global"], write_scopes: ["agent", "user", "workspace"], auto_recall: true, auto_save: "review" };

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/** Which kinds of memory an agent uses and who it shares them with. Emits {} when none is ticked (memory off). */
export function MemoryPicker({ value, onChange }: { value: MemCfg | undefined; onChange: (v: MemCfg) => void }) {
  const v = value ?? {};
  const types = v.types ?? [];
  const cur = { ...DEFAULT, ...v, types };
  const emit = (patch: MemCfg) => {
    const next = { ...cur, ...patch };
    onChange(next.types.length ? next : {});
  };
  return (
    <div data-testid="memory-picker">
      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        {MEMORY_TYPES.map(([id, label, help]) => (
          <label key={id} style={{ flexDirection: "row", alignItems: "center", gap: 5 }} title={help}>
            <input type="checkbox" checked={types.includes(id)} onChange={() => emit({ types: toggle(types, id) })} />{label}</label>))}
      </div>
      {types.length === 0 ? <div className="small mut" style={{ marginTop: 6 }}>No memory: this agent starts every chat fresh. Tick a kind to let it remember.</div> : (
        <div style={{ marginTop: 10 }}>
          <div className="small mut" style={{ marginBottom: 4 }}>It may read memories shared with: <InfoTip id="memory-scope" /></div>
          <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
            {ORDER.map((s) => <label key={s} style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
              <input type="checkbox" checked={cur.read_scopes.includes(s)} onChange={() => emit({ read_scopes: toggle(cur.read_scopes, s) })} />{SCOPE_LABELS[s]}</label>)}
          </div>
          <div className="small mut" style={{ margin: "8px 0 4px" }}>It may save memories for:</div>
          <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
            {ORDER.map((s) => <label key={s} style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
              <input type="checkbox" checked={cur.write_scopes.includes(s)} onChange={() => emit({ write_scopes: toggle(cur.write_scopes, s) })} />{SCOPE_LABELS[s]}</label>)}
          </div>
          <div className="row" style={{ flexWrap: "wrap", gap: 16, marginTop: 10 }}>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
              <input type="checkbox" checked={cur.auto_recall} onChange={() => emit({ auto_recall: !cur.auto_recall })} />Bring up relevant memories before it answers</label>
            <label>When it learns something new
              <select value={cur.auto_save} onChange={(e) => emit({ auto_save: e.target.value })}>
                <option value="review">ask me first (review queue)</option><option value="auto">save it straight away</option><option value="off">never save by itself</option></select>
              <InfoTip id="memory-review" /></label>
          </div>
        </div>)}
    </div>
  );
}

const LABEL: Record<string, string> = { recalled: "recalled", saved: "remembered", pending: "waiting for your review", duplicate: "already known", forgot: "forgot", episode: "chat summary kept",
  notes: "read the project notes", notes_written: "added to the project notes", compacted: "summarised the chat", skipped: "not saved" };

/** "Memory: recalled 2 · remembered 1" under an answer; click to see each one. */
export function MemoryNote({ events }: { events: MemEvent[] }) {
  const [open, setOpen] = useState(false);
  if (!events?.length) return null;
  const counts: Record<string, number> = {};
  for (const e of events) counts[e.kind] = (counts[e.kind] ?? 0) + 1;
  const summary = Object.entries(counts).map(([k, n]) => `${LABEL[k] ?? k} ${n}`).join(" · ");
  return (
    <div className="memory-note small">
      <button className="ghost small" aria-expanded={open} onClick={() => setOpen((o) => !o)}>🧠 Memory: {summary}</button>
      {open && <ul>{events.map((e, i) => <li key={i}><b>{LABEL[e.kind] ?? e.kind}</b>{e.type ? ` (${e.type}${e.scope ? `, ${SCOPE_LABELS[e.scope] ?? e.scope}` : ""})` : ""}{e.text ? `: ${e.text}` : e.file ? `: ${e.file}` : ""}</li>)}</ul>}
    </div>
  );
}
