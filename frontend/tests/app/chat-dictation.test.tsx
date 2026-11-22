import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
}));

const openai = { id: "openai", kind: "openai", label: "OpenAI", enabled: true };

describe("chat composer: dictation", () => {
  it("offers the microphone when voice is configured with dictation on", async () => {
    routeFetch({
      "GET /api/providers": () => jsonResponse([openai]),
      "GET /api/agents": () => jsonResponse([]),
      "GET /api/chat/sessions": () => jsonResponse([]),
      "GET /api/voice/settings": () => jsonResponse({
        configured: true, key_source: "store", key_hint: "…abcd", voice_id: "v1", tts_model: "m1", stt_model: "s1",
        auto_read: false, dictation: true, char_cap: 0, chars_used_month: 0,
      }),
    });
    await loadVoice(true);
    render(<Page />);
    expect(await screen.findByTitle("Dictate (ElevenLabs)")).toBeInTheDocument();
  });

  it("puts the transcript into the message box", async () => {
    const trackStop = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: trackStop }] });
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    class FakeRecorder {
      mimeType = "";
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start = vi.fn();
      stop = vi.fn(() => {
        this.ondataavailable?.({ data: new Blob(["audio"]) });
        this.onstop?.();
      });
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    routeFetch({
      "GET /api/providers": () => jsonResponse([openai]),
      "GET /api/agents": () => jsonResponse([]),
      "GET /api/chat/sessions": () => jsonResponse([]),
      "GET /api/voice/settings": () => jsonResponse({
        configured: true, key_source: "store", key_hint: "…abcd", voice_id: "v1", tts_model: "m1", stt_model: "s1",
        auto_read: false, dictation: true, char_cap: 0, chars_used_month: 0,
      }),
      "POST /api/voice/stt": () => jsonResponse({ text: "dictated words" }),
    });
    await loadVoice(true);
    render(<Page />);
    await userEvent.click(await screen.findByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByTitle("Stop and transcribe"));
    expect(await screen.findByLabelText("Message")).toHaveValue("dictated words");
    expect(trackStop).toHaveBeenCalled();
  });
});
