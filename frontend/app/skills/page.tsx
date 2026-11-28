"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import Markdown from "@/components/Markdown";
import { IBook, ICopy, IEdit, IPlus, ISave, ISearch, ITrash } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";

type Skill = { name: string; description: string; mcp: string[]; source: string; group?: string; used_by?: string[]; body?: string };
type Mcp = { id: string; label: string; ready: boolean };
type Draft = { name: string; description: string; body: string; mcp: string[]; group: string; editing: boolean };

const SUGGESTED = ["Coding", "Security", "Research", "Writing", "Data", "Ops", "General"];
const groupOf = (s: Skill) => s.group || "General";

const blank: Draft = { name: "", description: "", body: "## When to use\n\n## Steps\n1. \n", mcp: [], group: "General", editing: false };

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcp, setMcp] = useState<Mcp[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<Skill | null>(null);
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState(false);
  const [msg, setMsg] = useState("");
  const [shut, setShut] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([api<Skill[]>("/api/skills"), api<Mcp[]>("/api/mcp")]);
      setSkills(s); setMcp(m); setMsg("");
      setSel((cur) => cur ?? s[0]?.name ?? null);
    } catch (e) { setMsg("Backend offline: " + (e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (sel) api<Skill>(`/api/skills/${sel}`).then(setDetail).catch(() => setDetail(null)); }, [sel]);

  const shown = skills.filter((s) => !q || (s.name + " " + s.description + " " + groupOf(s)).toLowerCase().includes(q.toLowerCase()));
  const grouped = [...shown.reduce((m, s) => m.set(groupOf(s), [...(m.get(groupOf(s)) ?? []), s]), new Map<string, Skill[]>())]
    .sort(([a], [b]) => Number(a === "General") - Number(b === "General") || a.localeCompare(b));
  const groupNames = [...new Set([...SUGGESTED, ...skills.map(groupOf)])];
  const toggleGroup = (g: string) => setShut((c) => (c.includes(g) ? c.filter((x) => x !== g) : [...c, g]));
  const edit = (k: Skill) => { setDraft({ name: k.name, description: k.description, body: k.body ?? "", mcp: k.mcp, group: groupOf(k), editing: true }); setPreview(false); };

  async function save() {
    if (!draft) return;
    try {
      const r = await api<Skill>("/api/skills", { method: "POST", body: JSON.stringify({ name: draft.name, description: draft.description, body: draft.body, mcp: draft.mcp, group: draft.group, overwrite: draft.editing }) });
      setDraft(null); setPreview(false); await load(); setSel(r.name); setDetail(r);
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function del(name: string) {
    if (!confirm(`Delete the skill “${name}”?`)) return;
    try { await api(`/api/skills/${name}`, { method: "DELETE" }); setSel(null); setDetail(null); await load(); } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  async function duplicate(name: string) {
    try {
      const r = await api<Skill>(`/api/skills/${name}/duplicate`, { method: "POST" });
      await load(); setSel(r.name); setDetail(r); setMsg(""); edit(r);
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function importFile(file: File | undefined) {
    if (!file) return;
    try {
      const r = await api<Skill>("/api/skills/import", { method: "POST", body: JSON.stringify({ text: await file.text() }) });
      setDraft(null); await load(); setSel(r.name); setDetail(r); setMsg("");
    } catch (e) { setMsg("⚠ Import failed: " + (e as Error).message); }
  }

  return (
    <div>
      <div className="page-head"><h2>Skills</h2><InfoTip id="skills" /><span className="grow" />
        <button onClick={() => fileRef.current?.click()} title="Import a SKILL.md file" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><ISave size={15} />Import</button>
        <input ref={fileRef} type="file" accept=".md,text/markdown,text/plain" hidden aria-label="Import SKILL.md" onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
        <button className="acc" onClick={() => { setDraft({ ...blank }); setPreview(false); }} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IPlus size={15} />New skill</button></div>
      <p className="page-sub">Reusable know-how agents load on demand. Agents only see each skill&apos;s name and description until they decide to load it, so write the description as “use this when…”. A skill can bring MCP servers with it. Group them (Coding, Security…) so an agent can attach a whole group at once. Agents can also author skills themselves (<code>create_skill</code>).</p>
      {msg && <p className="err">{msg}</p>}
      <div className="two skills-two">
        <div className="list">
          <div style={{ position: "relative" }}>
            <input className="search" placeholder="Search skills" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 32 }} />
            <span style={{ position: "absolute", left: 10, top: 9, color: "var(--mut)" }}><ISearch size={15} /></span></div>
          {grouped.map(([g, list]) => {
            const open = !!q || !shut.includes(g);
            return (
              <div key={g} data-group={g}>
                <button className="sk-gh" aria-expanded={open} onClick={() => toggleGroup(g)}><span className={`chev ${open ? "open" : ""}`}>▶</span>{g}<span className="n">{list.length}</span></button>
                {open && list.map((s) => (
                  <div key={s.name} className={`li ${sel === s.name && !draft ? "on" : ""}`} onClick={() => { setDraft(null); setSel(s.name); }}>
                    <IBook size={15} /><span className="t" title={s.name}>{s.name}</span><span className={`pill ${s.source === "builtin" ? "" : "acc"}`}>{s.source === "builtin" ? "built-in" : "yours"}</span>
                  </div>))}
              </div>);
          })}
          {shown.length === 0 && <div className="small mut" style={{ padding: 10 }}>No skills match.</div>}
        </div>

        <div>
          {draft ? (
            <div className="card">
              <div className="row"><h3 style={{ margin: 0 }}>{draft.editing ? `Edit “${draft.name}”` : "New skill"}</h3><span className="grow" />
                <div className="seg"><button className={!preview ? "on" : ""} onClick={() => setPreview(false)}>Write</button><button className={preview ? "on" : ""} onClick={() => setPreview(true)}>Preview</button></div></div>
              <div className="grid" style={{ marginTop: 12 }}>
                <label>Name (lowercase, hyphens)<input value={draft.name} disabled={draft.editing} placeholder="release-checklist" onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
              </div>
              <label style={{ marginTop: 10 }}>Group
                <input list="skill-groups" value={draft.group} placeholder="General" onChange={(e) => setDraft({ ...draft, group: e.target.value })} />
                <datalist id="skill-groups">{groupNames.map((g) => <option key={g} value={g} />)}</datalist></label>
              <label style={{ marginTop: 10 }}>Use this skill when…<input value={draft.description} placeholder="the user asks to cut a release or bump versions" onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
              <label style={{ marginTop: 10 }}>Instructions (Markdown)</label>
              {preview ? <div className="card" style={{ marginTop: 6, boxShadow: "none", fontFamily: "var(--serif)", fontSize: 16 }}><Markdown text={draft.body} /></div>
                : <textarea rows={14} style={{ width: "100%", marginTop: 6, fontFamily: "var(--mono)", fontSize: 13 }} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />}
              <div style={{ marginTop: 12 }} className="small mut">MCP servers this skill brings along</div>
              <div className="row" style={{ marginTop: 6 }}>{mcp.map((m) => (
                <label key={m.id} style={{ flexDirection: "row", gap: 6, alignItems: "center", color: "var(--fg2)" }}>
                  <input type="checkbox" checked={draft.mcp.includes(m.id)} onChange={() => setDraft({ ...draft, mcp: draft.mcp.includes(m.id) ? draft.mcp.filter((x) => x !== m.id) : [...draft.mcp, m.id] })} />{m.label}</label>))}</div>
              <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
                <button className="ghost" onClick={() => setDraft(null)}>Cancel</button>
                <button className="acc" disabled={!draft.name || !draft.description.trim()} onClick={save}>{draft.editing ? "Save changes" : "Create skill"}</button></div>
            </div>
          ) : detail ? (
            <div className="card">
              <div className="card-head"><IBook /><b style={{ fontFamily: "var(--serif)", fontSize: 24, fontWeight: 500 }}>{detail.name}</b>
                <span className={`pill ${detail.source === "builtin" ? "" : "acc"}`}>{detail.source === "builtin" ? "built-in" : "yours / agent-made"}</span><span className="grow" />
                <div className="sk-actions">
                  <button className="icon-btn" title={detail.source === "builtin" ? "Duplicate to customise" : "Duplicate"} onClick={() => duplicate(detail.name)}><ICopy size={15} /></button>
                  <a className="icon-btn" title="Export SKILL.md" href={`${API_BASE}/api/skills/${detail.name}/export`} download={`${detail.name}-SKILL.md`}><ISave size={15} /></a>
                  {detail.source !== "builtin" && <>
                    <button className="icon-btn" title="Edit" onClick={() => edit(detail)}><IEdit size={15} /></button>
                    <button className="icon-btn" title="Delete" onClick={() => del(detail.name)}><ITrash size={15} /></button></>}
                </div>
              </div>
              <div className="kv" style={{ margin: "12px 0 6px" }}>
                <span>Use when</span><span>{detail.description}</span>
                <span>Group</span><span>{groupOf(detail)}</span>
                <span>Brings MCP</span><span>{detail.mcp.length ? detail.mcp.join(", ") : "—"}</span>
                <span>Used by</span><span className="sk-used">{detail.used_by?.length ? detail.used_by.map((a) => <span key={a} className="pill">{a}</span>) : "no agent yet"}</span>
              </div>
              <div style={{ borderTop: "1px solid var(--line)", marginTop: 12, paddingTop: 14, fontFamily: "var(--serif)", fontSize: 16, lineHeight: 1.7 }}>
                <Markdown text={detail.body ?? ""} /></div>
            </div>
          ) : <div className="empty">Select a skill, or create one.</div>}
        </div>
      </div>
    </div>
  );
}
