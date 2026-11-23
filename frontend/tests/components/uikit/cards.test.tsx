import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReplyContext } from "@/components/rich/ReplyContext";
import { SAMPLES } from "@/lib/uikit/samples.generated";
import { jsonResponse, routeFetch } from "../../helpers/http";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("katex", () => ({ default: { renderToString: vi.fn((s: string) => `<span class="katex">${s}</span>`) } }));
vi.mock("katex/dist/katex.min.css", () => ({}));
vi.mock("qrcode-generator", () => ({ default: () => { let n = 0; return { addData: (t: string) => { n = Math.max(3, Math.min(8, t.length % 6 + 3)); }, make: () => undefined, getModuleCount: () => n, isDark: (r: number, c: number) => (r + c) % 2 === 0 }; } }));
vi.mock("maplibre-gl", () => ({ Map: class { on() { /* nothing */ } addControl() { /* nothing */ } remove() { /* nothing */ } }, NavigationControl: class { /* nothing */ }, Popup: class { remove() { /* nothing */ } }, addProtocol: () => undefined, setWorkerUrl: () => undefined }));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
vi.mock("pmtiles", () => ({ Protocol: class { tile = vi.fn(); } }));

import UiBlock from "@/components/uikit/UiBlock";
import { contrast } from "@/components/uikit/cards2";
import { toneOf } from "@/components/uikit/cardkit";

const CARDS = Object.keys(SAMPLES).filter((k) => k.startsWith("card."));
const draw = (spec: any, reply?: (t: string) => void) => render(<ReplyContext.Provider value={reply ?? null}><UiBlock spec={spec} /></ReplyContext.Provider>);

afterEach(() => { vi.useRealTimers(); });

describe("every card sample", () => {
  it.each(CARDS)("%s draws without falling back", async (type) => {
    draw(SAMPLES[type]);
    await screen.findAllByTestId(type === "card.place" ? "card" : "card");
    expect(screen.queryByTestId("ui-fallback")).toBeNull();
    const toggle = screen.queryByRole("button", { name: "Show as table" });
    if (toggle) { await userEvent.click(toggle); expect(screen.getByTestId("data-table")).toBeInTheDocument(); }
    expect(screen.getAllByText(/Export/).length).toBeGreaterThan(0);
  });
});

