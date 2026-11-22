import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TrainerPanel, { summarize, type Job } from "@/components/TrainerPanel";
import { jsonResponse, routeFetch } from "../helpers/http";

const fw = (o: Record<string, unknown>) => ({ label: o.id, blurb: `${o.id} blurb`, licence: "Apache-2.0", size_mb: 900, packages: ["x==1"], notes: "A note.", state: "not_installed", version: null, error: null, ...o });
const status = (over: Record<string, unknown> = {}, install: Record<string, unknown> = {}) => ({
  frameworks: [fw({ id: "ludwig", label: "Ludwig", state: "ready", version: "0.17.10", size_mb: 3500 }), fw({ id: "pytorch", label: "PyTorch" }), fw({ id: "keras", label: "Keras / TensorFlow", state: "failed", error: "Step failed" })],
  install: { running: false, framework: null, log: [], error: null, ...install }, jobs_running: 0, ...over,
});
const job = (o: Partial<Job> & { id: string }): Job => ({ state: "done", mode: "train", framework: "pytorch", task: "classification", target: "y", name: "", epoch: null, epochs: null, progress: null, error: null, metrics: null, ...o });
const toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);
beforeEach(() => { toasts.length = 0; window.addEventListener("as:toast", onToast); });
afterEach(() => { window.removeEventListener("as:toast", onToast); vi.useRealTimers(); });

function setup(routes: Record<string, any> = {}) {
  const r = routeFetch({
    "GET /api/trainer/status": () => jsonResponse(status()), "GET /api/trainer/jobs": () => jsonResponse([]),
    "GET /api/mcp": () => jsonResponse([{ id: "trainer", enabled: false, ready: false }, { id: "other", enabled: true, ready: true }]), ...routes,
  });
  render(<TrainerPanel />);
  return r;
}
const fwCard = (id: string) => document.querySelector(`[data-framework="${id}"]`) as HTMLElement;

describe("summarize", () => {
  it("puts a job's headline numbers in words", () => {
    expect(summarize(null)).toBe("");
    expect(summarize({ accuracy: 0.9, majority_class_baseline: 0.6, macro_f1: 0.8 })).toBe("accuracy 0.9 (always guessing the most common answer: 0.6), macro F1 0.8");
    expect(summarize({ mae: 2, mean_baseline_mae: 5, r2: 0.7 })).toBe("mean error 2 (guessing the average: 5), R² 0.7");
    expect(summarize({ framework: "ludwig", "y.accuracy": 0.7 })).toBe("y.accuracy 0.7");
    expect(summarize({ framework: "ludwig" })).toBe("");
  });
});

