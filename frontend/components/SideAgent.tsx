"use client";
import { HttpAgent } from "@ag-ui/client";
import { driver } from "driver.js";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import { TOURS, TOUR_NAMES } from "@/lib/tour";
import { IArrowUp, IX, Spark } from "./Icons";
import { startTour } from "./TourRunner";

/** Pages the assistant may open. Keep in step with the sidebar. */
export const ROUTES: { path: string; label: string }[] = [
  { path: "/", label: "Chat" }, { path: "/dashboard/", label: "Dashboard" }, { path: "/runs/", label: "Runs" },
  { path: "/external/", label: "External agents" }, { path: "/agents/", label: "Agent Studio" }, { path: "/skills/", label: "Skills" },
  { path: "/knowledge/", label: "Knowledge" }, { path: "/mcp/", label: "MCP servers" }, { path: "/memory/", label: "Memory" }, { path: "/catalog/", label: "Catalog" }, { path: "/security/", label: "Security" }, { path: "/evals/", label: "Evaluations" }, { path: "/artifacts/", label: "Artifacts" }, { path: "/workflows/", label: "Workflows" }, { path: "/telemetry/", label: "Telemetry" }, { path: "/discovery/", label: "Discovery & AI-BOM" },
  { path: "/billing/", label: "Billing" }, { path: "/settings/", label: "Settings" }, { path: "/setup/", label: "Set-up guide" },
];

/** Things on screen that carry data-tour="..." and may be pointed at. */
export const TARGETS: string[] = Array.from(new Set(
  Object.values(TOURS).flatMap((t) => t.steps.map((s) => s.element)).filter((x): x is string => !!x)));

/** The tools the browser declares to the side agent (AG-UI "frontend tools"). The model calls them; we run them here. */
export const TOOLS = [
  { name: "navigate", description: "Open a page of Agent Studio.",
    parameters: { type: "object", properties: { page: { type: "string", enum: ROUTES.map((r) => r.path) } }, required: ["page"] } },
  { name: "start_tour", description: "Start a guided tour that walks the user through part of the app with highlighted cards.",
    parameters: { type: "object", properties: { tour: { type: "string", enum: TOUR_NAMES } }, required: ["tour"] } },
  { name: "explain", description: "Highlight one part of the screen with a card explaining it. Only works for things on the current page.",
    parameters: { type: "object", properties: { target: { type: "string", enum: TARGETS }, title: { type: "string" }, text: { type: "string" } }, required: ["target", "text"] } },
];

type Item = { id: string; role: "user" | "assistant" | "note" | "error"; text: string };

function uid() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

let highlighter: ReturnType<typeof driver> | null = null;

/** Runs one browser tool. The returned text goes back to the model as the tool result. */
export function runTool(name: string, args: Record<string, unknown>, go: (path: string) => void, off: string[] = []): string {
  if (off.includes(name)) return `${name} is switched off in Settings`;
  try {
    if (name === "navigate") {
      const page = String(args.page ?? "");
      if (!ROUTES.some((r) => r.path === page)) return `unknown page ${page}`;
      go(page);
      return `opened ${page}`;
    }
    if (name === "start_tour") {
      const tour = String(args.tour ?? "");
      if (!TOURS[tour]) return `unknown tour ${tour}`;
      startTour(tour);
      return `started the ${tour} tour`;
    }
    if (name === "explain") {
      const target = String(args.target ?? "");
      const el = document.querySelector(`[data-tour="${target}"]`);
      if (!el) return `${target} is not on this page`;
      highlighter?.destroy();
      highlighter = driver({ allowClose: true, popoverClass: "as-tour-pop", stagePadding: 6, stageRadius: 12 });
      highlighter.highlight({
        element: el,
        popover: { title: String(args.title ?? "") || undefined, description: String(args.text ?? ""), side: "left", align: "center" },
      });
      return `showed the card for ${target}`;
    }
    return `unknown tool ${name}`;
  } catch (e) {
    return `failed: ${(e as Error).message}`;
  }
}

