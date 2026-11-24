"use client";
// In the agent editor and the wizard: the agent's Live profile. With it the agent can be talked to in live mode as itself: its engine (this PC or Google Gemini Live),
// voice-only or avatar, voice, language, avatar and budget, together with its normal instructions, tools and knowledge.
import type { LiveProfile } from "@/lib/live";
import InfoTip from "./InfoTip";

export const LIVE_OFF: LiveProfile = { enabled: false, engine: "local", mode: "voice", voice: "", language: "", avatar_name: "", provider: "", model: "", agent_turns: false, budget_usd: 0 };
export const GEMINI_VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
  "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"];
export const LOCAL_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis"];
export const LANGS = ["en-US", "en-IN", "ar-EG", "bn-BD", "nl-NL", "fr-FR", "de-DE", "hi-IN", "id-ID", "it-IT", "ja-JP", "ko-KR", "mr-IN", "pl-PL", "pt-BR", "ro-RO", "ru-RU", "es-US", "ta-IN", "te-IN", "th-TH", "tr-TR", "uk-UA", "vi-VN"];

/** The profile of an agent as stored ({} = none). */
export const liveOf = (a: { live?: Partial<LiveProfile> | null } | undefined): Partial<LiveProfile> => a?.live ?? {};

/** A short label for the agents list; null when the agent has no Live profile. */
export function liveBadge(a: { live?: Partial<LiveProfile> | null }): string | null {
  const p = liveOf(a);
  if (!Object.keys(p).length) return null;
  if (!p.enabled) return "Live off";
  return `Live · ${p.engine === "gemini" ? "Gemini" : "local"} · ${p.mode === "avatar" ? "avatar" : "voice"}`;
}

export default function AgentLive({ agent, onChange, compact }: { agent: { live?: Partial<LiveProfile> | null }; onChange: (live: Partial<LiveProfile>) => void; compact?: boolean }) {
  const has = Object.keys(liveOf(agent)).length > 0;
  const p: LiveProfile = { ...LIVE_OFF, ...liveOf(agent) };
  const set = (patch: Partial<LiveProfile>) => onChange({ ...p, ...patch });
  const gem = p.engine === "gemini";
  return (
    <div className={compact ? "" : "card"} id="ag-live" data-testid="agent-live" style={{ marginTop: compact ? 10 : 14 }}>
      <div className="card-head"><b>Live</b><InfoTip id="live-agent" /></div>
      <div className="small mut">Talk to this agent by voice, as itself: its instructions, tools and knowledge, with a voice or an avatar.</div>
      <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={has && p.enabled} onChange={(e) => (e.target.checked ? set({ enabled: true }) : has ? set({ enabled: false }) : undefined)} aria-label="Can be used in live mode" /> Can be used in live mode</label>
      {has && !p.enabled && <div className="small mut" style={{ marginLeft: 24 }}>Switched off: live mode refuses this agent.</div>}
      {p.enabled && <div className="grid" style={{ marginTop: 8 }}>
        <label>Where it runs<select value={p.engine} aria-label="Live engine" onChange={(e) => set({ engine: e.target.value as LiveProfile["engine"], voice: "", language: "" })}>
          <option value="local">On this PC (Ollama, Whisper, Kokoro)</option><option value="gemini">Google Gemini Live (cloud)</option></select></label>
        <label>Mode<select value={p.mode} aria-label="Live mode" onChange={(e) => set({ mode: e.target.value as LiveProfile["mode"] })}><option value="voice">Voice only</option><option value="avatar">With avatar</option></select></label>
        <label>Voice<select value={p.voice} aria-label="Live voice" onChange={(e) => set({ voice: e.target.value })}><option value="">The Live default</option>{(gem ? GEMINI_VOICES : LOCAL_VOICES).map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
        {gem && <label>Language<select value={p.language} aria-label="Live language" onChange={(e) => set({ language: e.target.value })}><option value="">The Live default</option>{LANGS.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>}
        {gem && p.mode === "avatar" && <label>Avatar<input list="live-avatar-names" aria-label="Live avatar" value={p.avatar_name} placeholder="for example Ben" onChange={(e) => set({ avatar_name: e.target.value })} />
          <datalist id="live-avatar-names"><option value="Ben" /></datalist></label>}
        {!gem && <label className="check"><input type="checkbox" checked={p.agent_turns} onChange={(e) => set({ agent_turns: e.target.checked })} aria-label="Use the agent's tools in live mode" /> Use this agent&apos;s tools (slower: each answer is spoken after it is complete)</label>}
        <label>Budget per session, $ (0 = the Live settings&apos; limit)<input type="number" min={0} step={0.05} aria-label="Live budget" value={p.budget_usd} onChange={(e) => set({ budget_usd: Number(e.target.value) || 0 })} /></label>
      </div>}
      {p.enabled && <div className="small mut" style={{ marginTop: 6 }}>
        {gem ? "Gemini can use this agent's tools through one function that runs a normal turn with it. Google bills your project; the budget warns at 80% and stops at 100%."
          : p.agent_turns ? "Each answer is a full turn of this agent (tools, knowledge, memory), then spoken. Free on this PC unless the agent uses a cloud model."
            : "The model speaks as this agent (its instructions) with no tools, for the lowest delay."}</div>}
    </div>
  );
}
