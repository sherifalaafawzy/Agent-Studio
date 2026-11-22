import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Onboarding from "@/components/Onboarding";
import OnboardingGate from "@/components/OnboardingGate";
import SetupPage from "@/app/setup/page";
import { jsonResponse, routeFetch } from "../helpers/http";

const push = vi.fn();
let pathname = "/";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => pathname }));
const startTour = vi.fn();
vi.mock("@/components/TourRunner", () => ({ startTour: (...a: unknown[]) => startTour(...a) }));
vi.mock("@/components/OllamaSetup", () => ({ default: ({ onChange }: { onChange?: () => void }) => <button onClick={() => onChange?.()}>ollama-setup</button> }));
vi.mock("@/components/OllamaLibrary", () => ({ default: ({ onInstalledChange }: { onInstalledChange?: (n: string[]) => void }) => <button onClick={() => onInstalledChange?.(["qwen3:8b"])}>ollama-library</button> }));
vi.mock("@/components/AgentInstaller", () => ({ default: ({ only, onChange }: { only?: string[]; onChange?: (n: number) => void }) => <button data-only={only?.join(",")} onClick={() => onChange?.(1)}>agent-installer</button> }));
vi.mock("@/components/FolderPicker", () => ({ default: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => <input aria-label="folder" value={value} onChange={(e) => onChange(e.target.value)} /> }));

const STEPS = ["welcome", "power", "ollama", "model", "key", "workspace", "agents", "assistant", "first", "done"];
const LABELS: Record<string, string> = { welcome: "Welcome", power: "How agents are powered", ollama: "Local models (Ollama)", model: "A model to use", key: "An online provider key", workspace: "Your work folder", agents: "Coding agents", assistant: "Side assistant", first: "Your first chat or agent", done: "Finished" };
const checklist = (state: Record<string, string> = {}) => STEPS.map((id) => ({ id, label: LABELS[id], state: state[id] ?? "todo", detail: `detail of ${id}` }));
const SYS = { items: [
  { id: "python", label: "Python", state: "ok", detail: "3.12", why: "Already running." }, { id: "docker", label: "Docker", state: "missing", detail: "not running", why: "For the sandbox." },
  { id: "disk", label: "Free disk space", state: "warn", detail: "9 GB free", why: "Models are big." } ] };
const PROVS = [
  { id: "openai", label: "OpenAI", kind: "openai", enabled: true, auth_ready: true }, { id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: true, auth_ready: false },
  { id: "gemini", label: "Gemini", kind: "gemini", enabled: false }, { id: "ollama", label: "Ollama", kind: "ollama", enabled: true }, { id: "mock", label: "Mock", kind: "mock", enabled: true },
];

function server(over: { step?: string; choice?: string; check?: Record<string, string>; routes?: Record<string, any>; settings?: Record<string, unknown> } = {}) {
  const rec = { status: "in_progress", step: over.step ?? "welcome", choice: over.choice ?? "", completed: [] as string[], skipped: [] as string[] };
  const settings: Record<string, unknown> = { workspace_dir: "H:/htdocs/demo", sidebot_enabled: true, ...(over.settings ?? {}) };
  const r = routeFetch({
    "GET /api/easy/onboarding": () => jsonResponse({ record: rec, steps: STEPS, checklist: checklist(over.check), show: false, resume: true }),
    "PUT /api/easy/onboarding": (init?: RequestInit) => {
      const b = JSON.parse(init?.body as string);
      if (b.step) rec.step = b.step;
      if (b.status) rec.status = b.status;
      if (b.choice !== undefined) rec.choice = b.choice;
      for (const c of b.completed ?? []) rec.completed.push(c);
      for (const c of b.skipped ?? []) rec.skipped.push(c);
      return jsonResponse(rec);
    },
    "GET /api/easy/system": () => jsonResponse(SYS),
    "GET /api/providers": () => jsonResponse(PROVS),
    "GET /api/settings": () => jsonResponse(settings),
    "PUT /api/settings": (init?: RequestInit) => { Object.assign(settings, JSON.parse(init?.body as string)); return jsonResponse(settings); },
    ...(over.routes ?? {}),
  });
  return { ...r, rec, settings };
}
const sentPut = (calls: any[]) => calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/easy/onboarding").map((c) => c.body);
const next = () => userEvent.click(screen.getByRole("button", { name: "Next" }));

beforeEach(() => { push.mockReset(); startTour.mockReset(); pathname = "/"; });

