"use client";
// Guard models that fetch themselves: a missing Ollama model is downloaded in the background (Try it, saving a profile, live chat).
// PullRow draws one download (progress bar, size, speed, licence, cancel / try again); GuardDownloads is the strip at the top of the
// Guardrails card that lists every download that is running or just finished.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type PullState = {
  key: string; model: string; base: string; where: string; status: "downloading" | "done" | "error" | "cancelled";
  completed: number; total: number; percent: number | null; speed_bps: number | null; eta_s: number | null;
  size_hint_gb: number | null; licence: string; message: string; error_kind: string; profile?: string;
};

export const POLL_MS = process.env.NODE_ENV === "test" ? 5 : 1500;
const IDLE_POLL_MS = process.env.NODE_ENV === "test" ? 60_000 : 8000;        // idle: still look now and then, so a download a chat started shows up

const gb = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
const eta = (s: number) => (s >= 120 ? `about ${Math.round(s / 60)} min left` : `${Math.max(1, s)} s left`);

export function PullRow({ s, onCancel, onRetry }: { s: PullState; onCancel?: () => void; onRetry?: () => void }) {
  const size = s.total ? gb(s.total) : s.size_hint_gb ? `about ${s.size_hint_gb} GB` : "a few GB";
  return (
    <div className="small" data-testid="guard-pull" style={{ padding: "6px 0" }}>
      {s.status === "downloading" && <>
        <div><b>Downloading {s.model}</b> ({size}, {s.where})
          {s.percent != null ? ` · ${s.percent}%` : " · starting…"}{s.speed_bps ? ` · ${gb(s.speed_bps)}/s` : ""}{s.eta_s ? ` · ${eta(s.eta_s)}` : ""}</div>
        <div role="progressbar" aria-label={`Downloading ${s.model}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={s.percent ?? 0}
          style={{ height: 6, background: "var(--line, #ddd)", borderRadius: 3, margin: "4px 0", overflow: "hidden" }}>
          <div style={{ width: `${s.percent ?? 2}%`, height: "100%", background: "var(--acc, #c15f3c)" }} />
        </div>
        {s.licence && <div className="mut">Licence: {s.licence}. It is downloaded once and kept on {s.where === "on this PC" ? "this PC" : "that server"}.</div>}
        {onCancel && <button className="small" onClick={onCancel}>Cancel download</button>}
      </>}
      {s.status === "done" && <div><span className="pill ok">ready</span> {s.model} is downloaded, so checks with it now run.</div>}
      {(s.status === "error" || s.status === "cancelled") && <>
        <div role="alert"><span className={`pill ${s.status === "error" ? "bad" : "warn"}`}>{s.status === "error" ? "download failed" : "cancelled"}</span> <b>{s.model}</b>: {s.message}</div>
        <div className="row" style={{ marginTop: 4 }}>
          {s.error_kind === "ollama_down" && <a href="/setup/">Open Setup to start Ollama</a>}
          {onRetry && <button className="small" onClick={onRetry}>Try again</button>}
        </div>
      </>}
    </div>
  );
}

export default function GuardDownloads({ reload }: { reload: number }) {
  const [pulls, setPulls] = useState<PullState[]>([]);
  const load = useCallback(() => api<{ pulls: PullState[] }>("/api/guardrails/pulls").then((r) => { setPulls(r.pulls); return r.pulls; }).catch(() => [] as PullState[]), []);
  useEffect(() => { load(); }, [load, reload]);
  const busy = pulls.some((p) => p.status === "downloading");
  useEffect(() => {
    const t = setInterval(load, busy ? POLL_MS * 1.3 : IDLE_POLL_MS);
    return () => clearInterval(t);
  }, [busy, load]);
  if (!pulls.length) return null;
  const cancel = (p: PullState) => api("/api/guardrails/pulls/cancel", { method: "POST", body: JSON.stringify({ base: p.base, model: p.model }) }).then(load).catch(() => {});
  const retry = (p: PullState) => api("/api/guardrails/pulls/retry", { method: "POST", body: JSON.stringify({ profile: p.profile }) }).then(load).catch(() => {});
  return (
    <div className="cat-panel" data-testid="guard-downloads" style={{ marginBottom: 8 }}>
      <div className="lbl">Guard models</div>
      {pulls.map((p) => <PullRow key={p.key} s={p} onCancel={() => cancel(p)} onRetry={p.profile ? () => retry(p) : undefined} />)}
    </div>
  );
}
