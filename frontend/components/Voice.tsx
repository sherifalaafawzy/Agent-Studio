"use client";
// ElevenLabs voice: Listen (text-to-speech) on answers, dictation (speech-to-text) in the composer, and the Settings card.
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, ApiError, api } from "@/lib/api";

export type VoiceCfg = { configured: boolean; key_source: string | null; key_hint: string | null; voice_id: string; tts_model: string; stt_model: string;
  auto_read: boolean; dictation: boolean; char_cap: number; chars_used_month: number };

let shared: Promise<VoiceCfg | null> | null = null;
export function loadVoice(refresh = false): Promise<VoiceCfg | null> {
  if (refresh || !shared) shared = api<VoiceCfg>("/api/voice/settings").catch(() => null);
  return shared;
}
export function useVoice(): VoiceCfg | null {
  const [v, setV] = useState<VoiceCfg | null>(null);
  useEffect(() => { loadVoice().then(setV); const on = () => loadVoice(true).then(setV); window.addEventListener("as:voice", on); return () => window.removeEventListener("as:voice", on); }, []);
  return v;
}

// one player for the whole app: starting a new clip stops the previous one
let current: { audio: HTMLAudioElement; url: string; stop: () => void } | null = null;
export function stopSpeaking() { current?.stop(); current = null; }

export async function speak(text: string, onEnd?: () => void): Promise<void> {
  stopSpeaking();
  const res = await fetch(API_BASE + "/api/voice/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (!res.ok) { const b = await res.json().catch(() => null); throw new ApiError(res.status, b?.detail ?? b); }
  const url = URL.createObjectURL(await res.blob());
  const audio = new Audio(url);
  const stop = () => { audio.pause(); URL.revokeObjectURL(url); onEnd?.(); };
  audio.onended = () => { if (current?.audio === audio) current = null; stop(); };
  current = { audio, url, stop };
  await audio.play();
}

const Speaker = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 9v6h4l5 4V5L8 9H4Z" /><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" /></svg>;
const Mic = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>;

export function ListenButton({ text, autoPlay = false }: { text: string; autoPlay?: boolean }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [err, setErr] = useState("");
  const go = useCallback(async () => {
    if (state === "playing") { stopSpeaking(); setState("idle"); return; }
    setState("loading"); setErr("");
    try { await speak(text, () => setState("idle")); setState("playing"); }
    catch (e) { setState("idle"); setErr((e as Error).message); }
  }, [state, text]);
  const did = useRef(false);
  useEffect(() => { if (autoPlay && !did.current) { did.current = true; go(); } }, [autoPlay, go]);
  return <>
    <button className="act" onClick={go} title={state === "playing" ? "Stop" : "Read aloud (ElevenLabs)"}><Speaker />{state === "loading" ? "Loading…" : state === "playing" ? "Stop" : "Listen"}</button>
    {err && <span className="err" title={err}>{err.length > 60 ? err.slice(0, 60) + "…" : err}</span>}
  </>;
}

export function MicButton({ onText, className }: { onText: (t: string) => void; className?: string }) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [err, setErr] = useState("");
  const rec = useRef<MediaRecorder | null>(null);
  async function toggle() {
    setErr("");
    if (state === "recording") { rec.current?.stop(); return; }
    if (state !== "idle") return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("transcribing");
        try {
          const fd = new FormData();
          fd.append("file", new Blob(chunks, { type: mr.mimeType || "audio/webm" }), "speech.webm");
          const res = await fetch(API_BASE + "/api/voice/stt", { method: "POST", body: fd });
          const b = await res.json().catch(() => null);
          if (!res.ok) throw new Error(b?.detail ?? `HTTP ${res.status}`);
          if (b.text) onText(b.text);
        } catch (e) { setErr((e as Error).message); }
        setState("idle");
      };
      rec.current = mr; mr.start(); setState("recording");
    } catch (e) { setErr("Microphone unavailable: " + (e as Error).message); }
  }
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button className={className ?? "chip"} onClick={toggle} title={state === "recording" ? "Stop and transcribe" : "Dictate (ElevenLabs)"}
        style={state === "recording" ? { color: "var(--bad)", borderColor: "var(--bad)" } : undefined}>
        <Mic />{state === "recording" ? "Stop" : state === "transcribing" ? "…" : ""}</button>
      {err && <span className="small err" title={err}>{err.slice(0, 50)}</span>}
    </span>
  );
}

type V = { voice_id: string; name: string; category: string; preview_url: string | null };
type M = { model_id: string; name: string; description: string };

