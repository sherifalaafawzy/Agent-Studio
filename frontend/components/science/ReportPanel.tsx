"use client";
// Science Lab > Report: the labelled report built from what was recorded, with a count of unsupported claims, and "Keep as artifacts" (report.md, evidence.json, run.json).
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { labelShow } from "@/lib/science";
import Markdown from "../Markdown";
import { toast } from "../Nav";

type Report = { markdown: string; label_counts: Record<string, number>; unsupported: number };

export default function ReportPanel({ investigationId, refreshKey }: { investigationId: string; refreshKey: number }) {
  const [rep, setRep] = useState<Report | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Report>(`/api/science/investigations/${investigationId}/report`).then((r) => { setRep(r); setErr(""); }).catch((e) => setErr(e.message)), [investigationId]);
  useEffect(() => { void load(); }, [load, refreshKey]);
  const keep = async () => {
    try { const r = await api<{ files: { name: string }[] }>(`/api/science/investigations/${investigationId}/notebook`, { method: "POST" }); toast(`Kept ${r.files.length} files on the Artifacts page`); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  if (err) return <div className="card err" role="alert">{err}</div>;
  if (!rep) return <div className="card">Building the report…</div>;
  return (
    <div className="card" data-testid="science-report">
      <div className="card-head"><b>Report</b></div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {Object.entries(rep.label_counts).map(([l, n]) => { const s = labelShow(l); return <span key={l} className={`sci-badge ${s.tone}`}><span aria-hidden="true">{s.icon}</span> {l.replace(/_/g, " ")}: {n}</span>; })}
        {rep.unsupported > 0 && <span role="note" className="small" style={{ color: "var(--bad)" }}>{rep.unsupported} claim(s) have no usable evidence.</span>}
      </div>
      <div className="sci-report"><Markdown text={rep.markdown} /></div>
      <div className="row" style={{ gap: 6 }}>
        <button className="acc" onClick={keep}>Keep as artifacts</button>
        <button className="ghost" onClick={() => { void navigator.clipboard?.writeText(rep.markdown); toast("Copied the report"); }}>Copy Markdown</button>
      </div>
    </div>
  );
}
