/**
 * Guided tours (driver.js). A tour is a list of steps; each step names the page it lives on and, optionally,
 * an element marked with data-tour="...". Tours can cross pages: progress is kept in localStorage so the next
 * page continues where the last one stopped. Pure helpers here are unit-testable; the runner lives in TourRunner.
 */

export type TourStep = { page: string; element?: string; title: string; body: string; side?: "top" | "right" | "bottom" | "left" };
export type Tour = { title: string; blurb: string; steps: TourStep[] };

export const STATE_KEY = "as-tour";          // { tour, index } while a tour is running
export const DONE_KEY = "as-tour-done";      // "1" once the first-run tour was finished or dismissed

export const TOURS: Record<string, Tour> = {
  welcome: {
    title: "Welcome to Agent Studio",
    blurb: "A two-minute tour of the whole workbench.",
    steps: [
      { page: "/", title: "Your workbench", body: "Agent Studio runs chats, agents and tools side by side. This is the sidebar: everything lives here.", element: "sidebar", side: "right" },
      { page: "/", title: "Start a chat", body: "Type what you want done. Plain chat answers directly; pick a built agent to hand the work to a team.", element: "composer", side: "top" },
      { page: "/", title: "Pick who answers", body: "Choose plain chat or one of your agents. The model is Auto by default: it picks one for each message, or you can pin a provider and model yourself.", element: "agent-select", side: "top" },
      { page: "/", title: "Ask me anything", body: "Stuck? Open the assistant. It can take you to a page, start a tour, or point at things on screen.", element: "assistant", side: "left" },
      { page: "/agents/", title: "Design your agents", body: "Each agent has a model tier, instructions, tools, MCP servers and sub-agents. Super Engineer has everything switched on.", element: "agent-list", side: "right" },
      { page: "/knowledge/", title: "Give agents your documents", body: "Knowledge bases let agents search your own files (RAG). Upload documents, pick an embedding model, then attach the knowledge base to an agent.", element: "knowledge-list", side: "top" },
      { page: "/mcp/", title: "Connect tools", body: "MCP servers add tools: files, git, browsers, your own APIs. Pick one in the list, turn it on and test it. Add more with the Add server button.", element: "mcp-list", side: "top" },
      { page: "/memory/", title: "What agents remember", body: "Facts agents keep across chats. You can read, correct or forget any of them.", element: "memory-list", side: "right" },
      { page: "/settings/", title: "Settings", body: "Providers and API keys, sandbox limits, external agents, and the assistant's own model.", element: "settings-tabs", side: "bottom" },
    ],
  },
  chat: {
    title: "Chatting",
    blurb: "How a chat turn works, step by step.",
    steps: [
      { page: "/", title: "Compose", body: "Write a request. Enter sends it; Shift+Enter starts a new line.", element: "composer", side: "top" },
      { page: "/", title: "Queue or steer", body: "While the agent works you can queue a note for its next step, or steer now to interrupt the current step.", element: "composer", side: "top" },
      { page: "/", title: "Context window", body: "The ring shows how full the model's context is. Summarise frees room without losing the thread.", element: "context-meter", side: "bottom" },
    ],
  },
  agents: {
    title: "Agent Studio",
    blurb: "Building and delegating to agents.",
    steps: [
      { page: "/agents/", title: "Your agents", body: "Pick an agent to edit it, or create a new one.", element: "agent-list", side: "right" },
      { page: "/agents/", title: "Delegation tree", body: "This shows which sub-agents an agent can call, and with which models.", element: "agent-tree", side: "right" },
    ],
  },
  knowledge: {
    title: "Knowledge bases (RAG)",
    blurb: "Let agents search your own documents.",
    steps: [
      { page: "/knowledge/", title: "Your knowledge bases", body: "Each one holds documents turned into searchable passages by one embedding model. Open one to add files, paste text, or test a search.", element: "knowledge-list", side: "top" },
      { page: "/knowledge/", title: "Where vectors live", body: "Stores keep the vectors: a local file, a Docker container like Qdrant, or a service you connect. Start a Docker store here with one click.", element: "knowledge-stores", side: "top" },
    ],
  },
  auto: {
    title: "Auto mode",
    blurb: "Let Agent Studio pick the model for each message.",
    steps: [
      { page: "/", title: "Auto picks the model", body: "In Auto mode you never choose a provider or model. Agent Studio reads each message and picks the cheapest model that can handle it, within your spend caps. Under every answer it shows what it chose and why.", element: "model-pick", side: "top" },
      { page: "/settings/", title: "Tune Auto", body: "Choose what reads your messages (built-in rules or a local d1 decision model), cost versus quality, which providers it may use, and try a message to see the routing without spending anything.", element: "auto-card", side: "bottom" },
    ],
  },
  discovery: {
    title: "AI-BOM and discovery",
    blurb: "See what the system is made of, and optionally publish it.",
    steps: [
      { page: "/discovery/", title: "Your AI bill of materials", body: "Pick a redaction level and a scope, then preview, validate against the CycloneDX schema, or download. Nothing is published from here.", element: "aibom-panel", side: "top" },
      { page: "/settings/", title: "Publishing is opt-in", body: "Under Discovery & AI-BOM you choose a domain you control, what to publish and at which level. Everything is off until you switch it on.", element: "settings-tabs", side: "bottom" },
    ],
  },
  mcp: {
    title: "MCP servers",
    blurb: "Adding and testing tool servers.",
    steps: [
      { page: "/mcp/", title: "Tool servers", body: "Pick a server in the list; its details open on the right. Switch it on at the top, add credentials on Settings, then press Test on Health & logs.", element: "mcp-list", side: "right" },
      { page: "/mcp/", title: "Server details", body: "Overview, Settings, Tools and Health & logs live here. For Playwright, Settings holds the browser rules: keep allowed and blocked sites tight.", element: "mcp-detail", side: "left" },
    ],
  },
};

