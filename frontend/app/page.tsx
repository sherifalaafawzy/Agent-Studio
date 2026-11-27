"use client";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError, AUTO_ID, AutoInfo, AutoRoute, followSse, Provider, streamSse, usd } from "@/lib/api";
import Markdown from "@/components/Markdown";
import RouteNote from "@/components/AutoRoute";
import { MemoryNote, type MemEvent } from "@/components/MemoryUI";
import GuardrailNote, { type GuardEvent } from "@/components/GuardrailNote";
import ModelSelect from "@/components/ModelSelect";
import { toast } from "@/components/Nav";
import ContextMeter, { useContext as useCtx } from "@/components/ContextMeter";
import FolderPicker from "@/components/FolderPicker";
import { ListenButton, MicButton, useVoice } from "@/components/Voice";
import Link from "next/link";
import { buildLanes, fmtDur, RunThread, RunTimeline, type Run as TRun } from "@/components/Runs";
import { IAgents, IArrowUp, ICheck, IChevron, ICopy, IFolder, ISave, IStop, ITimeline, ITool, IX, Spark } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";
import UiBlock from "@/components/uikit/UiBlock";
import { PlainRichContext } from "@/components/rich/PlainContext";
import { ArtifactList, KeepContext, ReplyContext, StatePanel, type RunState } from "@/components/RichBlocks";
import type { Artifact } from "@/lib/artifacts";
import { CanvasContext, type OpenCanvas } from "@/components/rich/BlockActions";
import { DEFAULT_WIDTH, MAX_WIDTH, MIN_WIDTH, type Canvas } from "@/lib/canvas";
import { usePageContext } from "@/lib/pagectx";

const CanvasPanel = lazy(() => import("@/components/canvas/CanvasPanel"));   // the editor is only loaded when a canvas is opened

type Step = { agent: string; run_id?: string; type: "call" | "result"; tool: string; args?: string; result?: string };
type Call = { agent: string; run_id?: string; provider: string; model: string; cost_usd: number; input_tokens: number; output_tokens: number };
type Run = {
  run_id: string; parent: string | null; agent: string; spawned: boolean; definition?: any; task: string; status: string;
  steps: Step[]; texts: string[]; calls: Call[]; result?: string | null; cost_usd: number; started?: number; ended?: number | null; instruction?: string;
};
type Msg = { role: "user" | "bot" | "refused" | "summary" | "handoff" | "fallback"; text: string; suggestions?: { provider: string; label: string; model: string }[]; title?: string; hint?: string; raw?: string; retryable?: boolean; meta?: string; thinking?: string; thinkStart?: number; thinkSecs?: number; steps?: Step[]; calls?: Call[]; warnings?: string[]; runs?: Run[]; status?: string; streaming?: boolean; route?: AutoRoute; memory?: MemEvent[]; guardrails?: GuardEvent[]; artifacts?: Artifact[]; uis?: any[] };
type Agent = { id: string; name: string; tier: string; description: string; sub_agents: string[]; can_spawn?: boolean; agui?: { components?: boolean; endpoint?: boolean } };

const metaLine = (calls: Call[], usage: any, billing?: any) =>
  `${calls[0]?.model ?? ""}${calls.length ? " · " : ""}${calls.length} call${calls.length === 1 ? "" : "s"} · ${usage?.input_tokens ?? 0}→${usage?.output_tokens ?? 0} tok · ${usd(usage?.cost_usd ?? 0)}` +
  (billing ? ` · ${usd(billing.spent_usd)} of ${usd(billing.cap_usd)} cap used` : "");


/** The shared state of a finished turn (agents, totals, files), in the same shape the AG-UI stream sends. */
const runState = (m: Msg): RunState => ({
  agents: (m.runs ?? []).map((r) => ({ id: r.run_id, name: r.agent, status: r.status, parent: r.parent, cost: r.cost_usd ?? 0 })),
  totals: { model_calls: m.calls?.length ?? 0, tool_calls: (m.steps ?? []).filter((x) => x.type === "call").length, cost_usd: (m.calls ?? []).reduce((a, c) => a + (c.cost_usd ?? 0), 0) },
  artifacts: m.artifacts?.length ?? 0,
});

function ErrorNotice({ m, onRetry, onTry }: { m: Msg; onRetry?: () => void; onTry?: (p: string, model: string) => void }) {
  const [open, setOpen] = useState(false);
  const title = m.title ?? "Stopped";
  return (
    <div className="err-card" role="alert">
      <div className="err-head"><span className="err-dot">!</span><b>{title}</b></div>
      {m.hint && <div className="err-hint">{m.hint}</div>}
      {!m.hint && m.text && m.text !== title && <div className="err-hint">{m.text}</div>}
      {m.meta && <div className="err-hint">{m.meta}</div>}
      {onTry && m.suggestions && m.suggestions.length > 0 && <div className="err-try">
        <span className="small mut">Try another model:</span>
        {m.suggestions.map((s) => <button key={s.provider + s.model} className="chip small" onClick={() => onTry(s.provider, s.model)}>{s.label} · {s.model}</button>)}
      </div>}
      <div className="err-actions">
        {onRetry && <button className="acc small" onClick={onRetry}>Try again</button>}
        {m.raw && <button className="ghost small" onClick={() => setOpen((v) => !v)}>{open ? "Hide details" : "Details"}</button>}
      </div>
      {open && m.raw && <pre className="err-raw">{m.raw}</pre>}
    </div>
  );
}

