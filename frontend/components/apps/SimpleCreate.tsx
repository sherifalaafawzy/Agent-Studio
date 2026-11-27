"use client";
// Simple mode: describe the app, attach files (a spec, sample data, a picture), pick the agent it should use. The app is made from a standard template and the builder
// agent then fills it in while you watch (see the workspace). Nothing here runs anything: it only creates the app and keeps your attachments in its assets/ folder.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { AgentLite, AppRow, nameFromPrompt, readAttachment, Template } from "@/lib/apps";

export const BUILD_KEY = (id: string) => `app-build:${id}`;

export default function SimpleCreate({ onCreated }: { onCreated: (id: string) => void }) {
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [template, setTemplate] = useState("static-chat");
  const [agent, setAgent] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pick = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api<Template[]>("/api/apps/templates").then(setTemplates).catch((e) => setErr(e.message));
    api<AgentLite[]>("/api/agents").then((a) => setAgents(a.filter((x) => x.id !== "app_builder"))).catch((e) => setErr(e.message));
  }, []);
  const chosen = agents.find((a) => a.id === agent);
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const app = await api<AppRow>("/api/apps", { method: "POST", body: JSON.stringify({ name: nameFromPrompt(prompt), template, agent_id: agent, mode: "simple", description: prompt.trim().slice(0, 300) }) });
      const kept: string[] = [], failed: string[] = [];
      for (const f of files) {
        try { const a = await readAttachment(f); const r = await api<{ path: string }>(`/api/apps/${app.id}/attachments`, { method: "POST", body: JSON.stringify(a) }); kept.push(r.path.split("/").pop() ?? f.name); }
        catch (e) { failed.push(`${f.name}: ${(e as Error).message}`); }
      }
      try { sessionStorage.setItem(BUILD_KEY(app.id), JSON.stringify({ prompt: prompt.trim(), attachments: kept, failed })); } catch { /* the builder just starts without the note */ }
      onCreated(app.id);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  return (
    <div data-testid="simple-create">
      <label>What should the app do?
        <textarea value={prompt} rows={5} aria-label="What should the app do?" onChange={(e) => setPrompt(e.target.value)}
          placeholder="A page where I paste a customer email and get a polite reply from my support agent." /></label>
      <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 8 }}>
        <label className="grow">The agent it should use
          <select value={agent} aria-label="Agent the app uses" onChange={(e) => setAgent(e.target.value)}>
            <option value="">Let the builder suggest one</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select></label>
        <label className="grow">Kind of app
          <select value={template} aria-label="Kind of app" onChange={(e) => setTemplate(e.target.value)}>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select></label>
      </div>
      {chosen && !chosen.api_access && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>{chosen.name} has API access switched off, so an app cannot call it yet. You can turn it on in the app&apos;s settings.</div>}
      {templates.find((t) => t.id === template) && <div className="small mut">{templates.find((t) => t.id === template)!.description}</div>}
      <div style={{ marginTop: 8 }}>
        <input ref={pick} type="file" multiple hidden aria-label="Attach files" onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])])} />
        <button type="button" onClick={() => pick.current?.click()}>Attach files</button>
        <span className="small mut"> a spec, sample data (csv, json), a picture or a PDF; up to 2 MB each</span>
        {files.length > 0 && (
          <ul className="small" aria-label="Attached files" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {files.map((f, i) => <li key={`${f.name}${i}`}>{f.name} <button className="icon-btn" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button></li>)}
          </ul>
        )}
      </div>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="acc" disabled={busy || !prompt.trim()} onClick={go}>{busy ? "Creating…" : "Build it"}</button>
        <span className="small mut">The builder agent writes the app and you watch it appear. Nothing runs on your PC outside the locked preview.</span>
      </div>
    </div>
  );
}