describe("Onboarding: the guide", () => {
  it("opens at the saved step with progress, the why, and the cost", async () => {
    server({ step: "workspace", check: { workspace: "done" } });
    render(<Onboarding />);
    expect(await screen.findByRole("heading", { name: "Choose your work folder" })).toBeInTheDocument();
    expect(screen.getByLabelText("Step 6 of 10")).toBeInTheDocument();
    expect(document.querySelector(".bar i")).toHaveStyle({ width: "60%" });
    expect(screen.getByText(/Agents can only read and change files inside one folder/)).toBeInTheDocument();
    expect(screen.getByText(/Cost:/).parentElement).toHaveTextContent("Free.");
    expect(screen.getByText("done", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("Right now: detail of workspace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Folder/ })).toHaveAttribute("aria-current", "step");
  });

  it("the welcome step checks the PC in plain words", async () => {
    const { calls } = server();
    render(<Onboarding />);
    expect(await screen.findByText("Python")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("not found")).toBeInTheDocument();
    expect(screen.getByText("check")).toBeInTheDocument();
    expect(screen.getByText("Models are big.")).toBeInTheDocument();
    expect(screen.queryByText(/^Right now:/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/easy/system").length).toBe(2));
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Skip this step" })).toBeNull();
  });

  it("moves forward and back, and saves where you are, what you finished and what you skipped", async () => {
    const { calls } = server({ step: "model", choice: "both" });
    render(<Onboarding />);
    await screen.findByText("ollama-library");
    await next();
    await screen.findByRole("heading", { name: "Connect an online provider" });
    expect(sentPut(calls)).toEqual([{ step: "key", completed: ["model"] }]);
    await userEvent.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByRole("heading", { name: "Choose your work folder" });
    expect(sentPut(calls).at(-1)).toEqual({ step: "workspace", skipped: ["key"] });
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await screen.findByRole("heading", { name: "Connect an online provider" });
    expect(sentPut(calls).at(-1)).toEqual({ step: "key" });
    await userEvent.click(screen.getByRole("button", { name: /Coding agents/ }));                  // jump with the step chips
    await screen.findByText("agent-installer");
    expect(screen.getByRole("button", { name: /Coding agents/ })).toHaveAttribute("aria-current", "step");
  });

  it("the power step needs a choice, and the choice shapes later steps", async () => {
    const { calls } = server({ step: "power" });
    render(<Onboarding />);
    const go = await screen.findByRole("button", { name: "Next" });
    expect(go).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /On this PC/ }));
    expect(sentPut(calls)).toEqual([{ choice: "local" }]);
    await waitFor(() => expect(screen.getByRole("button", { name: /On this PC/ })).toHaveAttribute("aria-pressed", "true"));
    expect(go).toBeEnabled();
  });

  it("marks steps as optional when you chose the other way", async () => {
    server({ step: "ollama", choice: "online" });
    const { unmount } = render(<Onboarding />);
    expect(await screen.findByText(/You chose online providers, so this step is optional/)).toBeInTheDocument();
    unmount();
    server({ step: "key", choice: "local" });
    render(<Onboarding />);
    expect(await screen.findByText(/You chose local models, so this step is optional/)).toBeInTheDocument();
  });

  it("the Ollama step embeds the installer and refreshes the checklist when it changes", async () => {
    const { calls } = server({ step: "ollama", choice: "local" });
    render(<Onboarding />);
    await userEvent.click(await screen.findByText("ollama-setup"));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/easy/onboarding" && c.method === "GET").length).toBe(2));
  });

  it("the model step refreshes the checklist when the library's models change", async () => {
    const { calls } = server({ step: "model", choice: "local" });
    render(<Onboarding />);
    await userEvent.click(await screen.findByText("ollama-library"));
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/easy/onboarding").length).toBe(2));
  });

  it("the key step lists providers without ever asking for a key", async () => {
    server({ step: "key" });
    render(<Onboarding />);
    await screen.findByRole("heading", { name: "Connect an online provider" });
    await waitFor(() => expect(screen.getByText("OpenAI")).toBeInTheDocument());
    expect(screen.getByText("OpenAI").parentElement).toHaveTextContent("ready");
    expect(screen.getByText("Anthropic").parentElement).toHaveTextContent("needs a key");
    expect(screen.getByText("Gemini").parentElement).toHaveTextContent("off");
    expect(screen.queryByText("Ollama", { selector: "b" })).toBeNull();
    expect(screen.queryByText("Mock", { selector: "b" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText(/Never paste keys into the chat/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Settings → Model providers" })).toHaveAttribute("href", expect.stringMatching(/\/settings\/?#providers/));
  });

  it("the folder step saves a new work folder, and reports a refusal", async () => {
    const { calls } = server({ step: "workspace" });
    render(<Onboarding />);
    const box = await screen.findByLabelText("folder");
    await waitFor(() => expect(box).toHaveValue("H:/htdocs/demo"));
    const save = screen.getByRole("button", { name: "Save folder" });
    expect(save).toBeDisabled();
    await userEvent.clear(box);
    await userEvent.type(box, "H:/work");
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.url.pathname === "/api/settings")?.body).toEqual({ workspace_dir: "H:/work" }));
    await waitFor(() => expect(save).toBeDisabled());
  });

  it("explains a refused folder", async () => {
    server({ step: "workspace", routes: { "PUT /api/settings": () => jsonResponse({ detail: "workspace_dir must be an absolute path" }, { status: 422 }) } });
    render(<Onboarding />);
    const box = await screen.findByLabelText("folder");
    await userEvent.type(box, "x");
    await userEvent.click(screen.getByRole("button", { name: "Save folder" }));
    expect(await screen.findByText("workspace_dir must be an absolute path")).toBeInTheDocument();
  });

  it("the coding agents step shows only the main agents", async () => {
    const { calls } = server({ step: "agents" });
    render(<Onboarding />);
    const inst = await screen.findByText("agent-installer");
    expect(inst).toHaveAttribute("data-only", "claude,codex,gemini,copilot,cursor,grok");
    await userEvent.click(inst);
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/easy/onboarding").length).toBe(2));
  });

  it("the assistant step switches the assistant on and off", async () => {
    const { calls } = server({ step: "assistant", settings: { sidebot_enabled: true } });
    render(<Onboarding />);
    const off = await screen.findByRole("button", { name: "Switch the assistant off" });
    await waitFor(() => expect(screen.getByText("on", { selector: ".pill" })).toBeInTheDocument());
    await userEvent.click(off);
    expect(await screen.findByRole("button", { name: "Switch the assistant on" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT" && c.url.pathname === "/api/settings")!.body).toEqual({ sidebot_enabled: false });
    expect(screen.getByRole("link", { name: "Choose its model and rules" })).toHaveAttribute("href", expect.stringMatching(/\/settings\/?#assistant/));
  });

  it("explains a failed assistant switch", async () => {
    server({ step: "assistant", routes: { "PUT /api/settings": () => jsonResponse({ detail: "locked" }, { status: 500 }) } });
    render(<Onboarding />);
    await userEvent.click(await screen.findByRole("button", { name: "Switch the assistant off" }));
    expect(await screen.findByText("locked")).toBeInTheDocument();
  });

  it("the first step starts the agent wizard", async () => {
    const { calls } = server({ step: "first" });
    render(<Onboarding />);
    await userEvent.click(await screen.findByRole("button", { name: "Create an agent" }));
    expect(push).toHaveBeenCalledWith("/agents/?wizard=1");
    expect(sentPut(calls)[0]).toEqual({ completed: ["first"], step: "done" });
  });

  it("the first step starts a chat, and has a Finish button instead of Next", async () => {
    server({ step: "first" });
    render(<Onboarding />);
    expect(await screen.findByRole("button", { name: "Finish" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start a chat" }));
    expect(push).toHaveBeenLastCalledWith("/");
  });

  it("the last step summarises everything, and finishes or starts the tour", async () => {
    const { calls } = server({ step: "done", check: { model: "done", key: "optional" } });
    render(<Onboarding />);
    await screen.findByRole("heading", { name: "You are set up" });
    expect(screen.getAllByText(/^detail of /).length).toBe(8);
    expect(screen.getByText("A model to use").closest("li")).toHaveTextContent("done");
    expect(screen.getByText("An online provider key").closest("li")).toHaveTextContent("optional");
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
    expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute("href", expect.stringMatching(/^[/]catalog[/]?$/));
    await userEvent.click(screen.getByRole("button", { name: "Go to Agent Studio" }));
    expect(sentPut(calls).at(-1)).toEqual({ status: "done", completed: ["done"] });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(localStorage.getItem("as-tour-done")).toBe("1");
  });

  it("'Take a quick tour' finishes the guide and starts the welcome tour", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      server({ step: "done" });
      render(<Onboarding />);
      await userEvent.click(await screen.findByRole("button", { name: "Take a quick tour" }));
      await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
      act(() => { vi.advanceTimersByTime(500); });
      expect(startTour).toHaveBeenCalledWith("welcome");
    } finally { vi.useRealTimers(); }
  });

  it("'Skip the guide' leaves without finishing", async () => {
    const { calls } = server();
    render(<Onboarding />);
    await userEvent.click(await screen.findByRole("button", { name: "Skip the guide" }));
    expect(sentPut(calls).at(-1)).toEqual({ status: "skipped" });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });

  it("says when it cannot load, and when a save fails", async () => {
    routeFetch({ "GET /api/easy/onboarding": () => jsonResponse({ detail: "backend offline" }, { status: 500 }), "GET /api/easy/system": () => jsonResponse(SYS), "GET /api/providers": () => jsonResponse([]), "GET /api/settings": () => jsonResponse({}) });
    const { unmount } = render(<Onboarding />);
    expect(await screen.findByText("backend offline")).toBeInTheDocument();
    unmount();
    server({ step: "power", routes: { "PUT /api/easy/onboarding": () => jsonResponse({ detail: "disk full" }, { status: 500 }) } });
    render(<Onboarding />);
    await userEvent.click(await screen.findByRole("button", { name: /Both/ }));
    expect(await screen.findByText("disk full")).toBeInTheDocument();
  });

  it("tolerates a failing system check, provider list and settings", async () => {
    server({ routes: { "GET /api/easy/system": () => jsonResponse({}, { status: 500 }), "GET /api/providers": () => jsonResponse({}, { status: 500 }), "GET /api/settings": () => jsonResponse({}, { status: 500 }) } });
    render(<Onboarding />);
    expect(await screen.findByText("Checking…")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(screen.getByRole("heading", { name: "Welcome to Agent Studio" })).toBeInTheDocument();
  });

  it("the setup page is just the guide", async () => {
    server();
    render(<SetupPage />);
    expect(await screen.findByRole("heading", { name: "Set-up guide" })).toBeInTheDocument();
  });
});

describe("OnboardingGate", () => {
  const gate = (o: Record<string, unknown>) => routeFetch({ "GET /api/easy/onboarding": () => jsonResponse({ show: false, resume: false, steps: STEPS, record: { step: "ollama" }, ...o }) });

  it("sends a first-time visitor to the guide, once, and claims the first run so the tour waits", async () => {
    const { calls } = gate({ show: true });
    render(<OnboardingGate />);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/setup/"));
    expect(localStorage.getItem("as-tour-done")).toBe("1");
    expect(sessionStorage.getItem("as-gate-checked")).toBe("1");
    render(<OnboardingGate />);                                                    // a second mount in the same tab does not ask again
    expect(calls.length).toBe(1);
  });

  it("does not redirect when already on the guide, or when it is not needed", async () => {
    sessionStorage.clear();
    pathname = "/setup/";
    gate({ show: true });
    render(<OnboardingGate />);
    await waitFor(() => expect(sessionStorage.getItem("as-gate-checked")).toBe("1"));
    expect(push).not.toHaveBeenCalled();
    sessionStorage.clear();
    pathname = "/";
    gate({});
    render(<OnboardingGate />);
    await waitFor(() => expect(sessionStorage.getItem("as-gate-checked")).toBe("1"));
    expect(push).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("reminds someone who stopped half-way, and the reminder can be hidden", async () => {
    sessionStorage.clear();
    gate({ resume: true });
    render(<OnboardingGate />);
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Finish setting up Agent Studio · step 3 of 10");
    expect(within(banner).getByRole("link", { name: "Continue" })).toHaveAttribute("href", expect.stringMatching(/\/setup\/?$/));
    await userEvent.click(within(banner).getByRole("button", { name: "Hide this reminder" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("starts at step 1 for an unknown saved step, and stays quiet on the guide itself", async () => {
    sessionStorage.clear();
    gate({ resume: true, record: { step: "nonsense" } });
    const { unmount } = render(<OnboardingGate />);
    expect(await screen.findByRole("status")).toHaveTextContent("step 1 of 10");
    unmount();
    sessionStorage.clear();
    pathname = "/setup/";
    gate({ resume: true });
    render(<OnboardingGate />);
    await waitFor(() => expect(sessionStorage.getItem("as-gate-checked")).toBe("1"));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does nothing when the backend is offline, and works when storage is blocked", async () => {
    sessionStorage.clear();
    routeFetch({ "GET /api/easy/onboarding": () => jsonResponse({}, { status: 503 }) });
    render(<OnboardingGate />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(push).not.toHaveBeenCalled();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    gate({ show: true });
    render(<OnboardingGate />);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/setup/"));
  });

  it("treats an unknown path as the home page", async () => {
    sessionStorage.clear();
    (pathname as unknown) = null;
    gate({ show: true });
    render(<OnboardingGate />);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/setup/"));
  });

  it("raises a 'pending' flag while it waits for the server (so the welcome tour waits too), and lowers it after", async () => {
    sessionStorage.clear();
    let release: () => void = () => {};
    routeFetch({ "GET /api/easy/onboarding": () => new Promise<Response>((resolve) => { release = () => resolve(jsonResponse({ show: false, resume: false, steps: STEPS, record: { step: "welcome" } })); }) });
    render(<OnboardingGate />);
    await waitFor(() => expect(sessionStorage.getItem("as-gate-pending")).toBe("1"));
    release();
    await waitFor(() => expect(sessionStorage.getItem("as-gate-pending")).toBeNull());
    sessionStorage.clear();
    routeFetch({ "GET /api/easy/onboarding": () => jsonResponse({}, { status: 503 }) });
    render(<OnboardingGate />);
    await waitFor(() => expect(sessionStorage.getItem("as-gate-pending")).toBeNull());
  });
});