describe("TrainerPanel: frameworks", () => {
  it("shows each framework's state, size and licence", async () => {
    setup();
    await screen.findByText("Ludwig");
    expect(within(fwCard("ludwig")).getByText("Ready 0.17.10")).toBeInTheDocument();
    expect(within(fwCard("ludwig")).getByText(/Download about 3.5 GB · Apache-2.0/)).toBeInTheDocument();
    expect(within(fwCard("pytorch")).getByText("Not installed")).toBeInTheDocument();
    expect(within(fwCard("pytorch")).getByText(/900 MB/)).toBeInTheDocument();
    expect(within(fwCard("keras")).getByText("Failed")).toBeInTheDocument();
    expect(within(fwCard("keras")).getByText("Step failed")).toBeInTheDocument();
    expect(within(fwCard("keras")).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByText(/No jobs yet/)).toBeInTheDocument();
  });

  it("installs only after the licence and size are accepted", async () => {
    const { calls } = setup({ "POST /api/trainer/install": () => jsonResponse(status()) });
    await screen.findByText("PyTorch");
    await userEvent.click(within(fwCard("pytorch")).getByRole("button", { name: "Install…" }));
    expect(within(fwCard("pytorch")).getByText("A note.")).toBeInTheDocument();
    const go = within(fwCard("pytorch")).getByRole("button", { name: "Install PyTorch" });
    expect(go).toBeDisabled();
    await userEvent.selectOptions(within(fwCard("pytorch")).getByLabelText("Build"), "cpu");
    await userEvent.click(within(fwCard("pytorch")).getByRole("checkbox"));
    await userEvent.click(go);
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ framework: "pytorch", accepted: true, device: "cpu" }));
    await userEvent.click(within(fwCard("keras")).getByRole("button", { name: "Try again" }));
    expect(within(fwCard("keras")).queryByLabelText("Build")).toBeNull();                      // only PyTorch has the build choice
    await userEvent.click(within(fwCard("keras")).getByRole("button", { name: "Try again" }));    // second click closes the plan
    expect(within(fwCard("keras")).queryByRole("checkbox")).toBeNull();
  });

  it("shows an install refusal", async () => {
    setup({ "POST /api/trainer/install": () => jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) });
    await screen.findByText("PyTorch");
    await userEvent.click(within(fwCard("pytorch")).getByRole("button", { name: "Install…" }));
    await userEvent.click(within(fwCard("pytorch")).getByRole("checkbox"));
    await userEvent.click(within(fwCard("pytorch")).getByRole("button", { name: "Install PyTorch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not enough free disk space");
  });

  it("polls while an install runs and shows its log", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    setup({ "GET /api/trainer/status": () => jsonResponse(++n === 1
      ? status({ frameworks: [fw({ id: "pytorch", label: "PyTorch", state: "installing" })] }, { running: true, framework: "pytorch", log: ["== Install PyTorch", "Collecting torch"] })
      : status()) });
    expect(await screen.findByLabelText("install log")).toHaveTextContent("Collecting torch");
    expect(within(fwCard("pytorch")).getByRole("button", { name: "Install…" })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    await waitFor(() => expect(screen.queryByLabelText("install log")).toBeNull());
    expect(n).toBeGreaterThanOrEqual(2);
  });

  it("removes a framework after a confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    const { calls } = setup({ "POST /api/trainer/uninstall": () => jsonResponse(status()) });
    await screen.findByText("Ludwig");
    await userEvent.click(screen.getByRole("button", { name: "Remove Ludwig" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Remove Ludwig" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ framework: "ludwig" }));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("reports a refused removal", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "POST /api/trainer/uninstall": () => jsonResponse({ detail: "a job is running" }, { status: 422 }) });
    await screen.findByText("Ludwig");
    await userEvent.click(screen.getByRole("button", { name: "Remove Ludwig" }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "a job is running", tone: "err" }));
  });

  it("lets agents use the trainer only once something is installed", async () => {
    const { calls } = setup({ "PUT /api/mcp/trainer": () => jsonResponse({}) });
    const box = await screen.findByRole("checkbox", { name: "Let agents use it" });
    expect(box).toBeEnabled();
    await userEvent.click(box);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ enabled: true }));
    await waitFor(() => expect(toasts[0].text).toBe("Agents can use the trainer"));
  });

  it("can switch the trainer off again", async () => {
    const { calls } = setup({ "GET /api/mcp": () => jsonResponse([{ id: "trainer", enabled: true, ready: true }]), "PUT /api/mcp/trainer": () => jsonResponse({}) });
    const box = await screen.findByRole("checkbox", { name: "Let agents use it" });
    expect(box).toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ enabled: false }));
    await waitFor(() => expect(toasts[0].text).toBe("Trainer switched off for agents"));
  });

  it("the switch is off when nothing is installed, and a failed switch reports why", async () => {
    setup({ "GET /api/trainer/status": () => jsonResponse(status({ frameworks: [fw({ id: "pytorch", label: "PyTorch" })] })) });
    expect(await screen.findByRole("checkbox", { name: "Let agents use it" })).toBeDisabled();
  });

  it("reports a refused switch", async () => {
    setup({ "PUT /api/mcp/trainer": () => jsonResponse({ detail: "no" }, { status: 422 }) });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Let agents use it" }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "no", tone: "err" }));
  });

  it("works without a trainer MCP row and says when the backend is offline", async () => {
    setup({ "GET /api/mcp": () => jsonResponse([]) });
    await screen.findByText("Ludwig");
    expect(screen.queryByRole("checkbox", { name: "Let agents use it" })).toBeNull();
  });

  it("shows an offline message", async () => {
    routeFetch({ "GET /api/trainer/status": () => { throw new Error("refused"); }, "GET /api/trainer/jobs": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]) });
    render(<TrainerPanel />);
    expect(await screen.findByText(/Backend offline: refused/)).toBeInTheDocument();
  });
});

describe("TrainerPanel: jobs", () => {
  it("lists jobs with their result, progress or error, and cancels a running one", async () => {
    const jobs = [
      job({ id: "aaaaaaaaaa", name: "churn", metrics: { accuracy: 0.9, majority_class_baseline: 0.6, macro_f1: 0.8 } }),
      job({ id: "bbbbbbbbbb", state: "running", mode: "evaluate", progress: 0.4, epoch: 4, epochs: 10 }),
      job({ id: "cccccccccc", state: "failed", error: "worker stopped" }),
      job({ id: "dddddddddd", state: "starting", progress: null }),
      job({ id: "eeeeeeeeee", state: "running", progress: 0.1, epoch: null, epochs: null }),
    ];
    const { calls } = setup({ "GET /api/trainer/jobs": () => jsonResponse(jobs), "GET /api/trainer/status": () => jsonResponse(status({ jobs_running: 0 })), "POST /api/trainer/jobs/bbbbbbbbbb/cancel": () => jsonResponse({}) });
    const row = (id: string) => document.querySelector(`[data-job="${id}"]`) as HTMLElement;
    await screen.findByText("churn");
    expect(within(row("aaaaaaaaaa")).getByText(/accuracy 0.9/)).toBeInTheDocument();
    expect(within(row("bbbbbbbbbb")).getByText("4/10 epochs")).toBeInTheDocument();
    expect(within(row("bbbbbbbbbb")).getByText(/evaluate · pytorch/)).toBeInTheDocument();
    expect(within(row("cccccccccc")).getByText("worker stopped")).toBeInTheDocument();
    expect(within(row("eeeeeeeeee")).getByText("0/? epochs")).toBeInTheDocument();
    expect(within(row("aaaaaaaaaa")).queryByRole("button", { name: "Cancel" })).toBeNull();
    await userEvent.click(within(row("bbbbbbbbbb")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname.endsWith("/cancel"))).toBe(true));
  });

  it("reports a refused cancel", async () => {
    setup({ "GET /api/trainer/jobs": () => jsonResponse([job({ id: "ffffffffff", state: "running" })]), "POST /api/trainer/jobs/ffffffffff/cancel": () => jsonResponse({ detail: "already over" }, { status: 422 }) });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "already over", tone: "err" }));
  });
});