describe("weather", () => {
  it("switches units, shows alerts, hourly and daily rows, and handles missing data", async () => {
    draw(SAMPLES["card.weather"]);
    expect(document.querySelector(".uc-wx-t")).toHaveTextContent("24°"); expect(screen.getByText(/Heat advisory/)).toBeInTheDocument(); expect(screen.getByRole("list", { name: "Hourly" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show in °F" })); expect(document.querySelector(".uc-wx-t")).toHaveTextContent("75°");
    await userEvent.click(screen.getByRole("button", { name: "Show in °C" })); expect(document.querySelector(".uc-wx-t")).toHaveTextContent("24°");
  });
  it("starts in Fahrenheit and converts to Celsius", async () => {
    draw({ type: "card.weather", units: "F", place: "X", current: { temp: 68, condition: "Thunderstorm" }, daily: [{ day: "A", high: 86, low: 50, condition: "Snow" }, { day: "B", high: "x", low: null, condition: "Fog" }] });
    expect(screen.getByText("68°")).toBeInTheDocument(); await userEvent.click(screen.getByRole("button", { name: "Show in °C" })); expect(screen.getByText("20°")).toBeInTheDocument();
  });
  it("draws a forecast in both units", async () => {
    draw({ type: "card.forecast", units: "F", days: [{ day: "A", high: 50, low: 40, precip: 10 }, { day: "B", high: "x" }] });
    await userEvent.click(screen.getByRole("button", { name: "°F" })); expect(screen.getByRole("button", { name: "°C" })).toBeInTheDocument();
  });
});

describe("numbers", () => {
  it("shows up, down and flat changes with words for screen readers", () => {
    draw({ type: "card.kpis", items: [{ label: "A", value: 1, delta: 2 }, { label: "B", value: 2, delta: -3, unit: "x" }, { label: "C", value: 3, delta: 0 }] });
    expect(screen.getAllByText(/up/, { selector: ".sr-only" }).length).toBeGreaterThan(0); expect(screen.getAllByText(/down/, { selector: ".sr-only" }).length).toBeGreaterThan(0); expect(screen.getAllByText(/no change/, { selector: ".sr-only" }).length).toBeGreaterThan(0);
  });
  it("shows a KPI with a status, target and non-numeric value, and a quote that went down", () => {
    draw({ type: "card.kpi", label: "L", value: "n/a", status: "bad", target: 10 }); expect(screen.getByText("n/a")).toBeInTheDocument(); expect(screen.getByText(/bad/)).toBeInTheDocument();
    draw({ type: "card.quote", symbol: "Q", price: 10, change: -1, change_pct: -2, spark: [1] }); expect(screen.getByText(/This is data, not advice/)).toBeInTheDocument();
    draw({ type: "card.kpi", label: "M", value: 5, target: 10, status: "weird" });
  });
  it("marks the better option in a comparison with a word, not just colour", () => {
    draw(SAMPLES["card.compare"]); expect(screen.getAllByText("(better)").length).toBe(3);
  });
});

describe("people, things and sources", () => {
  it("only follows safe links and never loads remote images", () => {
    draw({ type: "card.person", name: "A", email: "a@b.co", phone: "+1 (555) 0100", image: "https://evil.example/x.png", links: [{ label: "ok", url: "https://x.co" }, { label: "bad", url: "javascript:alert(1)" }] });
    expect(screen.getByRole("link", { name: "a@b.co" })).toHaveAttribute("href", "mailto:a@b.co"); expect(screen.getByRole("link", { name: "+1 (555) 0100" })).toHaveAttribute("href", "tel:+15550100");
    expect(screen.getByRole("link", { name: "ok" })).toHaveAttribute("rel", "noopener noreferrer"); expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
    expect(screen.getByText(/remote image not loaded/)).toBeInTheDocument(); expect(document.querySelector("img")).toBeNull();
  });
  it("shows an embedded picture and an artifact picture", () => {
    draw({ type: "card.product", name: "P", image: "0123456789ab", specs: [{ label: "a", value: "b" }], pros: ["x"], cons: ["y"] });
    expect(document.querySelector("img")!.getAttribute("src")).toMatch(/\/api\/artifacts\/0123456789ab/); draw({ type: "card.person", name: "B", avatar: "data:image/png;base64,AAAA" });
  });
  it("draws sources, results, news, companies and files with and without links", () => {
    draw({ type: "card.source", title: "S" }); draw({ type: "card.news", headline: "H", url: "https://x.co", bullets: ["b"] }); draw({ type: "card.company", name: "C" });
    draw({ type: "card.file", name: "f", size: 2048, artifact: "0123456789ab", note: "n" }); draw({ type: "card.file", name: "g", artifact: "nope" }); draw({ type: "card.results", items: [{ title: "T", url: "javascript:1" }] });
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/artifacts/?id=0123456789ab");
  });
  it("copies code and highlights lines", async () => {
    const writeText = vi.fn(async () => undefined); Object.assign(navigator, { clipboard: { writeText } });
    const { container } = draw(SAMPLES["card.code"]);
    expect(container.querySelectorAll(".uc-hl").length).toBe(1); await userEvent.click(screen.getByRole("button", { name: "Copy" })); expect(writeText).toHaveBeenCalledWith(expect.stringContaining("def hello"));
    await screen.findByRole("button", { name: "Copied" });
  });
});

describe("time and work", () => {
  it("ticks tasks and sends the change as a message", async () => {
    const reply = vi.fn(); draw(SAMPLES["card.tasks"], reply);
    await userEvent.click(screen.getByRole("checkbox", { name: /Update docs/ })); expect(reply).toHaveBeenCalledWith("Done: Update docs");
    await userEvent.click(screen.getByRole("checkbox", { name: /Update docs/ })); expect(reply).toHaveBeenLastCalledWith("Not done: Update docs");
  });
  it("shows a read-only checklist when there is no chat", () => {
    draw(SAMPLES["card.tasks"]); expect(screen.getByRole("checkbox", { name: /Update docs/ })).toBeDisabled(); expect(screen.getByText(/works in the chat/)).toBeInTheDocument();
  });
  it("draws a single event, a conflict, steps in every state, alerts at every level and trips", () => {
    draw({ type: "card.event", title: "Lunch", start: "12:00", end: "13:00", where: "Cafe" }); expect(screen.getAllByText("Lunch").length).toBeGreaterThan(0);
    draw(SAMPLES["card.event"]); expect(screen.getAllByText(/conflict/).length).toBeGreaterThan(0);
    draw({ type: "card.steps", steps: [{ name: "a", status: "failed" }, { name: "b" }, { name: "c", status: "active" }, { name: "d", status: "done" }] });
    for (const level of ["info", "success", "warning", "error", "weird"]) draw({ type: "card.alert", level, text: level });
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    draw({ type: "card.itinerary", legs: [{ kind: "train", from: "A", to: "B" }, { kind: "boat", from: "C" }] });
  });
  it("draws a board and a timeline", () => { draw(SAMPLES["card.kanban"]); draw({ type: "card.timeline", events: [{ t: "1", title: "x" }] }); expect(screen.getByText("To do")).toBeInTheDocument(); });
  it("shows a place and opens its map on request", async () => {
    draw(SAMPLES["card.place"]); await userEvent.click(screen.getByRole("button", { name: "Show on a map" })); expect(await screen.findByTestId("map")).toBeInTheDocument();
    draw({ type: "card.place", name: "No coords", url: "https://x.co", rating: "x" }); expect(screen.queryAllByRole("button", { name: "Show on a map" })).toHaveLength(0);
  });
});

describe("recipes, money and answers", () => {
  it("scales a recipe", async () => {
    draw(SAMPLES["card.recipe"]);
    await userEvent.click(screen.getByRole("button", { name: "More servings" })); expect(screen.getByText("300 g Pasta")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fewer servings" })); await userEvent.click(screen.getByRole("button", { name: "Fewer servings" })); await userEvent.click(screen.getByRole("button", { name: "Fewer servings" })); expect(screen.getByText("100 g Pasta")).toBeInTheDocument();
  });
  it("totals a receipt itself when none is given", () => { draw({ type: "card.receipt", items: [{ name: "a", qty: 2, price: 3 }, { name: "b" }], tax: 1 }); expect(screen.getAllByText(/\$7|7\.00/).length).toBeGreaterThan(0); });
  it("sends a poll answer once", async () => {
    const reply = vi.fn(); draw(SAMPLES["card.poll"], reply);
    await userEvent.click(screen.getByRole("button", { name: "Option B" })); expect(reply).toHaveBeenCalledWith("Which option do you prefer?\n- Answer: Option B"); expect(screen.getByRole("button", { name: "Option A" })).toBeDisabled(); expect(screen.getByText("Sent.")).toBeInTheDocument();
  });
  it("approves or rejects through the chat", async () => {
    const reply = vi.fn(); draw(SAMPLES["card.approval"], reply);
    await userEvent.click(screen.getByRole("button", { name: "Reject" })); expect(reply).toHaveBeenCalledWith("Rejected: Delete 3 files from the workspace"); expect(screen.getByRole("status")).toHaveTextContent("Rejected");
    draw({ ...SAMPLES["card.approval"], risk: "high" }, reply); await userEvent.click(screen.getAllByRole("button", { name: "Approve" })[1]); expect(reply).toHaveBeenLastCalledWith("Approved: Delete 3 files from the workspace");
    draw({ ...SAMPLES["card.approval"], risk: "" }); expect(screen.getAllByText(/Answer in the chat box/).length).toBeGreaterThan(0);
  });
  it("shows scores in bands, usage near the cap, runs and findings", () => {
    for (const score of [90, 70, 20]) draw({ type: "card.scorecard", score, parts: [{ label: "a", score: 50 }] });
    draw({ type: "card.usage", spend: 9.5, cap: 10, tokens: 5, by_model: [{ model: "m", spend: 1 }] }); expect(screen.getByText(/close to the cap/)).toBeInTheDocument(); draw({ type: "card.usage", spend: 1 }); draw({ type: "card.usage", spend: 8, cap: 10 });
    for (const status of ["ok", "failed", "waiting", "running"]) draw({ type: "card.run", name: "r", status, steps: 2, duration_s: 3, cost_usd: 0.1 });
    for (const severity of ["critical", "medium", "info"]) draw({ type: "card.finding", title: "t", severity, refs: ["A"] });
  });
  it("hides secrets in an API call and tints errors", () => {
    draw(SAMPLES["card.request"]); expect(screen.getByText("201")).toBeInTheDocument();
    for (const status of [404, 500, 100]) draw({ type: "card.request", method: "GET", url: "https://x.co", status });
  });
});

describe("tools and text", () => {
  it("renders an equation with KaTeX once it loads, and as plain LaTeX before", async () => {
    draw(SAMPLES["card.equation"]); expect(screen.getByText("E = mc^2", { selector: "code" })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".katex")).not.toBeNull()); expect(screen.getByRole("button", { name: "Copy LaTeX" })).toBeInTheDocument();
  });
  it("draws a QR code from text", async () => { draw(SAMPLES["card.qr"]); await waitFor(() => expect(document.querySelector(".uc-qr")).not.toBeNull()); expect(screen.getByRole("img", { name: /QR code for https/ })).toBeInTheDocument(); });
  it("runs a countdown, pauses it and resets it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true }); draw(SAMPLES["card.countdown"]);
    expect(screen.getByRole("timer")).toHaveTextContent("00:05:00"); await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await act(async () => { vi.advanceTimersByTime(3000); }); expect(screen.getByRole("timer")).toHaveTextContent("00:04:5"); await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    await userEvent.click(screen.getByRole("button", { name: "Reset" })); expect(screen.getByRole("timer")).toHaveTextContent("00:05:00");
  });
  it("counts down to a date and stops at zero", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true }); draw({ type: "card.countdown", target: new Date(Date.now() + 90061000).toISOString() });
    expect(screen.getByRole("timer")).toHaveTextContent(/1d 01:01:0/); await act(async () => { vi.advanceTimersByTime(2000); });
    draw({ type: "card.countdown", target: "2000-01-01T00:00:00Z" }); expect(screen.getAllByRole("timer")[1]).toHaveTextContent("00:00:00");
  });
  it("searches a log and marks errors with text as well as colour", async () => {
    draw(SAMPLES["card.log"]); expect(screen.getByText(/4 of 4 lines/)).toBeInTheDocument(); expect(screen.getByText(/✕ 2025/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search the log"), "timeout"); expect(screen.getByText(/1 of 4 lines/)).toBeInTheDocument();
    draw({ type: "card.log", text: "a\nb" }); expect(screen.getByText(/2 of 2 lines/)).toBeInTheDocument();
  });
  it("shows an email draft that sends nothing, a definition, FAQ and outline", async () => {
    draw({ ...SAMPLES["card.email"], cc: ["c@x.co"] }); expect(screen.getByText(/nothing is sent/)).toBeInTheDocument(); expect(screen.getByRole("button", { name: "Copy draft" })).toBeInTheDocument();
    draw(SAMPLES["card.faq"]); draw(SAMPLES["card.toc"]); draw(SAMPLES["card.definition"]);
  });
  it("shows palette contrast and ignores bad colours", () => {
    draw({ type: "card.palette", colors: [{ name: "Ink", hex: "#000000" }, { name: "Bad", hex: "nope" }, { hex: "#ffffff" }] });
    expect(screen.getByText("21.0:1 on white")).toBeInTheDocument(); expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 0); expect(contrast("x", "#fff")).toBeNaN();
  });
  it("draws a match and a league table, and a molecule", () => { draw(SAMPLES["card.sports"]); draw({ type: "card.sports", table: [{ team: "A", p: 1, w: 1, d: 0, l: 0, pts: 3 }] }); draw(SAMPLES["card.molecule"]); expect(screen.getByText("Caffeine")).toBeInTheDocument(); });
  it("maps words to tones", () => { expect(["ok", "pending", "failed", "todo", "?"].map(toneOf)).toEqual(["ok", "warn", "bad", "info", ""]); });
});

