import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveSettingsForm } from "@/components/LiveSettings";
import { LiveClient } from "@/lib/live";
import { FakeSocket, fakeDeps, livePrices, liveSettings } from "../../helpers/liveFakes";
import { jsonResponse, routeFetch } from "../../helpers/http";

afterEach(() => vi.unstubAllGlobals());

const providers = [{ id: "ollama", label: "Ollama", enabled: true, kind: "ollama", catalog: { needs_key: false } }, { id: "vertex_ai", label: "Google Vertex AI", enabled: true, kind: "vertex_ai", catalog: { needs_key: false } }] as any;
const gem = (over = {}) => ({ configured: true, problems: [], provider: "vertex_ai", project: "my-proj", location: "us-central1", model: "gemini-3.8-live", auth_mode: "adc", voices: ["Puck", "Aoede"],
  languages: ["en-US", "de-DE"], endpoint: "wss://x", avatar_examples: ["Ben"], notes: ["Your audio goes to Google Cloud."], prices: { in: {}, out: {} }, ...over });
const g = { ...liveSettings, live_engine: "gemini" as const };

describe("Gemini section of the live settings", () => {
  it("switches engine with the toggle and hides the local-only settings for Gemini", async () => {
    routeFetch({ "GET /api/live/gemini/status": () => jsonResponse(gem()), "GET /api/live/prices": () => jsonResponse(livePrices) });
    const put = vi.fn();
    const { rerender } = render(<LiveSettingsForm s={liveSettings} voices={["af_heart"]} providers={providers} agents={[]} running={false} put={put} />);
    expect(screen.getByLabelText("Voice")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Google Gemini Live" }));
    expect(put).toHaveBeenLastCalledWith({ live_engine: "gemini" });
    rerender(<LiveSettingsForm s={g} voices={["af_heart"]} providers={providers} agents={[{ id: "a1", name: "Helper" }]} running={false} put={put} />);
    expect(await screen.findByTestId("gemini-section")).toBeInTheDocument();
    expect(screen.queryByLabelText("Speed")).toBeNull();
    expect(screen.getByText(/Gemini can call it as a tool/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "On this PC" }));
    expect(put).toHaveBeenLastCalledWith({ live_engine: "local" });
  });

  it("explains what is missing, and lets you change each Gemini setting", async () => {
    routeFetch({ "GET /api/live/gemini/status": () => jsonResponse(gem({ configured: false, problems: ["Set the Google Cloud project: open Providers."] })), "GET /api/live/prices": () => jsonResponse(livePrices) });
    const put = vi.fn();
    render(<LiveSettingsForm s={g} voices={[]} providers={providers} agents={[]} running={false} put={put} />);
    expect(await screen.findByTestId("gemini-problems")).toHaveTextContent("Set the Google Cloud project");
    expect(screen.getByRole("button", { name: "Test the connection" })).toBeDisabled();
    expect(screen.getByText("Your audio goes to Google Cloud.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Providers" })).toHaveAttribute("href", "/settings/#providers");
    await userEvent.selectOptions(screen.getByLabelText("Gemini voice"), "Aoede");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_voice: "Aoede" });
    await userEvent.selectOptions(screen.getByLabelText("Gemini language"), "de-DE");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_language: "de-DE" });
    await userEvent.selectOptions(screen.getByLabelText("Vertex provider"), "vertex_ai");
    fireEvent.change(screen.getByLabelText("Longest session"), { target: { value: "20" } });
    expect(put).toHaveBeenLastCalledWith({ live_gemini_max_minutes: 20 });
    const blur = (label: string, value: string) => { const el = screen.getByLabelText(label); fireEvent.change(el, { target: { value } }); fireEvent.blur(el); };
    blur("Gemini model", "gemini-3.9-live");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_model: "gemini-3.9-live" });
    blur("Avatar name", "Ben");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_avatar_name: "Ben" });
    blur("Cost limit", "2.5");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_cost_cap_usd: 2.5 });
    blur("Gemini region", "europe-west4");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_location: "europe-west4" });
    blur("Gemini endpoint", "ws://127.0.0.1:9");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_endpoint: "ws://127.0.0.1:9" });
    blur("Avatar video bitrate", "2000000");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_video_bitrate: 2000000 });
    blur("Avatar audio bitrate", "64000");
    expect(put).toHaveBeenLastCalledWith({ live_gemini_audio_bitrate: 64000 });
    put.mockClear();
    blur("Gemini model", "gemini-3.8-live");                       // unchanged values are not saved again
    blur("Avatar name", "");
    expect(put).not.toHaveBeenCalled();
  });

  it("tests the connection and shows Google's answer in words", async () => {
    let ok = true;
    routeFetch({ "GET /api/live/gemini/status": () => jsonResponse(gem()), "GET /api/live/prices": () => jsonResponse(livePrices),
      "POST /api/live/gemini/test": () => (ok ? jsonResponse({ ok: true, ms: 420, session: {} }) : jsonResponse({ detail: "Gemini Live refused the session: Permission denied (code 1008)" }, { status: 502 })) });
    render(<LiveSettingsForm s={g} voices={[]} providers={providers} agents={[]} running={false} put={vi.fn()} />);
    expect(await screen.findByText(/Ready: project my-proj, us-central1/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Test the connection" }));
    expect(await screen.findByTestId("gemini-test")).toHaveTextContent("Google accepted the session in 420 ms.");
    ok = false;
    await userEvent.click(screen.getByRole("button", { name: "Test the connection" }));
    await waitFor(() => expect(screen.getByTestId("gemini-test")).toHaveTextContent("Permission denied (code 1008)"));
    expect(screen.getByTestId("gemini-test")).toHaveAttribute("role", "alert");
  });

  it("copes with no status yet, a provider that is not in the list, and a running session", async () => {
    routeFetch({ "GET /api/live/gemini/status": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/live/prices": () => jsonResponse(livePrices) });
    render(<LiveSettingsForm s={{ ...g, live_gemini_provider: "my_vertex" }} voices={[]} providers={providers} agents={[]} running put={vi.fn()} />);
    expect(await screen.findByTestId("gemini-section")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "my_vertex" })).toBeInTheDocument();
    expect(screen.getByLabelText("Vertex provider")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test the connection" })).toBeDisabled();
    expect(screen.queryByTestId("gemini-problems")).toBeNull();
  });
});

describe("LiveClient with Gemini Live", () => {
  it("passes the engine and avatar to the server, reports video chunks, notices and the first sound of each answer", async () => {
    const deps = fakeDeps();
    const ev = { onVideo: vi.fn(), onNotice: vi.fn(), onAudioStart: vi.fn(), onState: vi.fn() };
    const c = new LiveClient(ev, deps);
    await c.start({ engine: "gemini", avatar: true, barge_in: false });
    const ws = FakeSocket.last;
    ws.onopen!();
    expect(ws.json()[0]).toEqual({ type: "start", engine: "gemini", avatar: true, barge_in: false });
    ws.emit({ type: "video", mime: "video/mp4", data: "QUJD" });
    ws.emit({ type: "notice", message: "careful" });
    expect(ev.onVideo).toHaveBeenCalledWith("video/mp4", "QUJD");
    expect(ev.onNotice).toHaveBeenCalledWith("careful");
    ws.emit({ type: "state", state: "thinking" });
    ws.audio(); ws.audio();
    expect(ev.onAudioStart).toHaveBeenCalledTimes(1);                  // once per answer
    ws.emit({ type: "state", state: "thinking" });
    ws.audio();
    expect(ev.onAudioStart).toHaveBeenCalledTimes(2);
    c.stop();
  });
});
