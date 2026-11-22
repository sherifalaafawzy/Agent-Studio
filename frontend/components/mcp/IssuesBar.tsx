"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IChevron } from "@/components/Icons";
import type { Issue } from "./shared";

/** One line when the harness has raised issues; opens to the full list with the buttons to debug or dismiss each. */
export default function IssuesBar({ issues, onChange, onOpen }: { issues: Issue[]; onChange: () => void; onOpen: (source: string) => void }) {
  const [open, setOpen] = useState(false);
  if (!issues.length) return null;
  async function debug(i: Issue) {
    try { await api(`/api/issues/${i.id}/debug`, { method: "POST" }); toast("Debugging started — the agent's report will appear in Runs and Recents"); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function dismiss(i: Issue) { await api(`/api/issues/${i.id}/resolve`, { method: "POST" }).catch(() => {}); onChange(); }
  return (
    <div className="issues mcp-issues">
      <button className="issues-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <b>Needs attention</b><span className="pill warn">{issues.length}</span>
        <span className="small mut grow">Raised by the harness itself — each one says what to do next.</span>
        <IChevron size={14} className={open ? "rot90" : ""} />
      </button>
      {open && issues.map((i) => (
        <div key={i.id} className="issue">
          <div className="issue-head"><span className="err-dot">!</span><b>{i.title}</b>
            <button className="ghost small" style={{ marginLeft: "auto" }} onClick={() => onOpen(i.source)}>Open server</button></div>
          <div className="small" style={{ color: "var(--fg2)", margin: "4px 0 6px" }}>{i.suggestion}</div>
          {i.detail && <details className="small mut"><summary>Details</summary><pre className="err-raw">{i.detail}</pre></details>}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="acc small" disabled={!!i.debug_job} onClick={() => debug(i)}>{i.debug_job ? "Debugging…" : "Debug with an agent"}</button>
            <button className="ghost small" onClick={() => dismiss(i)}>Mark resolved</button>
          </div>
        </div>))}
    </div>
  );
}