export function VoiceCard() {
  const [c, setC] = useState<VoiceCfg | null>(null);
  const [key, setKey] = useState("");
  const [voices, setVoices] = useState<V[]>([]);
  const [models, setModels] = useState<M[]>([]);
  const [sub, setSub] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const loadRemote = useCallback(async () => {
    const [v, m, s] = await Promise.allSettled([api<V[]>("/api/voice/voices"), api<M[]>("/api/voice/models"), api("/api/voice/subscription")]);
    setVoices(v.status === "fulfilled" ? v.value : []); setModels(m.status === "fulfilled" ? m.value : []); setSub(s.status === "fulfilled" ? s.value : null);
    if (v.status === "rejected") setMsg((v.reason as Error).message);
  }, []);
  const load = useCallback(async () => { const x = await api<VoiceCfg>("/api/voice/settings"); setC(x); if (x.configured) loadRemote(); }, [loadRemote]);
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [load]);
  const put = async (patch: any) => {
    try { setC(await api<VoiceCfg>("/api/voice/settings", { method: "PUT", body: JSON.stringify(patch) })); setMsg("Saved"); window.dispatchEvent(new Event("as:voice")); if (patch.api_key !== undefined) { setKey(""); loadRemote(); } }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  };
  if (!c) return msg ? <p className="err">{msg}</p> : null;
  const pct = c.char_cap ? Math.min(100, (c.chars_used_month / c.char_cap) * 100) : 0;
  return (
    <div className="card">
      <div className="card-head"><b>ElevenLabs</b><span className={`pill ${c.configured ? "ok" : ""}`}>{c.configured ? `connected${c.key_hint ? " " + c.key_hint : c.key_source === "env" ? " (env)" : ""}` : "not set up"}</span>
        {sub && <span className="mut small">{sub.tier} plan · {Number(sub.character_count).toLocaleString()} / {Number(sub.character_limit).toLocaleString()} credits used</span>}</div>
      <div className="card-desc">Read answers aloud and dictate messages. ElevenLabs bills characters on <b>your</b> account — Agent Studio&apos;s model caps don&apos;t cover it, so set a monthly character cap below (checked before anything is sent). For agents, switch on the ElevenLabs server in <a href="/mcp/">MCP servers</a> (it reuses this key).</div>
      <div className="grid" style={{ marginTop: 12 }}>
        <label>API key {c.key_hint && <span className="pill ok">stored {c.key_hint}</span>}
          <span style={{ display: "flex", gap: 6 }}><input type="password" autoComplete="off" value={key} placeholder={c.configured ? "(unchanged)" : "paste your xi-api-key"} onChange={(e) => setKey(e.target.value)} style={{ flex: 1 }} />
            <button disabled={!key} onClick={() => put({ api_key: key })}>Save</button></span></label>
        <label>Voice
          <span style={{ display: "flex", gap: 6 }}><select value={c.voice_id} onChange={(e) => put({ voice_id: e.target.value })} disabled={!voices.length} style={{ flex: 1 }}>
            <option value="">{voices.length ? "Choose a voice" : c.configured ? "Loading voices…" : "Add a key first"}</option>
            {voices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name} · {v.category}</option>)}</select>
            {voices.find((v) => v.voice_id === c.voice_id)?.preview_url && <button onClick={() => new Audio(voices.find((v) => v.voice_id === c.voice_id)!.preview_url!).play()}>Preview</button>}</span></label>
        <label>Speech model
          <select value={c.tts_model} onChange={(e) => put({ tts_model: e.target.value })} disabled={!models.length}>
            {!models.some((m) => m.model_id === c.tts_model) && <option value={c.tts_model}>{c.tts_model}</option>}
            {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.name}</option>)}</select></label>
        <label>Monthly character cap (0 = no cap)
          <input type="number" min={0} defaultValue={c.char_cap} onBlur={(e) => put({ char_cap: Number(e.target.value) })} /></label>
      </div>
      <div className="small mut" style={{ marginTop: 8 }}>This month: {c.chars_used_month.toLocaleString()} characters read aloud{c.char_cap ? ` of ${c.char_cap.toLocaleString()}` : ""}.</div>
      {c.char_cap > 0 && <div className="bar" style={{ marginTop: 6 }}><i className={pct > 90 ? "b" : pct > 70 ? "w" : ""} style={{ width: `${pct}%` }} /></div>}
      <div style={{ marginTop: 10 }}>
        {[["auto_read", "Read new answers aloud automatically"], ["dictation", "Show the microphone (dictation) in the chat composer"]].map(([k, l]) => (
          <div key={k} className="row" style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}><span className="grow">{l}</span>
            <label className="switch"><input type="checkbox" checked={(c as any)[k]} onChange={(e) => put({ [k]: e.target.checked })} /><i /></label></div>))}
      </div>
      {msg && <div className="small mut" style={{ marginTop: 6 }}>{msg}</div>}
      {c.configured && c.voice_id && <div style={{ marginTop: 8 }}><ListenButton text="Hello! This is how Agent Studio will read your answers." /></div>}
    </div>
  );
}
