"use client";
// Science Lab > Components: optional science tools (PaperQA2, ToolUniverse...) that can be installed, each into its own isolated environment, after you read the plan and accept the licence.
// Hosted services cannot be installed. Claude Science and Google's co-scientist are not part of this lab: Agent Studio does not connect to, drive or install them.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Component } from "@/lib/science";
import { toast } from "../Nav";

type Status = { installing: string; log: string[]; components: Component[] };

export default function ComponentsPanel() {
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState("");
  const [ack, setAck] = useState<Record<string, boolean>>({});
  const load = useCallback(() => api<Status>("/api/science/components").then((s) => { setSt(s); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!st?.installing) return;
    const t = setInterval(() => { void load(); }, 2500);
    return () => clearInterval(t);
  }, [st?.installing, load]);
  const install = async (id: string) => {
    try { setSt(await api<Status>(`/api/science/components/${id}/install`, { method: "POST", body: JSON.stringify({ licence_ack: !!ack[id] }) })); toast("Installing in its own environment"); }
    catch (e) { setErr((e as Error).message); }
  };
  const remove = async (id: string) => {
    try { setSt(await api<Status>(`/api/science/components/${id}`, { method: "DELETE" })); toast("Removed"); } catch (e) { setErr((e as Error).message); }
  };
  if (!st) return <div className="card" data-testid="science-components">{err ? <span className="err" role="alert">{err}</span> : "Loading…"}</div>;
  return (
    <div data-testid="science-components">
      <div className="card small" role="note">Each component is installed into its own environment, never into Agent Studio's. Only ready-made packages at least 14 days old are installed. Claude Science and Google's co-scientist are not part of this lab.</div>
      {err && <div className="err small" role="alert">{err}</div>}
      {st.components.map((c) => (
        <div key={c.id} className="card" data-testid={`component-${c.id}`}>
          <div className="card-head"><b>{c.name}</b> <span className="chip">{c.kind === "hosted" ? "hosted service" : "self-hostable"}</span> {c.state === "ready" && <span className="chip">installed {c.installed_version}</span>}</div>
          <div>{c.summary}</div>
          <dl className="sci-dl small">
            <dt>Licence</dt><dd>{c.licence || "unknown"} {c.licence_verified ? <span className="mut">(confirmed: {c.licence_source})</span> : <span style={{ color: "var(--warn-text)" }}>(not confirmed)</span>}</dd>
            {c.hardware && <><dt>Needs</dt><dd>{c.hardware}</dd></>}{c.disk && <><dt>Disk</dt><dd>{c.disk}</dd></>}{c.keys && <><dt>Keys</dt><dd>{c.keys}</dd></>}
            {c.egress && <><dt>Where data goes</dt><dd>{c.egress}</dd></>}{c.cost && <><dt>Cost</dt><dd>{c.cost}</dd></>}
          </dl>
          {c.caution && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>⚠ {c.caution}</div>}
          {c.link && /^https:\/\//.test(c.link) && <a className="small" href={c.link} target="_blank" rel="noopener noreferrer">Project page ↗</a>}
          {!c.can_install && c.reasons.length > 0 && <div className="small mut" role="note" style={{ marginTop: 4 }}>{c.reasons.join(" ")}</div>}
          {c.can_install && c.state !== "ready" && (
            <div style={{ marginTop: 6 }}>
              <div className="small mut">Plan: {c.steps.join(" → ")}.</div>
              <label className="check"><input type="checkbox" checked={!!ack[c.id]} onChange={(e) => setAck({ ...ack, [c.id]: e.target.checked })} /> I accept the {c.licence} licence</label>
              <button className="acc" disabled={!ack[c.id] || c.installing || !!st.installing} onClick={() => install(c.id)}>{c.installing ? "Installing…" : "Install"}</button>
            </div>)}
          {c.state === "failed" && <div className="err small" role="alert" style={{ whiteSpace: "pre-wrap" }}>{c.error}</div>}
          {c.state === "ready" && <div style={{ marginTop: 6 }}>{c.command && <div className="small">Start it with: <code>{c.command}</code></div>}<button className="ghost small" onClick={() => remove(c.id)}>Remove</button></div>}
          {c.installing && st.log.length > 0 && <pre className="small sci-log" aria-label="Install log">{st.log.slice(-8).join("\n")}</pre>}
        </div>))}
    </div>
  );
}
