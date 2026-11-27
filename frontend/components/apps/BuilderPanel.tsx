"use client";
// Talk to the builder agent about this app. It plans first, then works only through the app_* tools: it writes files inside the app's folder (nothing else, nothing runs),
// and its changes arrive in the Changes tab for you to accept or reject (in a simple-mode app they apply at once and every change is a version you can undo).
// After each turn that changed the app, this panel waits for the preview to reload, asks the server to check the files and what the preview reported, and - if something is
// broken - sends the builder the report so it can fix it. That loop is bounded: the server counts failed checks in a row and says give_up after three.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, streamSse } from "@/lib/api";
import { AppRow, BuildPlan, builderMessage, BUILDER_ID, fixMessage, ModelInfo, ModelOption, Verify } from "@/lib/apps";
import PlanCard from "@/components/apps/PlanCard";

type Msg = { role: "user" | "bot" | "error" | "note"; text: string };
export type BuildStart = { prompt: string; attachments: string[]; failed?: string[] };
export type BuilderRequest = { id: number; text: string; label?: string };
type RunOpts = { attachments?: string[]; auto?: boolean; depth?: number; shown?: string };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const BEST = "best";

export default function BuilderPanel({ app, onChanged, start, onStarted, request, refreshKey = 0, settleMs = 2500 }:
  { app: AppRow; onChanged: () => void; start?: BuildStart | null; onStarted?: () => void; request?: BuilderRequest | null; refreshKey?: number; settleMs?: number }) {
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [options, setOptions] = useState<ModelOption[] | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [plan, setPlan] = useState<BuildPlan | null>(null);
  const [autoFix, setAutoFix] = useState(true);
  const [status, setStatus] = useState("");
  const [lastCheck, setLastCheck] = useState<Verify | null>(null);
  const abort = useRef<AbortController | null>(null);
  const session = useRef(app.session_id);
  const began = useRef(false);
  const lastRequest = useRef(0);
  const stopped = useRef(false);
  const autoRef = useRef(true);
  const runRef = useRef<(text: string, opts?: RunOpts) => Promise<void>>(async () => undefined);

  useEffect(() => {
    try { const v = localStorage.getItem("apps-autofix"); if (v === "off") { setAutoFix(false); autoRef.current = false; } } catch { /* no storage: keep the default */ }
  }, []);
  const loadModel = useCallback(() => Promise.all([api<ModelInfo>(`/api/apps/${app.id}/model`), api<{ options: ModelOption[] }>("/api/apps/models")])
    .then(([m, o]) => { setModel(m); setOptions(o.options); }).catch(() => { setModel({ saved: { mode: "best", provider: "", model: "" }, effective: { provider: "", label: "", kind: "", model: "", source: "none" } }); setOptions([]); }), [app.id]);
  useEffect(() => { loadModel(); }, [loadModel]);
  const loadPlan = useCallback(() => api<{ plan: BuildPlan | null }>(`/api/apps/${app.id}/plan`).then((r) => setPlan(r.plan)).catch(() => undefined), [app.id]);
  useEffect(() => { loadPlan(); }, [loadPlan, refreshKey]);

  const pickModel = async (value: string) => {
    const [provider, ...rest] = value.split("|");
    try {
      setModel(await api<ModelInfo>(`/api/apps/${app.id}/model`, { method: "PUT", body: JSON.stringify(value === BEST ? { mode: "best" } : { mode: "custom", provider, model: rest.join("|") }) }));
    } catch (e) { setMsgs((m) => [...m, { role: "error", text: (e as Error).message }]); }
  };
  const toggleAuto = (on: boolean) => { setAutoFix(on); autoRef.current = on; try { localStorage.setItem("apps-autofix", on ? "on" : "off"); } catch { /* no storage */ } };
  const clearPlan = async () => { try { await api(`/api/apps/${app.id}/plan`, { method: "DELETE" }); setPlan(null); } catch { /* the plan stays */ } };

  const run = useCallback(async (text: string, opts: RunOpts = {}): Promise<void> => {
    const { attachments = [], auto = false, depth = 0, shown } = opts;
    const raw = auto || shown !== undefined;                              // already a full message for the builder (a fix request, a picked element): sent as it is
    const provider = model?.effective.provider;
    if (!provider || !text.trim()) return;
    const ctl = new AbortController();
    abort.current = ctl;
    stopped.current = false;
    let changed = false;
    setBusy(true); setSteps([]); setStatus("");
    if (!auto) api(`/api/apps/${app.id}/verify/reset`, { method: "POST" }).catch(() => undefined);
    setMsgs((m) => [...m, auto ? { role: "note", text: `Fixing what the check found (try ${depth} of 3)…` } : { role: "user", text: (shown ?? text).trim() }, { role: "bot", text: "" }]);
    const patch = (fn: (t: string) => string) => setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.role === "bot") c[c.length - 1] = { ...l, text: fn(l.text) }; return c; });
    const fail = (message: string) => setMsgs((m) => [...m.filter((x, i) => !(i === m.length - 1 && x.role === "bot" && !x.text)), { role: "error", text: message }]);
    try {
      await streamSse("/api/chat/stream", { provider, agent: BUILDER_ID, message: raw ? text : builderMessage(app, text, attachments), session_id: session.current || undefined,
        ...(model?.saved.mode === "custom" && model.effective.model ? { model: model.effective.model } : {}) }, (e) => {
        if (e.type === "session" && e.session_id) session.current = e.session_id;
        else if (e.type === "text") patch((t) => t + e.delta);
        else if (e.type === "tool_call") setSteps((s) => [...s.slice(-5), String(e.name ?? e.tool ?? "tool")]);
        else if (e.type === "app") { changed = true; onChanged(); }
        else if (e.type === "refused") fail(e.detail?.message ?? "The request was refused.");
        else if (e.type === "error") fail(e.title ?? e.message ?? "The builder failed.");
      }, ctl.signal);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") stopped.current = true;
      else fail(e instanceof ApiError ? e.message : (e as Error).message);
    }
    abort.current = null;
    onChanged();
    if (changed && autoRef.current && !stopped.current) {
      setSteps([]); setStatus("Checking the preview…");
      await sleep(settleMs);
      let v: Verify | null = null;
      if (!stopped.current) { try { v = await api<Verify>(`/api/apps/${app.id}/verify`, { method: "POST" }); } catch { v = null; } }
      setStatus("");
      if (v) {
        setLastCheck(v);
        if (!v.ok && (v.give_up || depth >= v.max_rounds)) {
          setMsgs((m) => [...m, { role: "error", text: `I could not fix this by myself after ${v!.max_rounds} tries. What is still wrong:\n${v!.report}\nTell me what to change, or open the Files tab to look at it.` }]);
        } else if (!v.ok && !stopped.current) {
          await runRef.current(fixMessage(app, v), { auto: true, depth: depth + 1 });
          return;
        }
      }
    }
    setBusy(false); setSteps([]);
  }, [model, app, onChanged, settleMs]);
  useEffect(() => { runRef.current = run; }, [run]);

  useEffect(() => {
    if (!start || began.current || !model?.effective.provider) return;
    began.current = true;
    onStarted?.();
    run(start.prompt, { attachments: start.attachments });
  }, [start, model, run, onStarted]);
  useEffect(() => {
    if (!request || lastRequest.current === request.id || !model) return;
    lastRequest.current = request.id;
    if (busy) setMsgs((m) => [...m, { role: "note", text: "The builder is busy: ask again when it has finished." }]);
    else run(request.text, { shown: request.label ?? "Change request from the preview" });
  }, [request, model, busy, run]);

  const submit = () => { const t = input; setInput(""); run(t); };
  const ready = !!model?.effective.provider;
  const current = model?.saved.mode === "custom" ? `${model.saved.provider}|${model.saved.model}` : BEST;
  return (
    <div data-testid="builder-panel" className="builder">
      {model !== null && !ready && (
        <div className="small" role="alert" style={{ color: "var(--warn-text)" }}>No model provider is switched on and signed in, so the builder cannot answer. <a href="/settings/">Open Settings</a> to connect one.</div>
      )}
      {options && options.length > 0 && (
        <label className="small">Model for this app
          <select value={options.some((o) => `${o.provider}|${o.model}` === current) || current === BEST ? current : BEST} aria-label="Builder model" onChange={(e) => pickModel(e.target.value)}>
            <option value={BEST}>Best available (automatic){model?.saved.mode === "best" && model.effective.label ? ` — now ${model.effective.label}, ${model.effective.model}` : ""}</option>
            {options.map((o) => <option key={o.provider} value={`${o.provider}|${o.model}`} disabled={!o.ready}>{o.label} — {o.model}{o.ready ? "" : " (not signed in)"}</option>)}
          </select></label>
      )}
      <PlanCard plan={plan} onClear={clearPlan} />
      <div className="builder-log" role="log" aria-live="polite" aria-label="Builder conversation">
        {msgs.length === 0 && <div className="mut small">Tell the builder what to build or change. It plans first, builds in small steps, checks the preview and fixes what it finds.</div>}
        {msgs.map((m, i) => <div key={i} className={`msg-${m.role} builder-msg`} role={m.role === "error" ? "alert" : undefined}>{m.text || (busy && i === msgs.length - 1 ? "…" : "")}</div>)}
      </div>
      {busy && steps.length > 0 && <div className="small mut" aria-live="polite">Working: {steps[steps.length - 1]}</div>}
      {status && <div className="small mut" aria-live="polite" data-testid="builder-status">{status}</div>}
      {!busy && lastCheck && <div className="small" data-testid="last-check" style={{ color: lastCheck.ok ? "var(--ok)" : "var(--warn)" }}>{lastCheck.ok ? "✓ Checked: no problems found" : `Checked: ${lastCheck.problems.filter((p) => p.level === "error").length + lastCheck.console.length} problem(s) left`}</div>}
      {start?.failed && start.failed.length > 0 && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>Not attached: {start.failed.join("; ")}</div>}
      <div className="row" style={{ marginTop: 6, alignItems: "flex-end" }}>
        <textarea className="grow" rows={2} value={input} aria-label="Message to the builder" placeholder="Make the buttons bigger, or add a second page…" onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !busy) { e.preventDefault(); submit(); } }} />
        {busy ? <button onClick={() => { stopped.current = true; abort.current?.abort(); }}>Stop</button> : <button className="acc" disabled={!ready || !input.trim()} onClick={submit}>Send</button>}
      </div>
      <label className="check small"><input type="checkbox" checked={autoFix} onChange={(e) => toggleAuto(e.target.checked)} aria-label="Fix problems automatically" /> Check the preview after each change and fix problems automatically (up to 3 tries)</label>
    </div>
  );
}
