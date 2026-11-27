"use client";
// "Try this box": run ONE box with text you type (no side effects unless you allow them), and "Run from here": start at this box and reuse an earlier run for everything before it.
import { useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import type { Doc, TestResult, WNode } from "@/lib/workflows";

export default function TryBox({ doc, node, lastInput, canRunFrom, runFromWhy, onRunFrom }: {
  doc: Doc; node: WNode; lastInput?: string; canRunFrom: boolean; runFromWhy: string; onRunFrom: () => void;
}) {
  const [input, setInput] = useState(lastInput ?? "");
  const [allow, setAllow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TestResult | null>(null);
  const [err, setErr] = useState("");
  const test = async () => {
    setBusy(true); setErr(""); setRes(null);
    try { setRes(await api<TestResult>(`/api/workflows/${doc.id}/nodes/${node.id}/test`, { method: "POST", body: JSON.stringify({ doc, input, task: input, allow_side_effects: allow }) })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section className="card" data-testid="try-box" style={{ marginTop: 12 }}>
      <div className="row"><b className="small">Try this box</b><InfoTip id="wf-run-from" /></div>
      <label>Text this box receives<textarea rows={3} value={input} aria-label="Test input" placeholder="What the box before it would hand over" onChange={(e) => setInput(e.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} /> Allow side effects (a box that could change something or spend money only runs if this is ticked)</label>
      <div className="row" style={{ marginTop: 6 }}>
        <button onClick={test} disabled={busy}>{busy ? "Trying…" : "Test this box"}</button>
        <button onClick={onRunFrom} disabled={!canRunFrom} title={canRunFrom ? "Run from here, reusing the last run for everything before this box" : runFromWhy}>Run from here</button>
      </div>
      {!canRunFrom && <div className="small mut" style={{ marginTop: 4 }}>{runFromWhy}</div>}
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 6 }}>{err}</div>}
      {res && (
        <div data-testid="try-result" style={{ marginTop: 8 }}>
          <span className={`pill ${res.status === "done" ? "ok" : res.status === "error" ? "bad" : "warn"}`}>{res.status === "not_run" ? "not run" : res.status}</span>
          {res.ports && res.ports.length > 0 && res.ports[0] !== "out" && <span className="small mut"> went {res.ports.join(", ")}</span>}
          {res.message && <div className="small" style={{ marginTop: 4 }}>{res.message}</div>}
          {res.error && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 4 }}>{res.error}</div>}
          {res.status === "done" && <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", marginTop: 4 }}>{(res.output ?? "").slice(0, 4000) || "(empty)"}</pre>}
        </div>)}
    </section>
  );
}