export const TOUR_NAMES = Object.keys(TOURS);

/** Trailing slashes are optional in routes ("/agents" and "/agents/" are the same page). */
export function normPath(p: string): string {
  const s = (p || "/").split("?")[0].split("#")[0];
  return s.length > 1 ? s.replace(/\/+$/, "") || "/" : "/";
}

export function samePage(a: string, b: string): boolean {
  return normPath(a) === normPath(b);
}

export type TourState = { tour: string; index: number };

export function readState(store: Pick<Storage, "getItem"> | null | undefined = safeStorage()): TourState | null {
  try {
    const raw = store?.getItem(STATE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as TourState;
    if (!v || !TOURS[v.tour] || typeof v.index !== "number") return null;
    return { tour: v.tour, index: Math.max(0, Math.min(v.index, TOURS[v.tour].steps.length)) };
  } catch {
    return null;
  }
}

export function writeState(state: TourState | null, store: Pick<Storage, "setItem" | "removeItem"> | null | undefined = safeStorage()): void {
  try {
    if (!store) return;
    if (state) store.setItem(STATE_KEY, JSON.stringify(state));
    else store.removeItem(STATE_KEY);
  } catch {
    /* storage may be blocked (private windows): the tour then simply does not resume */
  }
}

/**
 * The steps of `tour` from `index` that live on the page we are on, and where the tour continues.
 * `next` is the index of the first step on a different page (null when the tour is over).
 */
export function slice(tour: string, index: number, path: string): { steps: TourStep[]; next: number | null; nextPage: string | null } {
  const all = TOURS[tour]?.steps ?? [];
  const here: TourStep[] = [];
  let i = index;
  while (i < all.length && samePage(all[i].page, path)) here.push(all[i++]);
  if (i >= all.length) return { steps: here, next: null, nextPage: null };
  return { steps: here, next: i, nextPage: all[i].page };
}

/** Has the first-run tour already been shown or dismissed? */
export function firstRunDone(store: Pick<Storage, "getItem"> | null | undefined = safeStorage()): boolean {
  if (!store) return true;                     // no storage at all: never auto-start the tour
  try {
    return store.getItem(DONE_KEY) === "1";
  } catch {
    return true;
  }
}

export function markFirstRunDone(store: Pick<Storage, "setItem"> | null | undefined = safeStorage()): void {
  try {
    store?.setItem(DONE_KEY, "1");
  } catch {
    /* ignore */
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
