"use client";
// The saved record of past live sessions: when, which agent and engine, how long, what it cost (and whether that was reported or estimated), and why it ended.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SessionRow } from "@/lib/live";

const SOURCE: Record<string, string> = { reported: "reported", estimated: "estimated", mixed: "reported + estimated", local: "local", none: "–" };
const ENDED: Record<string, string> = { user: "you ended it", budget: "budget reached" };

export default function LiveHistory({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api<SessionRow[]>("/api/live/history?limit=30").then(setRows).catch((e) => setErr((e as Error).message)); }, [refreshKey]);
  const total = (rows ?? []).reduce((a, r) => a + r.cost_usd, 0);
  return (
    <details className="card" data-testid="live-history">
      <summary><b>Past sessions</b>{rows && rows.length > 0 && <span className="small mut"> · {rows.length} saved · ${total.toFixed(4)} total (cloud)</span>}</summary>
      {err && <div className="err small" role="alert">{err}</div>}
      {rows && rows.length === 0 && <p className="small mut">No sessions yet.</p>}
      {rows && rows.length > 0 && <table className="small" style={{ width: "100%" }}><thead><tr><th>When</th><th>Who</th><th>Engine</th><th>Length</th><th style={{ textAlign: "right" }}>Cost</th><th>Ended</th></tr></thead><tbody>
        {rows.map((r) => (
          <tr key={r.id} data-testid="history-row"><td>{new Date(r.started * 1000).toLocaleString()}</td><td>{r.agent || "no agent"}</td>
            <td>{r.engine === "gemini" ? "Gemini" : "this PC"} · {r.mode}</td><td>{Math.round(r.seconds)} s</td>
            <td style={{ textAlign: "right" }} title={SOURCE[r.cost_source] ?? r.cost_source}>{r.cost_source === "estimated" || r.cost_source === "mixed" ? "≈ " : ""}${r.cost_usd.toFixed(4)} <span className="mut">{SOURCE[r.cost_source] ?? ""}</span></td>
            <td>{ENDED[r.ended_by] ?? r.ended_by}</td></tr>))}</tbody></table>}
    </details>
  );
}