function SummaryDivider({ text, meta }: { text: string; meta?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sum-div">
      <div className="sum-line"><span>Context summarised{meta ? ` · ${meta}` : ""}</span></div>
      <button className="ghost small" onClick={() => setOpen((v) => !v)} style={{ display: "block", margin: "6px auto" }}>{open ? "Hide the summary" : "Show the summary the model resumes from"}</button>
      {open && <div className="sum-body"><Markdown text={text} /></div>}
    </div>
  );
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return <button className="act" title="Copy" onClick={() => navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1200); })}>
    {ok ? <ICheck size={14} /> : <ICopy size={14} />}{ok ? "Copied" : "Copy"}</button>;
}

const SUGGESTIONS = [
  "Explore this repo and summarise its structure",
  "Find and fix a failing test",
  "Review the latest change for bugs",
  "Plan a feature, then build it with sub-agents",
];

function ChatPage() {
  const sp = useSearchParams();
  const router = useRouter();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [pid, setPid] = useState("");
  const [auto, setAuto] = useState<AutoInfo | null>(null);            // Auto mode: the router picks the provider and model per message
  const [liveRoute, setLiveRoute] = useState<AutoRoute | null>(null);
  const [agent, setAgent] = useState("");
  const [model, setModel] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [title, setTitle] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<string | undefined>();
  const [offline, setOffline] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selLane, setSelLane] = useState("");
  const [showPanel, setShowPanel] = useState(false);
  const [canvasId, setCanvasId] = useState("");                       // the canvas open beside the chat ("" = none)
  usePageContext("chat", () => ({ agent: agent || "plain chat", chatId: session ?? null, canvasOpen: !!canvasId }));
  const [canvasTick, setCanvasTick] = useState(0);                    // bumped when an agent changes it, so the panel reloads
  const [pane, setPane] = useState<"chat" | "canvas">("chat");        // on a phone only one of the two shows at a time
  const [canvasW, setCanvasW] = useState(() => { try { return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Number(localStorage.getItem("canvas_w")) || DEFAULT_WIDTH)); } catch { return DEFAULT_WIDTH; } });
  const [pop, setPop] = useState<null | "provider" | "workspace">(null);          // which composer popover is open (one at a time)
  const [note, setNote] = useState("");
  const [asks, setAsks] = useState<any[]>([]);
  const [notes, setNotes] = useState<{ id: string; text: string; mode: string }[]>([]);   // sent during the run, not yet read
  const [noteMode, setNoteMode] = useState<"queue" | "steer">("queue");             // permission / question / plan requests from external agents
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const voice = useVoice();
  const { info: ctx, reload: reloadCtx } = useCtx(session, `${msgs.length}-${busy}`);
  const [autoReadIdx, setAutoReadIdx] = useState(-1);   // the answer that just finished (read aloud if enabled)
  const [, setTick] = useState(0);
  const jobRef = useRef<string | null>(null);
  const stopped = useRef(false);   // the user pressed Stop before the run had a job id to cancel
  const seqRef = useRef(-1);
  const end = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const abort = useRef<AbortController | null>(null);
  const liveRuns = useRef<Record<string, Run>>({});
  const sessionRef = useRef<string | undefined>(undefined);
  const autoRef = useRef<AutoInfo | null>(null);
  const usedAuto = useRef(false);       // the open chat's last answer was routed by Auto (decided before the Auto info may have loaded)
  const chatPid = useRef(false);        // an opened chat has chosen the provider: the load-time default must not overwrite it
  const msgsRef = useRef<Msg[]>([]);
  sessionRef.current = session;
  msgsRef.current = msgs;

  useEffect(() => {
    Promise.all([api<Provider[]>("/api/providers"), api<Agent[]>("/api/agents"), api<AutoInfo>("/api/auto").catch(() => null)]).then(([p, a, au]) => {
      setProviders(p); setAgents(a); setOffline(false); setAuto(au); autoRef.current = au;
      const first = p.find((x) => x.enabled);
      const start = au?.available ? AUTO_ID : first?.id;                      // Auto is the default whenever it can work
      if (start) setPid((cur) => (sessionRef.current || chatPid.current ? (usedAuto.current && au?.available ? AUTO_ID : cur) : start));
    }).catch(() => setOffline(true));
  }, []);
  useEffect(() => {                                   // a popover closes on a click outside it, or Escape (the folder browser's dialog counts as inside)
    if (!pop) return;
    const down = (e: MouseEvent) => { const t = e.target as Element | null; if (!t?.closest("[data-pop-root], .backdrop")) setPop(null); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setPop(null); };
    document.addEventListener("mousedown", down); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [pop]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);
  useEffect(() => { if (!busy) return; const t = setInterval(() => setTick((x) => x + 1), 1000); return () => clearInterval(t); }, [busy]);
  useEffect(() => { const t = ta.current; if (t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 260) + "px"; } }, [input]);

  const prov = providers.find((p) => p.id === pid);
  const isAuto = pid === AUTO_ID;
  const canSend = isAuto || !!prov?.enabled;                              // Auto is only ever selected while it is available
  const ag = agents.find((a) => a.id === agent);
  const plainRich = ag?.agui?.components === false;                         // this agent has AG-UI components switched off: its answers stay plain Markdown
  const announce = () => window.dispatchEvent(new Event("as:sessions"));

  const newChat = useCallback(() => {
    abort.current?.abort(); setMsgs([]); setSession(undefined); setTitle(""); setRuns([]); setBusy(false); setNote(""); setShowPanel(false); setAsks([]); jobRef.current = null; setLiveRoute(null);
    setTimeout(() => ta.current?.focus(), 0);
  }, []);

  const openSession = useCallback(async (id: string) => {
    abort.current?.abort(); setBusy(false); setNote("");
    try {
      const r = await api<{ session: any; messages: any[]; active_job: any }>(`/api/chat/sessions/${id}`);
      const lastRoute = [...r.messages].reverse().find((m) => m.role === "bot")?.meta?.route;      // the chat used Auto: keep using it
      setLiveRoute(null); chatPid.current = true; usedAuto.current = !!lastRoute;
      setSession(id); setTitle(r.session.title); setPid(lastRoute && autoRef.current?.available ? AUTO_ID : r.session.provider); setAgent(r.session.agent ?? ""); setModel(r.session.model ?? ""); setWorkspace(r.session.workspace ?? "");
      const ms: Msg[] = r.messages.map((m) => {
        const mt = m.meta ?? {};
        if (m.role === "handoff") return { role: "handoff" as const, text: m.text };
        if (m.role === "bot" && mt.error) return { role: "refused" as const, text: mt.error.title, title: mt.error.title, hint: mt.error.hint,
          raw: mt.error.raw, retryable: !!mt.error.retryable, suggestions: mt.error.suggestions, meta: metaLine(mt.calls ?? [], mt.usage), steps: mt.steps, runs: mt.runs };
        if (m.role === "bot" && mt.no_answer) return { role: "refused" as const, text: "No answer", title: "The model finished without an answer",
          hint: "It ran its steps but didn't write a reply, even after one retry. Try again, or ask it to summarise what it found so far.", retryable: true,
          meta: metaLine(mt.calls ?? [], mt.usage), steps: mt.steps, runs: mt.runs };
        if (m.role === "summary") return { role: "summary" as const, text: m.text,
          meta: mt.covered ? `${mt.covered} earlier messages condensed` : undefined };
        return m.role === "bot"
          ? { role: "bot", text: m.text, thinking: mt.thinking ?? undefined, status: mt.status, steps: mt.steps, calls: mt.calls, warnings: mt.warnings, runs: mt.runs, route: mt.route ?? undefined, memory: mt.memory ?? undefined, guardrails: mt.guardrails ?? undefined, artifacts: mt.artifacts ?? undefined, uis: mt.uis ?? undefined,
              meta: metaLine(mt.calls ?? [], mt.usage) + (mt.status && mt.status !== "ok" ? ` · ${mt.status}` : "") }
          : { role: m.role, text: m.text };
      });
      setMsgs(ms);
      setRuns([...ms].reverse().find((m) => m.runs?.length)?.runs ?? []); setAsks([]);
      if (r.active_job) attach(r.active_job.job_id, r.active_job.started);
    } catch { newChat(); setNote("That chat no longer exists."); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newChat]);

  // URL drives which chat is open: /?s=<id> opens one, /?new=<t> starts fresh.
  const sParam = sp.get("s"), newParam = sp.get("new"), canvasParam = sp.get("canvas");
  useEffect(() => {                                                // /?canvas=<id> opens that canvas; /?canvas=latest the newest one (or a blank one)
    if (!canvasParam) return;
    if (canvasParam !== "latest") { setCanvasId(canvasParam); setPane("canvas"); return; }
    api<Canvas[]>("/api/canvas?limit=1").then((l) => { if (l.length) { setCanvasId(l[0].id); setPane("canvas"); } else void openInCanvas({ title: "Untitled", type: "markdown", content: "" }); }).catch((e) => toast((e as Error).message, "err"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasParam]);
  useEffect(() => { if (sParam && sParam !== sessionRef.current) openSession(sParam); }, [sParam, openSession]);
  useEffect(() => { if (newParam) newChat(); }, [newParam, newChat]);

  function onEvent(e: any) {
    const upd = () => setRuns(Object.values(liveRuns.current));
    if (typeof e.seq === "number") seqRef.current = e.seq;
    switch (e.type) {
      case "job": jobRef.current = e.job_id; break;
      case "external_text": { const r = liveRuns.current[e.run_id]; if (r) { r.texts = [(r.texts[0] ?? "") + e.delta]; } upd(); break; }
      case "external_ask": setAsks((a) => [...a.filter((x) => x.request_id !== e.request_id), e]); setShowPanel(true); break;
      case "external_resolved": setAsks((a) => a.filter((x) => x.request_id !== e.request_id)); break;
      case "note_delivered":
        setNotes((n) => n.filter((x) => x.id !== e.note_id));
        setMsgs((m) => { const c = [...m]; c.splice(Math.max(0, c.length - 1), 0, { role: "user", text: e.text, meta: e.mode === "steer" ? "your steer note · read now" : "your note · read at the next step" }); return c; });
        break;
      case "note_retracted": setNotes((n) => n.filter((x) => x.id !== e.note_id)); break;
      case "guardrail":                                         // a safety check flagged or blocked something: shown under the answer as it happens
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.streaming) c[c.length - 1] = { ...l, guardrails: [...(l.guardrails ?? []), e] }; return c; });
        break;
      case "canvas":                                             // an agent opened or changed the canvas: show it and refresh it
        setCanvasId(e.canvas_id); setCanvasTick((t) => t + 1); if (e.action === "open") setPane("canvas");
        break;
      case "ui":                                                 // a chart, map or card the agent showed (render_ui)
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.streaming) c[c.length - 1] = { ...l, uis: [...(l.uis ?? []), e.spec] }; return c; });
        break;
      case "artifact":                                           // a file the agent produced: shown under the answer as it happens
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.streaming) c[c.length - 1] = { ...l, artifacts: [...(l.artifacts ?? []), e.artifact] }; return c; });
        break;
      case "memory":                                             // what the agent recalled / remembered: shown under the answer as it happens
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.streaming) c[c.length - 1] = { ...l, memory: [...(l.memory ?? []), { ...e, type: e.memory_type }] }; return c; });
        break;
      case "route":
        setLiveRoute(e);
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1]; if (l?.streaming) c[c.length - 1] = { ...l, route: e }; return c; });
        break;
      case "fallback":
        setMsgs((m) => { const c = [...m]; c.splice(Math.max(0, c.length - 1), 0, { role: "fallback", text: `${e.from.provider}${e.from.model ? "/" + e.from.model : ""} → ${e.to.provider}${e.to.model ? "/" + e.to.model : ""}`, meta: e.reason }); return c; });
        break;
      case "steered":
        setMsgs((m) => { const c = [...m]; const l = c[c.length - 1];
          if (l?.streaming) c[c.length - 1] = { ...l, streaming: false, meta: "interrupted by your steer note" };
          return [...c, { role: "bot", text: "", streaming: true }]; });
        break;
      case "end":
        jobRef.current = null; setAsks([]); setNotes([]);
        if (e.status !== "done" && sessionRef.current) { const sid = sessionRef.current; setTimeout(() => reloadKeepingNotice(sid), 300); }
        break;
      case "session":
        if (!sessionRef.current) { setSession(e.session_id); sessionRef.current = e.session_id; router.replace(`/?s=${e.session_id}`, { scroll: false }); announce(); }
        break;
      case "agent_start": liveRuns.current[e.run_id] = { ...e, status: "running", steps: [], texts: [], calls: [], cost_usd: 0 }; upd(); if (e.run_id !== "root") setShowPanel(true); break;
      case "tool_call": case "tool_result": liveRuns.current[e.run_id]?.steps.push({ ...e, type: e.type === "tool_call" ? "call" : "result" }); upd(); break;
      case "agent_text": liveRuns.current[e.run_id]?.texts.push(e.text); upd(); break;
      case "model_call": { const r = liveRuns.current[e.run_id]; if (r) { r.calls.push(e); r.cost_usd += e.cost_usd; } upd(); break; }
      case "agent_end": { const r = liveRuns.current[e.run_id]; if (r) { r.status = e.status; r.result = e.result; r.cost_usd = e.cost_usd; r.ended = e.ended; } upd(); break; }
      case "thinking": setMsgs((m) => { const c = [...m]; const l = c[c.length - 1];
        if (l?.streaming) c[c.length - 1] = { ...l, thinking: (l.thinking ?? "") + e.delta, thinkStart: l.thinkStart ?? Date.now() }; return c; }); break;
      case "text": setMsgs((m) => { const c = [...m]; const l = c[c.length - 1];
        if (l?.streaming) c[c.length - 1] = { ...l, text: l.text + e.delta, thinkSecs: l.thinkSecs ?? (l.thinkStart ? (Date.now() - l.thinkStart) / 1000 : undefined) }; return c; }); break;
      case "done":
        setMsgs((m) => { setAutoReadIdx(m.length - 1); return m; });
        setMsgs((m) => { const l = m[m.length - 1]; const secs = l?.thinkSecs ?? (l?.thinkStart ? (Date.now() - l.thinkStart) / 1000 : undefined);
          const noAnswer = !e.reply?.trim();
          return [...m.slice(0, -1), noAnswer
            ? { role: "refused" as const, text: "No answer", title: "The model finished without an answer",
                hint: "It ran its steps but didn't write a reply, even after one retry for the answer. Try again, or ask it to summarise what it found so far.",
                retryable: true, meta: metaLine(e.model_calls, e.usage, e.billing) + " · the work it did is still in the Agents panel",
                steps: e.steps, runs: e.runs }
            : { role: "bot", text: e.reply, meta: metaLine(e.model_calls, e.usage, e.billing), steps: e.steps, calls: e.model_calls, warnings: e.warnings, runs: e.runs, route: e.route ?? l?.route, memory: e.memory?.length ? e.memory : l?.memory, guardrails: e.guardrails?.length ? e.guardrails : l?.guardrails, artifacts: e.artifacts?.length ? e.artifacts : l?.artifacts, uis: e.uis?.length ? e.uis : l?.uis,
            thinking: e.thinking ?? l?.thinking, thinkSecs: secs },
          ...(e.halted ? [{ role: "refused" as const, text: "Turn stopped early: " + e.halted.message, meta: "Partial progress above is real; nothing further was sent to any provider." }] : [])]; });
        setRuns(e.runs ?? []);
        break;
      case "refused": setMsgs((m) => [...m.slice(0, -1), { role: "refused", text: e.detail.message,
        meta: `Projected ${usd(e.detail.projected_usd)} · remaining ${usd(e.detail.remaining_usd)} · nothing was sent to the provider` }]); break;
      case "error": setMsgs((m) => [...m.slice(0, -1), { role: "refused", text: e.title ?? e.message, title: e.title, hint: e.hint, raw: e.raw, retryable: e.retryable ?? false, suggestions: e.suggestions }]); break;
    }
  }

  async function reloadKeepingNotice(sid: string) {
    // a stopped / failed run: show what was saved, then the reason
    const notice = msgsRef.current[msgsRef.current.length - 1];
    await openSession(sid);      // a failure is saved with the chat now, so it is shown from the reloaded history
    void notice;
  }

  async function pump(open: (signal: AbortSignal) => Promise<void>) {
    const ac = new AbortController(); abort.current = ac;
    for (let attempt = 0; ; attempt++) {
      try {
        await open(ac.signal);
        return;
      } catch (e) {
        if ((e as any)?.name === "AbortError") {                              // we detached (switched chat): the job keeps running
          if (stopped.current) {                                              // ...or the user stopped a run that had no job id yet: finish the bubble
            stopped.current = false;
            setMsgs((m) => { const l = m[m.length - 1]; return l?.streaming ? [...m.slice(0, -1), { ...l, streaming: false, text: l.text || "Stopped." }] : m; });
          }
          return;
        }
        if (e instanceof ApiError && e.status === 402) { setMsgs((m) => [...m.slice(0, -1), { role: "refused", text: e.detail.message }]); return; }
        if (e instanceof ApiError || !jobRef.current || attempt >= 30) { setMsgs((m) => [...m.slice(0, -1), { role: "refused", text: (e as Error).message }]); return; }
        setNote("Connection lost — the run continues on the server; reconnecting…");
        await new Promise((r) => setTimeout(r, Math.min(1000 * (attempt + 1), 5000)));
        const jid = jobRef.current;
        open = (signal) => followSse(`/api/chat/jobs/${jid}/events?after=${seqRef.current}`, onEvent, signal).then(() => setNote(""));
      }
    }
  }

  async function send(textArg?: string, use?: { provider: string; model: string }) {
    const text = (textArg ?? input).trim();
    if (busy) {
      if (!text) return;
      setInput("");
      const jid = jobRef.current;
      if (!jid) { toast("The run is still starting - try again in a moment", "warn"); setInput(text); return; }
      try {
        const n = await api<{ id: string }>(`/api/chat/jobs/${jid}/notes`, { method: "POST", body: JSON.stringify({ text, mode: noteMode }) });
        setNotes((x) => [...x, { id: n.id, text, mode: noteMode }]);
      } catch (e) { toast((e as Error).message, "warn"); }
      return;
    }
    if (!text || (!use && !canSend)) return;   // keyboard path must match the disabled Send button
    stopped.current = false;
    setInput(""); setBusy(true); setNote(""); setPop(null); liveRuns.current = {}; setRuns([]); setAsks([]); seqRef.current = -1; jobRef.current = null; setLiveRoute(null);
    setStartedAt(Date.now());
    if (!title) setTitle(text.slice(0, 60));
    setMsgs((m) => [...m, { role: "user", text }, { role: "bot", text: "", streaming: true }]);
    try {
      await pump((signal) => streamSse("/api/chat/stream", { provider: use?.provider ?? pid, agent: agent || undefined, model: use ? use.model : (isAuto ? undefined : (model || undefined)), workspace: workspace || undefined, message: text, session_id: session },
        onEvent, signal));
    } finally { setBusy(false); abort.current = null; announce(); }
  }

  async function attach(jobId: string, started: number) {
    // re-attach to a run that is still going (opened from the sidebar, another tab, after a reload…): replay everything
    liveRuns.current = {}; setRuns([]); seqRef.current = -1; jobRef.current = jobId; setBusy(true); setStartedAt(started * 1000);
    setMsgs((m) => [...m, { role: "bot", text: "", streaming: true }]);
    try { await pump((signal) => followSse(`/api/chat/jobs/${jobId}/events?after=-1`, onEvent, signal)); }
    finally { setBusy(false); abort.current = null; announce(); }
  }

  async function retractNote(id: string) {
    const jid = jobRef.current;
    setNotes((x) => x.filter((n) => n.id !== id));
    if (!jid) return;
    try { await api(`/api/chat/jobs/${jid}/notes/${id}`, { method: "DELETE" }); }
    catch { toast("That note was already read by the agent", "warn"); }
  }

  async function saveAnswer(m: Msg) {
    const h = m.text.match(/^#{1,3}\s+(.+)$/m)?.[1] ?? m.text.split(String.fromCharCode(10))[0];
    const name = `${h.replace(/[^\w .-]+/g, "").trim().slice(0, 50) || "answer"}.md`;
    try { await api("/api/artifacts", { method: "POST", body: JSON.stringify({ name, content: m.text, agent: agent || "", session_id: session ?? "" }) }); toast("Kept on the Artifacts page"); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  async function stop() {
    const jid = jobRef.current;
    if (jid) await api(`/api/chat/jobs/${jid}/cancel`, { method: "POST" }).catch(() => abort.current?.abort());
    else { stopped.current = true; abort.current?.abort(); }
  }

  async function answer(ask: any, body: any) {
    try { await api(`/api/acp/respond/${ask.request_id}`, { method: "POST", body: JSON.stringify(body) }); setAsks((a) => a.filter((x) => x.request_id !== ask.request_id)); }
    catch (e) { setNote("⚠ " + (e as Error).message); }
  }

  const lanes = buildLanes(runs as unknown as TRun[]);
  const lane = lanes.find((l) => l.key === selLane) ?? lanes[lanes.length - 1];
  async function saveRun(r: TRun) {
    const id = prompt("Save this agent permanently. Id (a-z, 0-9, _):", (r.definition?.name ?? r.agent).toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 28));
    if (!id) return;
    try {
      await api("/api/agents", { method: "POST", body: JSON.stringify({ id, name: r.definition?.name ?? id, description: r.task.slice(0, 120), instruction: r.definition?.instruction ?? r.instruction ?? "",
        tier: r.definition?.tier ?? "flash", tools: r.definition?.tools ?? [], mcp: r.definition?.mcp ?? [] }) });
      setNote(`Saved agent “${id}” — it's now in Agent Studio.`);
    } catch (e) { setNote("⚠ " + (e as Error).message); }
  }
  const subCount = runs.filter((r) => r.run_id !== "root").length;
  const liveActivity = (() => {
    const all = Object.values(liveRuns.current);
    for (let i = all.length - 1; i >= 0; i--) { const r = all[i]; const s = [...r.steps].reverse().find((x) => x.type === "call"); if (r.status === "running" && s) return `${r.agent} → ${s.tool}`; }
    const sub = all.filter((r) => r.run_id !== "root" && r.status === "running").pop();
    return sub ? `${sub.agent} is working` : "Thinking";
  })();

  const composer = (
    <div className="composer-wrap">
      <div className="composer" data-tour="composer">
        {notes.length > 0 && <div className="note-chips">{notes.map((n) => (
          <span key={n.id} className={`note-chip ${n.mode}`} title={n.mode === "steer" ? "Steer: read now, interrupts the current step" : "Queued: read at the agent's next step"}>
            <b>{n.mode === "steer" ? "⚡ steer" : "⏳ queued"}</b> {n.text.length > 90 ? n.text.slice(0, 90) + "…" : n.text}
            <button onClick={() => retractNote(n.id)} title="Retract (only works until it is read)" aria-label="Retract note">✕</button></span>))}</div>}
        <textarea ref={ta} rows={1} className="composer-input" value={input} autoFocus aria-label="Message"
          placeholder={busy ? "Add a note for the agent — it reads it during the run" : msgs.length ? "Reply to the agent…" : "What should we work on?"}
          onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <div className="composer-bar">
          <div className="composer-left">
            <label className="pill-select" title="Agent" data-tour="agent-select">
              <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
                <option value="">Plain chat</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}{a.sub_agents.length ? ` · ${a.sub_agents.length} sub` : ""}{a.can_spawn ? " · builds agents" : ""}</option>)}
              </select></label>
            {isAuto
              ? <button className={`pill-select auto-pill ${pop === "provider" ? "on" : ""}`} data-tour="model-pick" data-pop-root onClick={() => setPop((v) => (v === "provider" ? null : "provider"))} aria-label="Auto mode"
                title="Auto picks the provider and model for each message. Click to change the provider.">
                <Spark className="spark still" />{liveRoute ? <>Auto · <b>{liveRoute.model}</b></> : "Auto"}</button>
              : <button className={`pill-select ${pop === "provider" ? "on" : ""}`} data-pop-root onClick={() => setPop((v) => (v === "provider" ? null : "provider"))} aria-label="Provider"
                title="Which company's models answer">{prov ? prov.label.split(" (")[0] : "Provider"}</button>}
            {!isAuto && prov?.enabled && <ModelSelect key={pid} provider={pid} value={model} onChange={setModel} compact className="pill-select-inner"
              emptyLabel={agent ? "Agent's models" : `${prov.label.split(" (")[0]} · default`} />}
            <button className={`pill-select ${pop === "workspace" ? "on" : ""}`} data-pop-root onClick={() => setPop((v) => (v === "workspace" ? null : "workspace"))} aria-label="Workspace"
              title={workspace ? `Workspace for this chat: ${workspace}` : "Workspace folder for this chat (default from Settings)"}>
              <IFolder size={14} />{workspace ? (workspace.split(/[\\/]/).filter(Boolean).pop() ?? workspace) : "Workspace"}</button>
          </div>
          <div className="composer-right">
            {voice?.configured && voice.dictation && <MicButton className="tool-btn" onText={(t) => { setInput((x) => (x ? x.trimEnd() + " " : "") + t); ta.current?.focus(); }} />}
            {busy && <button className="pill-select" onClick={() => setNoteMode((m) => (m === "queue" ? "steer" : "queue"))}
              title={noteMode === "queue" ? "Queue: read at the agent's next step. Click for Steer now." : "Steer now: interrupts the current step. Click for Queue."}>
              {noteMode === "queue" ? "Queue" : "Steer now"}</button>}
            {busy && <button className="send stop" onClick={stop} title="Stop this run" aria-label="Stop"><IStop /></button>}
            <button className="send" disabled={!input.trim() || (!busy && !canSend)} onClick={() => send()}
              title={busy ? "Send note (Enter)" : canSend ? "Send (Enter)" : "Enable a model provider in Settings first"} aria-label="Send"><IArrowUp size={18} /></button>
          </div>
        </div>
        {pop === "provider" && (
          <div className="pop opts-pop" data-pop-root>
            <label><span className="pop-label">Provider</span>
              <select value={pid} onChange={(e) => { setPid(e.target.value); setModel(""); setPop(null); }}>
                {auto?.available && <option value={AUTO_ID}>Auto — picks the model for each message</option>}
                {providers.map((p) => <option key={p.id} value={p.id} disabled={!p.enabled}>{p.label}{p.enabled ? "" : " — off"}</option>)}</select></label>
            {isAuto && <div className="small mut"><InfoTip id="auto" /> Auto chooses the provider and model for every message, within your spend caps, and shows why under each answer. <Link href="/settings/#auto">Change how it chooses</Link></div>}
            <div className="small mut">Every model call, including sub-agents, is checked against the provider&apos;s cap before it is sent. <InfoTip id="spend-caps" /> <InfoTip id="provider" /></div>
          </div>
        )}
        {pop === "workspace" && (
          <div className="pop opts-pop" data-pop-root>
            <label><span className="pop-label"><IFolder size={14} /> Workspace for this chat</span>
              <FolderPicker compact value={workspace} onChange={setWorkspace} onPicked={() => setPop(null)} placeholder="default from Settings" /></label>
            <div className="small mut">The agent can only read and change files inside this folder. <InfoTip id="workspace" /></div>
          </div>
        )}
      </div>
      {providers.length > 0 && !providers.some((p) => p.enabled) && <div className="foot-note">
        No model provider is enabled yet — <Link href="/settings/#providers">add a key in Settings</Link>.</div>}
    </div>
  );

  async function openInCanvas(o: OpenCanvas) {
    try {
      const c = await api<Canvas>("/api/canvas", { method: "POST", body: JSON.stringify({ ...o, session_id: session ?? "", agent: agent || "" }) });
      setCanvasId(c.id); setCanvasTick((t) => t + 1); setPane("canvas");
    } catch (e) { toast((e as Error).message, "err"); }
  }
  async function toggleCanvas() {                                    // the Canvas chip: close it, reopen this chat's newest one, or start a blank one
    if (canvasId) { setCanvasId(""); return; }
    try {
      const list = session ? await api<Canvas[]>(`/api/canvas?session_id=${session}`) : [];
      if (list.length) { setCanvasId(list[0].id); setPane("canvas"); } else await openInCanvas({ title: "Untitled", type: "markdown", content: "" });
    } catch (e) { toast((e as Error).message, "err"); }
  }
  function keepWidth(w: number) { try { localStorage.setItem("canvas_w", String(w)); } catch { /* storage may be unavailable */ } }
  function startResize(ev: React.PointerEvent) {                     // drag the divider to change how wide the canvas is
    ev.preventDefault();
    let last = canvasW;
    const move = (m: PointerEvent) => { last = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, window.innerWidth - m.clientX)); setCanvasW(last); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); keepWidth(last); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  function resizeKey(ev: React.KeyboardEvent) {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    const n = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, canvasW + (ev.key === "ArrowLeft" ? 40 : -40)));
    setCanvasW(n); keepWidth(n);
  }

  return (
    <ReplyContext.Provider value={(t) => { void send(t); }}>
    <KeepContext.Provider value={{ agent: agent || "", session: session ?? "" }}>
    <CanvasContext.Provider value={openInCanvas}>
    <PlainRichContext.Provider value={plainRich}>
    <div className={`cv${canvasId ? " with-canvas" : ""} show-${pane}`}>
      {canvasId && <div className="cv-tabs seg" role="group" aria-label="Chat or canvas"><button className={pane === "chat" ? "on" : ""} aria-pressed={pane === "chat"} onClick={() => setPane("chat")}>Chat</button>
        <button className={pane === "canvas" ? "on" : ""} aria-pressed={pane === "canvas"} onClick={() => setPane("canvas")}>Canvas</button></div>}
      <div className="cv-main">
        <div className="cv-head">
          <span className="cv-title">{title || (msgs.length ? "New chat" : "")}</span>
          {offline && <span className="pill bad">backend offline — run npm run dev</span>}
          <span className="grow" />
          {session && <ContextMeter sid={session} info={ctx} busy={busy} onSummarized={() => { if (session) openSession(session); reloadCtx(); }} />}
          {session && !busy && <Link href={`/runs/?s=${session}`} className="chip" title="Timeline, threads, tools and cost for this chat"><ITimeline size={15} />Inspect</Link>}
          <button className="chip" aria-pressed={!!canvasId} onClick={() => { void toggleCanvas(); }} title="A document you and the agent edit together, next to the chat">Canvas</button>
          {(runs.length > 0 || showPanel) && <button className="chip" onClick={() => setShowPanel((v) => !v)}><IAgents size={15} />Agents{subCount ? ` · ${subCount}` : ""}</button>}
        </div>

        {msgs.length === 0 ? (
          <div className="hero">
            <h1><Spark className="spark" />{greeting()}</h1>
            {composer}
            <div className="suggest">{SUGGESTIONS.map((s) => <button key={s} onClick={() => { setInput(s); ta.current?.focus(); }}>{s}</button>)}</div>
            {note && <div className="foot-note">{note}</div>}
          </div>
        ) : (<>
          <div className="cv-scroll">
            <div className="cv-col">
              {msgs.map((m, i) => m.role === "handoff" || m.role === "fallback" ? (
                <div key={i} className="sum-div"><div className="sum-line"><span>{m.role === "handoff" ? m.text : `Switched model: ${m.text}`}{m.meta ? ` · ${m.meta}` : ""}</span></div></div>
              ) : m.role === "summary" ? (
                <SummaryDivider key={i} text={m.text} meta={m.meta} />
              ) : m.role === "user" ? (
                <div key={i} className="turn-user"><div><div className="bubble">{m.text}</div>{m.meta && <div className="meta" style={{ textAlign: "right" }}>{m.meta}</div>}</div></div>
              ) : m.role === "refused" ? (
                <ErrorNotice key={i} m={m} onRetry={m.retryable ? () => { const u = [...msgs].reverse().find((x) => x.role === "user"); if (u) { setMsgs((x) => x.slice(0, -2)); send(u.text); } } : undefined}
                  onTry={(p, mdl) => { const u = [...msgs].reverse().find((x) => x.role === "user"); if (u) { setMsgs((x) => x.slice(0, -2)); send(u.text, { provider: p, model: mdl }); } }} />
              ) : (
                <div key={i} className="turn-bot">
                  {m.route && <RouteNote route={m.route} />}
                  <MemoryNote events={m.memory ?? []} />
                  <GuardrailNote events={m.guardrails ?? []} />
                  {(m.steps?.length ?? 0) > 0 && (
                    <details className="tools">
                      <summary><ITool size={14} />Used {m.steps!.filter((s) => s.type === "call").length} tool{m.steps!.filter((s) => s.type === "call").length === 1 ? "" : "s"}<IChevron size={13} /></summary>
                      {m.steps!.map((s, j) => <div key={j} className={`step ${s.type === "result" ? "res" : ""}`}>{s.type === "call" ? `${s.agent} → ${s.tool}(${s.args})` : `↳ ${s.result}`}</div>)}
                    </details>
                  )}
                  {m.thinking && (
                    <details className="think-box">
                      <summary><Spark className={`spark ${m.streaming && !m.text ? "" : "still"}`} />
                        {m.streaming && !m.text ? "Thinking…" : m.thinkSecs ? `Thought for ${fmtDur(m.thinkSecs)}` : "Thought process"}<IChevron size={13} /></summary>
                      <div className="think-body"><Markdown text={m.thinking} /></div>
                    </details>)}
                  {m.streaming && !(m.thinking && !m.text) && <div className="thinking"><Spark className="spark" />{liveActivity}…{startedAt && <span className="mut"> · {fmtDur((Date.now() - startedAt) / 1000)}</span>}
                    {startedAt && Date.now() - startedAt > 20000 && <span className="mut small"> · keeps running if you close this tab</span>}</div>}
                  {m.streaming && asks.map((q) => (
                    <div key={q.request_id} className="ask">
                      <div><b>{q.label ?? q.agent}</b> {q.ask === "permission" ? "asks permission" : q.ask === "question" ? "has a question" : "proposes a plan"}
                        {q.kind && <span className="pill" style={{ marginLeft: 6 }}>{q.kind}</span>}</div>
                      {q.title && <div className="small" style={{ margin: "6px 0", wordBreak: "break-word" }}>{q.title}</div>}
                      {q.input && <pre className="small" style={{ maxHeight: 140 }}>{typeof q.input === "string" ? q.input : JSON.stringify(q.input)}</pre>}
                      {q.ask === "plan_approval" && <pre className="small" style={{ maxHeight: 200 }}>{q.plan}</pre>}
                      <div className="row" style={{ marginTop: 8 }}>
                        {q.ask === "permission" && (q.options ?? []).map((o: any) => <button key={o.id} className={String(o.kind).startsWith("allow") ? "acc" : ""} onClick={() => answer(q, { option_id: o.id })}>{o.name}</button>)}
                        {q.ask === "question" && (q.questions ?? []).flatMap((qq: any) => (qq.options ?? []).map((o: any) =>
                          <button key={qq.id + o.id} onClick={() => answer(q, { answers: [{ questionId: qq.id, selectedOptionIds: [o.id] }] })}>{o.label}</button>))}
                        {q.ask === "question" && <button className="ghost" onClick={() => answer(q, { skip: true })}>Skip</button>}
                        {q.ask === "plan_approval" && <><button className="acc" onClick={() => answer(q, { accepted: true })}>Approve plan</button><button onClick={() => answer(q, { accepted: false })}>Reject</button></>}
                        {q.ask !== "question" && <button className="ghost" onClick={() => answer(q, { cancel: true })}>Cancel</button>}
                      </div>
                    </div>))}
                  {m.text && <Markdown text={m.text} />}
                  {m.streaming && m.text && <span className="cursor" />}
                  {!plainRich && m.uis && m.uis.length > 0 && m.uis.map((u, j) => <UiBlock key={j} spec={u} />)}
                  {m.artifacts && m.artifacts.length > 0 && <ArtifactList items={m.artifacts} />}
                  {!m.streaming && m.runs && (m.runs.length > 1 || (m.steps ?? []).length > 0) && <StatePanel state={runState(m)} />}
                  {m.warnings?.map((w, j) => <div key={j} className="small" style={{ color: "var(--warn-text)", fontFamily: "var(--sans)" }}>⚠ {w}</div>)}
                  {!m.streaming && (
                    <div className="turn-meta">
                      <CopyBtn text={m.text} />
                      {m.text && <button className="act" title="Keep this answer on the Artifacts page" onClick={() => saveAnswer(m)}><ISave size={14} />Keep</button>}
                      {m.text && <button className="act" title="Open this answer in the canvas to edit it with the agent" onClick={() => { void openInCanvas({ title: (m.text.match(/^#{1,3}\s+(.+)$/m)?.[1] ?? m.text.split(String.fromCharCode(10))[0]).slice(0, 80), type: "markdown", content: m.text }); }}>Edit in canvas</button>}
                      {voice?.configured && voice.voice_id && m.text && <ListenButton key={i} text={m.text} autoPlay={voice.auto_read && i === autoReadIdx} />}
                      {m.runs && m.runs.length > 1 && <button className="act" onClick={() => { setRuns(m.runs!); setShowPanel(true); }}>
                        <IAgents size={14} />{m.runs.length - 1} sub-agent{m.runs.length - 1 === 1 ? "" : "s"}{m.runs.some((r) => r.spawned) ? " (agent-built)" : ""}</button>}
                      <span>{m.meta}</span>
                    </div>
                  )}
                </div>
              ))}
              {note && <div className="small mut" style={{ textAlign: "center" }}>{note}</div>}
              <div ref={end} />
            </div>
          </div>
          {composer}
        </>)}
      </div>

      {showPanel && (
        <aside className="ap">
          <div className="ap-head"><IAgents /><b>Agents</b><span className="mut small">{runs.length ? `${runs.length} run${runs.length === 1 ? "" : "s"}` : "none yet"}</span>
            <span className="grow" /><button className="icon-btn" onClick={() => setShowPanel(false)} title="Close"><IX size={16} /></button></div>
          <div className="ap-body">
            {runs.length === 0 ? <div className="small mut">Agents appear here live as they start — including sub-agents that agents build themselves.</div> : (<>
              <RunTimeline runs={runs as unknown as TRun[]} selected={lane?.key} onSelect={setSelLane} height={220} />
              <div className="row small" style={{ margin: "8px 0 4px" }}><span className="mut">Click a lane to read that agent&apos;s thread.</span><span className="grow" />
                {session && <Link href={`/runs/?s=${session}`}>Open inspector →</Link>}</div>
              {lane && <div style={{ marginTop: 10 }}><RunThread key={lane.key} lane={lane} onSave={saveRun} /></div>}
            </>)}
          </div>
        </aside>
      )}
      {canvasId && (<>
        <div className="cv-divider" role="separator" aria-orientation="vertical" aria-label="Resize the canvas (arrow keys)" tabIndex={0} onPointerDown={startResize} onKeyDown={resizeKey} />
        <div className="cv-canvas" style={{ ["--canvas-w" as string]: `${canvasW}px` }}>
          <Suspense fallback={<div className="small mut" role="status" style={{ padding: 16 }}>Opening the canvas…</div>}>
            <CanvasPanel canvasId={canvasId} refreshKey={canvasTick} onClose={() => setCanvasId("")} onGone={() => setCanvasId("")}
              onAsk={(t) => { setPane("chat"); void send(t); }} />
          </Suspense>
        </div></>)}
    </div>
    </PlainRichContext.Provider>
    </CanvasContext.Provider>
    </KeepContext.Provider>
    </ReplyContext.Provider>
  );
}

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Burning the midnight oil?" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Page() {
  return <Suspense fallback={null}><ChatPage /></Suspense>;
}
