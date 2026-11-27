"use client";
// Live mode: talk to a model by voice, voice only or with the animated avatar. Everything runs on this PC by default (Whisper + Kokoro + an Ollama model).
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, Provider, api } from "@/lib/api";
import { AvatarVideoPlayer, realPlayerDeps } from "@/lib/avatarVideo";
import { LiveClient, LiveProfile, LiveSettings, LiveState, LiveStatus, Metrics, Usage, realDeps } from "@/lib/live";
import { LiveSettingsForm } from "@/components/LiveSettings";
import Avatar, { Orb, STATE_TEXT } from "./Avatar";
import LatencyCard from "./LatencyCard";
import LiveHistory from "./LiveHistory";
import LiveInstall from "./LiveInstall";
import UsageMeter from "./UsageMeter";

type Line = { role: "user" | "assistant"; text: string; live?: boolean };

export default function LivePanel() {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agents, setAgents] = useState<{ id: string; name: string; live?: Partial<LiveProfile> }[]>([]);
  const [err, setErr] = useState("");
  const [state, setState] = useState<LiveState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [turns, setTurns] = useState<Metrics[]>([]);
  const [avatar, setAvatar] = useState(false);
  const [muted, setMuted] = useState(false);
  const [typed, setTyped] = useState("");
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [notice, setNotice] = useState("");
  const [video, setVideo] = useState(false);                       // avatar video from Gemini Live is arriving
  const client = useRef<LiveClient | null>(null);
  const videoEl = useRef<HTMLVideoElement>(null);
  const player = useRef<AvatarVideoPlayer | null>(null);
  const seenVideo = useRef<number | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [budget, setBudget] = useState<{ level: string; message: string } | null>(null);
  const [histKey, setHistKey] = useState(0);

  const load = useCallback(async () => {
    try {
      const s = await api<LiveStatus>("/api/live/status");
      setStatus(s); setAvatar((a) => (a === false && s.settings.live_avatar ? true : a));
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { load(); api<Provider[]>("/api/providers").then((p) => setProviders(p.filter((x) => x.enabled))).catch(() => undefined); api<{ id: string; name: string; live?: Partial<LiveProfile> }[]>("/api/agents").then(setAgents).catch(() => undefined); }, [load]);
  useEffect(() => () => client.current?.stop(), []);

  const put = async (patch: Partial<LiveSettings>) => {
    try { const s = await api<LiveSettings>("/api/live/settings", { method: "PUT", body: JSON.stringify(patch) }); setStatus((st) => (st ? { ...st, settings: s } : st)); }
    catch (e) { setErr((e as Error).message); }
  };

  const profile = agents.find((a) => a.id === status?.settings.live_agent)?.live;       // the chosen agent's Live profile, if it has one
  const profOn = !!profile && !!profile.enabled;
  const profOff = !!profile && Object.keys(profile).length > 0 && !profile.enabled;
  const start = async () => {
    if (!status) return;
    const s = status.settings;
    setErr(""); setNotice(""); setVideo(false); seenVideo.current = null; setLines([]); setUsage(null); setBudget(null);
    const gem = profOn ? profile!.engine === "gemini" : s.live_engine === "gemini";
    const wantAvatar = profOn ? profile!.mode === "avatar" : avatar;
    if (gem && wantAvatar && videoEl.current) player.current = new AvatarVideoPlayer(videoEl.current, realPlayerDeps(), (ms) => { seenVideo.current = ms; });
    const c = new LiveClient({
      onState: (st) => { setState(st); if (st === "thinking" || st === "speaking") setNotice(""); if (st === "thinking") { player.current?.markWaiting(); if (videoEl.current) videoEl.current.muted = false; } },   // a new answer: its sound (inside the video) is audible again
      onUser: (text) => setLines((l) => [...l.map((x) => ({ ...x, live: false })), { role: "user", text }]),
      onAssistant: (delta, final) => setLines((l) => {
        const last = l[l.length - 1];
        if (final != null) return last?.role === "assistant" && last.live ? [...l.slice(0, -1), { role: "assistant", text: final }] : [...l, { role: "assistant", text: final }];
        return last?.role === "assistant" && last.live ? [...l.slice(0, -1), { ...last, text: last.text + delta }] : [...l, { role: "assistant", text: delta, live: true }];
      }),
      onMetrics: (m) => { player.current?.finish(); const seen = seenVideo.current; seenVideo.current = null; setTurns((t) => [...t.slice(-19), { ...m, ...(seen != null ? { seen_video_ms: seen } : {}) }]); },
      onVideo: (mime, data) => { setVideo(true); player.current?.push(mime, data); },
      onNotice: setNotice,
      onAudioStart: () => { if (videoEl.current) videoEl.current.muted = true; },       // the voice arrives as audio: do not play the video's own sound as well
      onError: (m) => { setErr(m); },
      onUsage: setUsage,
      onBudget: setBudget,
      onClear: () => { player.current?.interrupted(); setLines((l) => l.map((x) => ({ ...x, live: false }))); },
      onReady: () => setAnalyser(c.analyser),
    }, realDeps(API_BASE));
    client.current = c;
    if (videoEl.current) videoEl.current.muted = false;
    await c.start({ engine: gem ? "gemini" : "local", avatar: gem && wantAvatar, provider: s.live_provider, model: s.live_model, agent: s.live_agent, voice: s.live_voice, speed: s.live_speed, hangover_ms: s.live_hangover_ms, barge_in: s.live_barge_in });
  };
  const stop = () => { client.current?.stop(); client.current = null; player.current?.reset(); player.current = null; setAnalyser(null); setMuted(false); setVideo(false); setHistKey((k) => k + 1); };
  const running = state !== "idle";
  const last = turns[turns.length - 1] ?? null;

  if (!status) return err ? <p className="err" role="alert">{err}</p> : <p className="mut">Loading…</p>;
  const s = status.settings;
  const gemini = profOn ? profile!.engine === "gemini" : s.live_engine === "gemini";
  const avatarMode = profOn ? profile!.mode === "avatar" : avatar;
  const ready = (gemini || status.local.ready) && !profOff;
  const agentName = agents.find((a) => a.id === s.live_agent)?.name ?? "";
  return (
    <div data-testid="live-panel">
      <div className="page-head"><h2>Live</h2></div>
      <p className="page-sub">Talk to a model by voice, on this PC.</p>
      {!gemini && !status.local.ready && <LiveInstall local={status.local} onChange={load} />}
      {profOn && <p className="small mut" data-testid="live-profile-note">Using {agentName}&apos;s Live profile: {gemini ? "Google Gemini Live" : "this PC"}, {avatarMode ? "with avatar" : "voice only"}{profile!.budget_usd ? `, budget $${profile!.budget_usd.toFixed(2)}` : ""}.</p>}
      {profOff && <p className="err small" role="alert" data-testid="live-profile-off">Live mode is switched off for {agentName}. Switch it on in the agent&apos;s Live section, or choose another agent.</p>}
      {budget && <p className={`small ${budget.level === "stop" ? "err" : "warn"}`} role={budget.level === "stop" ? "alert" : "status"} data-testid="live-budget-banner">{budget.message}</p>}
      {gemini && <p className="small warn" role="note" data-testid="live-cloud-banner">Google Gemini Live is on: your voice and the answers go to Google Cloud. Change this under Settings.</p>}
      {err && <div className="err small" role="alert" style={{ margin: "8px 0" }}>{err}</div>}
      <div className="live-grid">
        <div className="card live-stage">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div role="group" aria-label="Mode" className="seg">
              <button className={!avatarMode ? "on" : ""} aria-pressed={!avatarMode} disabled={profOn} title={profOn ? "Set by the agent's Live profile" : undefined} onClick={() => { setAvatar(false); put({ live_avatar: false }); }}>Voice only</button>
              <button className={avatarMode ? "on" : ""} aria-pressed={avatarMode} disabled={profOn} title={profOn ? "Set by the agent's Live profile" : undefined} onClick={() => { setAvatar(true); put({ live_avatar: true }); }}>With avatar</button>
            </div>
            <span className={`pill ${state === "speaking" || state === "hearing" ? "ok" : ""}`} role="status" data-testid="live-state">{STATE_TEXT[state]}</span>
          </div>
          <div className="live-face">
            {gemini && avatarMode && <video ref={videoEl} className="live-video" playsInline autoPlay aria-label="Avatar video" data-testid="live-video" style={{ display: video ? "block" : "none" }} />}
            {gemini && avatarMode && !video && (running ? <p className="small mut" data-testid="live-video-wait">Waiting for the avatar video from Google…</p> : <Avatar analyser={null} state={state} />)}
            {!(gemini && avatarMode) && (avatarMode ? <Avatar analyser={analyser} state={state} /> : <Orb analyser={analyser} state={state} />)}
          </div>
          {notice && <p className="small warn" role="status" data-testid="live-notice">{notice}</p>}
          <div className="row" style={{ justifyContent: "center", flexWrap: "wrap" }}>
            {!running ? <button className="acc" disabled={!ready} onClick={start} data-testid="live-start">Start talking</button>
              : <><button onClick={stop} data-testid="live-stop">End</button>
                <button onClick={() => { const m = !muted; client.current?.setMuted(m); setMuted(m); }} aria-pressed={muted}>{muted ? "Unmute" : "Mute"}</button>
                <button onClick={() => { client.current?.interrupt(); if (videoEl.current) videoEl.current.muted = true; }} disabled={state !== "speaking" && state !== "thinking"}>Interrupt</button></>}
          </div>
          {state === "loading" && <p className="small mut" style={{ textAlign: "center" }}>Loading the model and the voice (the first time can take a few seconds)…</p>}
          <p className="small mut" style={{ textAlign: "center" }}>Headphones work best: without them the assistant can hear itself and stop talking.</p>
          {running && <form className="row" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { client.current?.say(typed.trim()); setTyped(""); } }}>
            <input className="grow" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="…or type a message" aria-label="Type a message" /><button disabled={!typed.trim()}>Send</button></form>}
          <div className="live-log" aria-live="polite" data-testid="live-log">
            {lines.length === 0 && <p className="small mut">What you and the assistant say appears here.</p>}
            {lines.map((l, i) => <div key={i} className={`live-line ${l.role}`}><span className="small mut">{l.role === "user" ? "You" : "Assistant"}</span><div>{l.text}</div></div>)}
          </div>
        </div>
        <div>
          <UsageMeter u={usage} />
          <LatencyCard last={last} turns={turns} ready={ready} selfTest={!gemini} />
          <div className="card" data-testid="live-settings">
            <div className="card-head"><b>Settings</b>{status.engine.state === "ready" && <span className="pill ok" title={`speech: ${status.engine.stt_model} on ${status.engine.device}; voice on ${status.engine.tts_device}`}>engines warm</span>}</div>
            <LiveSettingsForm s={s} voices={status.local.voices} providers={providers} agents={agents} running={running} put={put} />
            {status.local.ready && <p className="small mut">Speech: Whisper {status.local.stt_model} · {status.local.device === "cuda" ? "GPU" : "CPU"}. {profOn && profile!.agent_turns ? "Each answer is a full turn of the agent, with its tools." : "On this PC the model only talks (no tools) unless the agent's Live profile uses its tools."}</p>}
          </div>
        </div>
      </div>
      <LiveHistory refreshKey={histKey} />
    </div>
  );
}
