"use client";
// The preview of an app. A page is shown in an iframe that has NO same-origin rights (sandbox="allow-scripts" only) and is served from a separate port with a strict
// Content-Security-Policy, so it cannot reach the rest of Agent Studio. A server app runs in a container first (Settings > Apps must allow that).
// The preview server adds a small helper script to every page: it reports script errors to Agent Studio (shown under the frame, and read by the builder) and, when you press
// "Pick an element", tells this panel which element you clicked so you can say what to change about it.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { AppRow, Check, ConsoleEntry, consoleReport, PickedEl, RunInfo } from "@/lib/apps";
import InfoTip from "@/components/InfoTip";

type Device = "desktop" | "tablet" | "phone";
const WIDTH: Record<Device, string> = { desktop: "100%", tablet: "768px", phone: "390px" };

export default function PreviewPanel({ app, refreshKey, consoleKey = 0, onPicked, onFix }: { app: AppRow; refreshKey: number; consoleKey?: number; onPicked?: (el: PickedEl) => void; onFix?: (report: string) => void }) {
  const [run, setRun] = useState<RunInfo | null>(null);
  const [err, setErr] = useState("");
  const [frame, setFrame] = useState(0);
  const [logs, setLogs] = useState<string | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [device, setDevice] = useState<Device>("desktop");
  const [picking, setPicking] = useState(false);
  const [reports, setReports] = useState<ConsoleEntry[]>([]);
  const isStatic = app.type === "static";
  const started = useRef(false);
  const ref = useRef<HTMLIFrameElement>(null);
  const picked = useRef(onPicked);
  picked.current = onPicked;

  const start = useCallback(async () => {
    setBusy(true); setErr("");
    try { setRun(await api<RunInfo>(`/api/apps/${app.id}/run`, { method: "POST" })); setFrame((f) => f + 1); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }, [app.id]);
  const stop = async () => { try { setRun(await api<RunInfo>(`/api/apps/${app.id}/stop`, { method: "POST" })); } catch (e) { setErr((e as Error).message); } };
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (isStatic) start();
    else api<RunInfo>(`/api/apps/${app.id}/status`).then(setRun).catch((e) => setErr(e.message));
  }, [isStatic, start, app.id]);
  useEffect(() => { if (refreshKey) { setFrame((f) => f + 1); setPicking(false); } }, [refreshKey]);
  useEffect(() => { api<{ errors: ConsoleEntry[] }>(`/api/apps/${app.id}/console`).then((c) => setReports(c.errors.filter((e) => e.kind !== "console.warn"))).catch(() => setReports([])); }, [app.id, consoleKey, refreshKey]);
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (!ref.current || e.source !== ref.current.contentWindow || !e.data || typeof e.data !== "object") return;
      if ("asPicked" in e.data) { setPicking(false); picked.current?.(e.data.asPicked as PickedEl); }
      else if ("asPickCancelled" in e.data) setPicking(false);
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);

  const pick = (on: boolean) => { setPicking(on); ref.current?.contentWindow?.postMessage({ asPick: on }, "*"); };
  const showLogs = async () => { try { setLogs((await api<{ logs: string }>(`/api/apps/${app.id}/logs?tail=200`)).logs); } catch (e) { setErr((e as Error).message); } };
  const doCheck = async () => { try { setCheck(await api<Check>(`/api/apps/${app.id}/check`)); } catch (e) { setErr((e as Error).message); } };
  const enable = async (id: string) => {
    try { await api(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify({ api_access: true }) }); await doCheck(); } catch (e) { setErr((e as Error).message); }
  };
  const running = run?.status === "running" || run?.status === "ready";
  const serverNeedsSwitch = err.includes("Settings > Apps");
  return (
    <div data-testid="preview-panel">
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        {isStatic
          ? <button onClick={() => setFrame((f) => f + 1)} disabled={!run?.url}>Reload</button>
          : <button className="acc" disabled={busy} onClick={start}>{run?.status === "running" ? "Restart" : "Run"}</button>}
        {!isStatic && <button disabled={run?.status !== "running"} onClick={stop}>Stop</button>}
        {isStatic && onPicked && <button aria-pressed={picking} className={picking ? "acc" : ""} disabled={!running} onClick={() => pick(!picking)} title="Click an element in the preview, then say what to change">{picking ? "Click an element…" : "Pick an element"}</button>}
        {run?.url && <a className="chip small" href={run.url} target="_blank" rel="noopener noreferrer">Open in a new tab</a>}
        <span className="grow" />
        <span role="group" aria-label="Preview size" className="row" style={{ gap: 2 }}>
          {(["desktop", "tablet", "phone"] as Device[]).map((d) => <button key={d} className="chip small" aria-pressed={device === d} onClick={() => setDevice(d)}>{d[0].toUpperCase() + d.slice(1)}</button>)}
        </span>
        <span className="chip small" data-testid="run-status">{run ? (run.status === "ready" ? "ready" : run.status) : "checking…"}</span><InfoTip id={isStatic ? "apps" : "app-server"} />
      </div>
      {run?.detail && <div className="small mut" style={{ marginTop: 4 }}>{run.detail}</div>}
      {err && <div className="err small" role="alert">{err}{serverNeedsSwitch && <> <a href="/settings/">Open Settings</a></>}</div>}
      {run?.url && running ? (
        <iframe ref={ref} key={frame} title="App preview" src={run.url} sandbox="allow-scripts" className="apps-frame" referrerPolicy="no-referrer" style={{ maxWidth: WIDTH[device], display: "block", marginLeft: "auto", marginRight: "auto" }} />
      ) : (
        <div className="apps-frame apps-frame-empty mut small">{isStatic ? "Starting the preview…" : "Press Run to start this app in a container."}</div>
      )}
      {reports.length > 0 && (
        <div className="preview-errors" role="alert" data-testid="preview-errors">
          <b className="small">The preview reported {reports.length} problem{reports.length === 1 ? "" : "s"}</b>
          <ul className="small" style={{ margin: "4px 0", paddingLeft: 18 }}>
            {reports.slice(0, 5).map((e, i) => <li key={i}>{e.text}{e.source ? <span className="mut"> ({e.source}{e.line ? `:${e.line}` : ""})</span> : null}{e.count > 1 ? ` ×${e.count}` : ""}</li>)}
          </ul>
          {onFix && <button className="chip small" onClick={() => onFix(consoleReport(reports))}>Ask the builder to fix this</button>}
        </div>
      )}
      <div className="row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
        <button onClick={doCheck}>Check the app</button>
        {!isStatic && <button onClick={showLogs}>Show logs</button>}
      </div>
      {check && (
        <ul className="small" aria-label="Check results" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {check.problems.length === 0 && <li>No problems found.</li>}
          {check.problems.map((p, i) => <li key={i} style={{ color: p.level === "error" ? "var(--bad)" : p.level === "warn" ? "var(--warn)" : undefined }}><b>{p.level}</b>: {p.text}</li>)}
          {check.agents.filter((a) => a.exists && !a.api_access).map((a) => <li key={a.id}><button className="chip small" onClick={() => enable(a.id)}>Turn on API access for {a.id}</button></li>)}
        </ul>
      )}
      {logs !== null && <pre className="apps-logs" aria-label="App logs">{logs || "(no output yet)"}</pre>}
    </div>
  );
}
