"use client";
// Apps: small web apps that agents build with you and that use your agents. This page is the library; an app is opened full screen in /apps/edit/.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { AgentLite, AppRow, ago, fmtSize, Template, TYPE_LABEL } from "@/lib/apps";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete, Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";
import SimpleCreate from "@/components/apps/SimpleCreate";

type Step = null | "choose" | "simple" | "advanced";

function AdvancedCreate({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("static-chat");
  const [agent, setAgent] = useState("");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<Template[]>("/api/apps/templates").then(setTemplates).catch((e) => setErr(e.message));
    api<AgentLite[]>("/api/agents").then((a) => setAgents(a.filter((x) => x.id !== "app_builder"))).catch((e) => setErr(e.message));
  }, []);
  const make = async () => {
    try { const a = await api<AppRow>("/api/apps", { method: "POST", body: JSON.stringify({ name, template, agent_id: agent, mode: "advanced" }) }); onCreated(a.id); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div data-testid="advanced-create">
      <label>Name<input value={name} aria-label="App name" onChange={(e) => setName(e.target.value)} placeholder="Support reply helper" /></label>
      <div className="grid" style={{ marginTop: 8 }}>
        {templates.map((t) => (
          <label key={t.id} className="card" style={{ cursor: "pointer", borderColor: template === t.id ? "var(--acc)" : undefined }}>
            <input type="radio" name="tpl" checked={template === t.id} onChange={() => setTemplate(t.id)} aria-label={t.label} /> <b>{t.label}</b>
            <div className="small mut">{t.description}</div>
          </label>
        ))}
      </div>
      <label style={{ marginTop: 8, display: "block" }}>The agent it calls (you can change this later)
        <select value={agent} aria-label="Agent the app uses" onChange={(e) => setAgent(e.target.value)}>
          <option value="">None yet</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select></label>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 12 }}><button className="acc" disabled={!name.trim()} onClick={make}>Create and open</button></div>
    </div>
  );
}

export default function AppsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<AppRow[] | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [step, setStep] = useState<Step>(null);
  const [del, setDel] = useState<AppRow | null>(null);
  const load = useCallback(() => api<AppRow[]>("/api/apps").then((a) => { setApps(a); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const shown = useMemo(() => (apps ?? []).filter((a) => (!kind || a.type === kind) && (!q.trim() || `${a.name} ${a.description}`.toLowerCase().includes(q.trim().toLowerCase()))), [apps, q, kind]);
  const open = (id: string, extra = "") => router.push(`/apps/edit/?id=${id}${extra}`);
  const copy = async (a: AppRow) => {
    try { await api(`/api/apps/${a.id}/duplicate`, { method: "POST" }); toast("App copied"); load(); } catch (e) { setErr((e as Error).message); }
  };
  const remove = async (a: AppRow) => {
    try { await api(`/api/apps/${a.id}?confirm=true`, { method: "DELETE" }); setDel(null); toast("App deleted"); load(); } catch (e) { setDel(null); setErr((e as Error).message); }
  };
  return (
    <div data-testid="apps-page">
      <div className="page-head"><h2>Apps</h2><InfoTip id="apps" /><span className="grow" /><button className="acc" onClick={() => setStep("choose")}>New app</button></div>
      <p className="page-sub">Small web apps that an agent builds with you and that use your agents. Pages run in a locked preview; each app can call only the agents you list for it.</p>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ gap: 10, flexWrap: "wrap", margin: "8px 0" }}>
        <input type="search" value={q} aria-label="Search apps" placeholder="Search apps" onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} />
        <select value={kind} aria-label="Kind" onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option><option value="static">Pages</option><option value="node">Node servers</option><option value="python">Python servers</option>
        </select>
      </div>
      {apps === null && !err && <div className="mut">Loading your apps…</div>}
      {apps !== null && apps.length === 0 && (
        <div className="card" data-testid="apps-empty"><b>No apps yet</b><div className="small mut">Describe what you want and an agent builds it, or start from a template and write it yourself.</div>
          <div className="row" style={{ marginTop: 8 }}><button className="acc" onClick={() => setStep("choose")}>Create the first one</button></div></div>
      )}
      {apps !== null && apps.length > 0 && shown.length === 0 && <div className="mut">No app matches.</div>}
      <div className="apps-grid" role="list">
        {shown.map((a) => (
          <div key={a.id} className="card apps-card" role="listitem">
            <div className="row"><button className="linkish" aria-label={`Open ${a.name}`} onClick={() => open(a.id)}><b>{a.name}</b></button><span className="grow" />
              <span className="chip small">{TYPE_LABEL[a.type]}</span>{a.mode === "simple" && <span className="chip small">simple</span>}</div>
            <div className="small mut">{a.description || "No description"}</div>
            <div className="small">{a.agents.length ? <>Calls: {a.agents.join(", ")}</> : <span className="mut">Calls no agent yet</span>}</div>
            <div className="small mut">{a.files} files · {fmtSize(a.size)} · version {a.version} · edited {ago(a.updated)}{a.git?.remote ? " · git" : ""}</div>
            {a.pending > 0 && <div className="small" style={{ color: "var(--warn)" }}>{a.pending} change{a.pending === 1 ? "" : "s"} waiting for your review</div>}
            <div className="row" style={{ marginTop: 6, gap: 6 }}>
              <button onClick={() => open(a.id)}>Open</button><button onClick={() => copy(a)} aria-label={`Duplicate ${a.name}`}>Duplicate</button>
              <button onClick={() => setDel(a)} aria-label={`Delete ${a.name}`}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      {step === "choose" && (
        <Modal title="New app" onClose={() => setStep(null)}>
          <div className="grid">
            <button className="card" style={{ textAlign: "left" }} onClick={() => setStep("simple")}><b>Simple</b><div className="small mut">Describe it, attach files, and the builder agent writes it. Best if you do not want to touch the code.</div></button>
            <button className="card" style={{ textAlign: "left" }} onClick={() => setStep("advanced")}><b>Advanced</b><div className="small mut">Start from a template, edit the files yourself, and review each change the agent suggests.</div></button>
          </div>
        </Modal>
      )}
      {step === "simple" && <Modal title="New app: simple" onClose={() => setStep(null)}><SimpleCreate onCreated={(id) => { setStep(null); open(id, "&build=1"); }} /></Modal>}
      {step === "advanced" && <Modal title="New app: advanced" onClose={() => setStep(null)}><AdvancedCreate onCreated={(id) => { setStep(null); open(id); }} /></Modal>}
      {del && <ConfirmDelete what={`"${del.name}"`} onConfirm={() => remove(del)} onClose={() => setDel(null)} />}
    </div>
  );
}
