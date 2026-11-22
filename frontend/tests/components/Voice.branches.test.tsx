import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VoiceCard, type VoiceCfg } from "@/components/Voice";
import { jsonResponse, routeFetch } from "../helpers/http";

const base: VoiceCfg = {
  configured: true, key_source: "store", key_hint: "…abcd", voice_id: "v1", tts_model: "m1", stt_model: "s1",
  auto_read: false, dictation: true, char_cap: 1000, chars_used_month: 100,
};

function show(over: Partial<VoiceCfg>) {
  routeFetch({
    "GET /api/voice/settings": () => jsonResponse({ ...base, ...over }),
    "GET /api/voice/voices": () => jsonResponse([]),
    "GET /api/voice/models": () => jsonResponse([]),
    "GET /api/voice/subscription": () => jsonResponse({ tier: "free", character_count: 1, character_limit: 2 }),
  });
  render(<VoiceCard />);
}

describe("VoiceCard usage display", () => {
  it("shows no cap text and no usage bar when the cap is 0", async () => {
    show({ char_cap: 0, chars_used_month: 5 });
    expect(await screen.findByText(/5 characters read aloud\./)).toBeInTheDocument();
    expect(document.querySelector(".bar")).toBeNull();
  });

  it("uses a warning bar between 70% and 90% of the cap", async () => {
    show({ chars_used_month: 800 });
    await screen.findByText(/800 characters read aloud of 1,000/);
    expect(document.querySelector(".bar i")).toHaveClass("w");
  });

  it("uses a plain bar below 70% of the cap", async () => {
    show({ chars_used_month: 100 });
    await screen.findByText(/100 characters read aloud of 1,000/);
    const bar = document.querySelector(".bar i") as HTMLElement;
    expect(bar.className).toBe("");
    expect(bar.style.width).toBe("10%");
  });

  it("shows plain 'connected' when the key has no hint and is not from the environment", async () => {
    show({ key_hint: null, key_source: "store" });
    expect(await screen.findByText("connected")).toBeInTheDocument();
  });
});