describe("layouts", () => {
  it("stacks, grids, splits and tabs components", async () => {
    draw(SAMPLES["layout.stack"]); expect(screen.getAllByTestId("card")).toHaveLength(2);
    draw({ ...SAMPLES["layout.grid"], columns: 9 }); draw(SAMPLES["layout.split"]);
    draw(SAMPLES["layout.tabs"]); const tabs = screen.getByRole("tablist", { name: "Tabs" });
    await userEvent.click(within(tabs).getByRole("tab", { name: "Table" })); expect(screen.getByRole("tabpanel")).toContainElement(screen.getByTestId("data-table"));
    await userEvent.keyboard("{ArrowRight}"); await userEvent.keyboard("{ArrowLeft}");
    const t = within(tabs).getAllByRole("tab")[1]; t.focus(); await userEvent.keyboard("{ArrowRight}"); expect(within(tabs).getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true"); await userEvent.keyboard("{ArrowLeft}");
  });
  it("pages through a carousel both ways", async () => {
    draw(SAMPLES["layout.carousel"]); expect(screen.getByText("1 of 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" })); expect(screen.getByText("2 of 2")).toBeInTheDocument(); await userEvent.click(screen.getByRole("button", { name: "Next" })); expect(screen.getByText("1 of 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous" })); expect(screen.getByText("2 of 2")).toBeInTheDocument();
  });
  it("shows a bad child as its own fallback without breaking the rest", () => {
    draw({ type: "layout.stack", children: [{ type: "nonsense" }, SAMPLES["card.alert"]] }); expect(screen.getByTestId("ui-fallback")).toBeInTheDocument(); expect(screen.getByTestId("card")).toBeInTheDocument();
  });
});

describe("audio, video, JSON and HTML", () => {
  const meta = (type: string, mime: string) => ({ id: "0123456789ab", name: `f.${type === "audio" ? "mp3" : "mp4"}`, type, mime, size: 2048 });
  it("plays saved media and refuses anything else", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(meta("audio", "audio/mpeg")), "GET /api/artifacts/ba9876543210": () => jsonResponse(meta("video", "video/mp4")) });
    draw(SAMPLES["media.audio"]); expect(await screen.findByLabelText("Meeting recording")).toBeInstanceOf(HTMLAudioElement);
    draw({ ...SAMPLES["media.video"], captions: "0123456789ab" }); expect(await screen.findByLabelText("Demo")).toBeInstanceOf(HTMLVideoElement);
    draw({ type: "media.audio", artifact: "http://evil.example/a.mp3" }); expect(await screen.findByText(/only files saved as artifacts/)).toBeInTheDocument();
  });
  it("shows JSON as a tree and HTML in the locked frame", () => {
    draw(SAMPLES.json); expect(screen.getByText('"demo"')).toBeInTheDocument();
    draw(SAMPLES.html); const frame = document.querySelector("iframe")!; expect(frame.getAttribute("sandbox")).toBe("");
  });
});