/** The floating assistant: a launcher button and a panel that streams AG-UI events from /api/sidebot. */
export default function SideAgent() {
  const router = useRouter();
  const path = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState<{ provider: string | null; model: string | null; enabled: boolean } | null>(null);
  const [off, setOff] = useState<string[]>([]);                     // tools the user switched off in Settings -> Assistant
  const agent = useRef<HttpAgent | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    agent.current = new HttpAgent({ url: `${API_BASE}/api/sidebot`, agentId: "sidebot" });
    return () => { agent.current?.abortRun(); };
  }, []);

  useEffect(() => {
    if (!open) return;
    api<{ sidebot_enabled: boolean; sidebot_resolved: { provider: string | null; model: string | null }; sidebot_tools?: { name: string; enabled: boolean }[] }>("/api/settings")
      .then((s) => { setModel({ provider: s.sidebot_resolved.provider, model: s.sidebot_resolved.model, enabled: s.sidebot_enabled }); setOff((s.sidebot_tools ?? []).filter((t) => !t.enabled).map((t) => t.name)); })
      .catch(() => setModel(null));
  }, [open]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [items, open]);

  const add = (role: Item["role"], text: string) => setItems((l) => [...l, { id: uid(), role, text }]);
  // The bubble being streamed is marked "live:"; closing it lets the next model round start a new bubble.
  const appendAssistant = (delta: string) =>
    setItems((l) => {
      const last = l[l.length - 1];
      if (last && last.role === "assistant" && last.id.startsWith("live:")) return [...l.slice(0, -1), { ...last, text: last.text + delta }];
      return [...l, { id: `live:${uid()}`, role: "assistant", text: delta }];
    });
  const closeLive = () => setItems((l) => l.map((x) => (x.id.startsWith("live:") ? { ...x, id: x.id.replace("live:", "done:") } : x)));

  async function send(text?: string) {
    const a = agent.current;
    if (!a || busy) return;
    if (text) {
      add("user", text);
      a.addMessage({ id: uid(), role: "user", content: text });
    }
    setBusy(true);
    try {
      let rounds = 0;
      let again = true;
      while (again && rounds < 4) {                  // a tool result is sent back and the model answers it
        rounds++;
        const results: { id: string; text: string }[] = [];
        await a.runAgent({ tools: TOOLS.filter((t) => !off.includes(t.name)), context: [{ description: "current page", value: path }] }, {
          onTextMessageContentEvent: ({ event }) => appendAssistant(event.delta),
          onToolCallEndEvent: ({ event, toolCallName, toolCallArgs }) => {
            const text = runTool(toolCallName, toolCallArgs as Record<string, unknown>, (p) => router.push(p), off);
            results.push({ id: event.toolCallId, text });
            add("note", `${toolCallName}: ${text}`);
          },
          onRunErrorEvent: ({ event }) => add("error", event.message),
        });
        closeLive();
        again = results.length > 0;
        for (const r of results) a.addMessage({ id: uid(), role: "tool", toolCallId: r.id, content: r.text });
      }
    } catch (e) {
      add("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const sendDraft = () => {
    const t = draft.trim();
    if (!t) return;
    setDraft("");
    void send(t);
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    sendDraft();
  };

  const modelLabel = !model ? "" : !model.enabled ? "switched off in Settings" :
    model.provider ? `${model.provider}${model.model ? ` · ${model.model}` : ""}` : "no model set";

  return (
    <>
      <button className="sa-fab" data-tour="assistant" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Open the assistant" title="Assistant">
        <Spark size={16} /><span>Assistant</span>
      </button>
      {open && (
        <aside className="sa-panel" role="dialog" aria-label="Assistant">
          <div className="sa-head">
            <div className="grow">
              <b>Assistant</b>
              <div className="sa-model" title="The model this assistant uses; set it in Settings → Assistant">{modelLabel}</div>
            </div>
            <button className="icon-btn" aria-label="Close the assistant" onClick={() => setOpen(false)}><IX size={16} /></button>
          </div>
          <div className="sa-msgs">
            {items.length === 0 && (
              <div className="sa-empty">
                <p>Ask me how something works, or to take you somewhere. I can also walk you through a part of the app with highlighted cards.</p>
                <div className="sa-chips">
                  <button onClick={() => void send("What can I do here?")}>What can I do here?</button>
                  <button onClick={() => void send("Show me around the app.")}>Show me around</button>
                  <button onClick={() => void send("Take me to MCP servers.")}>Open MCP servers</button>
                </div>
              </div>
            )}
            {items.map((m) => (
              <div key={m.id} className={`sa-msg ${m.role}`}>{m.text}</div>
            ))}
            {busy && <div className="sa-msg note">working…</div>}
            <div ref={bottom} />
          </div>
          <form className="sa-compose" onSubmit={submit}>
            <textarea rows={1} value={draft} placeholder="Ask about the app…" onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendDraft(); } }} aria-label="Message the assistant" />
            <button className="send" disabled={busy || !draft.trim()} aria-label="Send"><IArrowUp size={16} /></button>
          </form>
        </aside>
      )}
    </>
  );
}
