"use client";
// The settings of live mode: where it runs (this PC, or Google Gemini Live), which model and voice, how long a pause means you finished, whether you may interrupt,
// avatar by default. Shown on the Live page and in Settings > Voice.
import { useCallback, useEffect, useState } from "react";
import { Provider, api } from "@/lib/api";
import type { GeminiStatus, LiveSettings, LiveStatus } from "@/lib/live";
import ModelSelect from "@/components/ModelSelect";
import PriceTable from "@/components/live/PriceTable";

type AgentRow = { id: string; name: string };
type Put = (patch: Partial<LiveSettings>) => void;

function GeminiSection({ s, providers, running, put }: { s: LiveSettings; providers: Provider[]; running: boolean; put: Put }) {
  const [st, setSt] = useState<GeminiStatus | null>(null);
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => api<GeminiStatus>("/api/live/gemini/status").then(setSt).catch(() => setSt(null)), []);
  useEffect(() => { refresh(); }, [refresh, s.live_gemini_provider, s.live_gemini_voice, s.live_gemini_avatar_name, s.live_gemini_location]);
  const run = async () => {
    setBusy(true); setTest(null);
    try { const r = await api<{ ms: number }>("/api/live/gemini/test", { method: "POST" }); setTest({ ok: true, text: `Google accepted the session in ${r.ms} ms.` }); }
    catch (e) { setTest({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const vertex = providers.filter((p) => p.kind === "vertex_ai");
  return (
    <div data-testid="gemini-section" className="card" style={{ marginTop: 8 }}>
      <b>Google Gemini Live</b>
      <p className="small warn" role="note">Your voice and the answers go to Google Cloud while this engine is on. Google bills your project; Agent Studio ends a session at your cost limit below.</p>
      {st && st.problems.length > 0 && <ul className="small err" data-testid="gemini-problems">{st.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      {st?.configured && <p className="small ok">Ready: project {st.project}, {st.location}.</p>}
      <label>Vertex provider<select value={s.live_gemini_provider} aria-label="Vertex provider" disabled={running} onChange={(e) => put({ live_gemini_provider: e.target.value })}>
        {!vertex.some((p) => p.id === s.live_gemini_provider) && <option value={s.live_gemini_provider}>{s.live_gemini_provider}</option>}
        {vertex.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
      <p className="small mut">The project, region and credentials (a service account or an application-default login) are set on the <a href="/settings/#providers">Providers</a> page, in the Google Vertex AI provider. Credentials are write-only there; this page never shows them.</p>
      <label>Model<input aria-label="Gemini model" defaultValue={s.live_gemini_model} disabled={running} onBlur={(e) => e.target.value !== s.live_gemini_model && put({ live_gemini_model: e.target.value })} /></label>
      <label>Voice<select value={s.live_gemini_voice} aria-label="Gemini voice" disabled={running} onChange={(e) => put({ live_gemini_voice: e.target.value })}>{(st?.voices ?? [s.live_gemini_voice]).map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
      <label>Language<select value={s.live_gemini_language} aria-label="Gemini language" disabled={running} onChange={(e) => put({ live_gemini_language: e.target.value })}>{(st?.languages ?? [s.live_gemini_language]).map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
      <label>Avatar name<input list="gemini-avatars" aria-label="Avatar name" defaultValue={s.live_gemini_avatar_name} disabled={running} placeholder="for example Ben" onBlur={(e) => e.target.value !== s.live_gemini_avatar_name && put({ live_gemini_avatar_name: e.target.value })} />
        <datalist id="gemini-avatars">{(st?.avatar_examples ?? []).map((a) => <option key={a} value={a} />)}</datalist></label>
      <p className="small mut">Google documents prebuilt avatars but publishes no list of names in the pages Agent Studio could read (its example is “Ben”), so type the name you want. Custom avatars need Google&apos;s approval and are not offered here.</p>
      <label>Longest session {s.live_gemini_max_minutes} min<input type="range" min={1} max={60} step={1} value={s.live_gemini_max_minutes} aria-label="Longest session" disabled={running} onChange={(e) => put({ live_gemini_max_minutes: Number(e.target.value) })} /></label>
      <label>Cost limit per session $<input type="number" min={0.05} max={100} step={0.05} aria-label="Cost limit" defaultValue={s.live_gemini_cost_cap_usd} disabled={running} onBlur={(e) => Number(e.target.value) !== s.live_gemini_cost_cap_usd && put({ live_gemini_cost_cap_usd: Number(e.target.value) })} /></label>
      <details className="small"><summary>Advanced</summary>
        <label>Region (empty = the provider&apos;s)<input aria-label="Gemini region" defaultValue={s.live_gemini_location} disabled={running} onBlur={(e) => e.target.value !== s.live_gemini_location && put({ live_gemini_location: e.target.value })} /></label>
        <label>Endpoint override (ws:// or wss://, for a proxy or a test server)<input aria-label="Gemini endpoint" defaultValue={s.live_gemini_endpoint} disabled={running} onBlur={(e) => e.target.value !== s.live_gemini_endpoint && put({ live_gemini_endpoint: e.target.value })} /></label>
        <label>Avatar video bitrate (bps, 0 = Google&apos;s default)<input type="number" min={0} aria-label="Avatar video bitrate" defaultValue={s.live_gemini_video_bitrate} disabled={running} onBlur={(e) => Number(e.target.value) !== s.live_gemini_video_bitrate && put({ live_gemini_video_bitrate: Number(e.target.value) })} /></label>
        <label>Avatar audio bitrate (bps, 0 = Google&apos;s default)<input type="number" min={0} aria-label="Avatar audio bitrate" defaultValue={s.live_gemini_audio_bitrate} disabled={running} onBlur={(e) => Number(e.target.value) !== s.live_gemini_audio_bitrate && put({ live_gemini_audio_bitrate: Number(e.target.value) })} /></label>
      </details>
      <PriceTable />
      {st && <ul className="small mut">{st.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      <div className="row"><button onClick={run} disabled={busy || running || !st?.configured}>{busy ? "Testing…" : "Test the connection"}</button></div>
      {test && <div className={`small ${test.ok ? "ok" : "err"}`} role={test.ok ? "status" : "alert"} data-testid="gemini-test">{test.text}</div>}
    </div>
  );
}

export function LiveSettingsForm({ s, voices, providers, agents, running, put }: {
  s: LiveSettings; voices: string[]; providers: Provider[]; agents: AgentRow[]; running: boolean; put: Put;
}) {
  const gemini = s.live_engine === "gemini";
  return (
    <div data-testid="live-settings-form">
      <div role="group" aria-label="Where it runs" className="seg" style={{ marginBottom: 8 }}>
        <button className={!gemini ? "on" : ""} aria-pressed={!gemini} disabled={running} onClick={() => put({ live_engine: "local" })}>On this PC</button>
        <button className={gemini ? "on" : ""} aria-pressed={gemini} disabled={running} onClick={() => put({ live_engine: "gemini" })}>Google Gemini Live</button>
      </div>
      {gemini && <GeminiSection s={s} providers={providers} running={running} put={put} />}
      {!gemini && <>
        <label>Provider<select value={s.live_provider} aria-label="Provider" disabled={running} onChange={(e) => put({ live_provider: e.target.value, live_model: "" })}>
          {!providers.some((p) => p.id === s.live_provider) && <option value={s.live_provider}>{s.live_provider}</option>}
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}{p.catalog?.needs_key ? "" : " (no key needed)"}</option>)}</select></label>
        <label>Model<ModelSelect provider={s.live_provider} value={s.live_model} onChange={(v) => put({ live_model: v })} emptyLabel="Provider default" /></label>
        <p className="small mut">Pick a fast, non-thinking model (for example qwen3:8b through Ollama). Cloud models are billed to that provider and count against its cap.</p>
        <label>Voice<select value={s.live_voice} aria-label="Voice" disabled={running} onChange={(e) => put({ live_voice: e.target.value })}>{voices.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
        <label>Speed {s.live_speed.toFixed(1)}×<input type="range" min={0.6} max={1.6} step={0.1} value={s.live_speed} aria-label="Speed" disabled={running} onChange={(e) => put({ live_speed: Number(e.target.value) })} /></label>
      </>}
      <label>Agent {gemini ? "(Gemini can call it as a tool)" : "instructions"}<select value={s.live_agent} aria-label="Agent" disabled={running} onChange={(e) => put({ live_agent: e.target.value })}>
        <option value="">None: a plain voice assistant</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label>Wait before answering {s.live_hangover_ms} ms<input type="range" min={150} max={1000} step={50} value={s.live_hangover_ms} aria-label="Wait before answering" disabled={running} onChange={(e) => put({ live_hangover_ms: Number(e.target.value) })} /></label>
      <label className="row small"><input type="checkbox" checked={s.live_barge_in} disabled={running} onChange={(e) => put({ live_barge_in: e.target.checked })} aria-label="Let me interrupt" /> Let me interrupt by talking</label>
      <label className="row small"><input type="checkbox" checked={s.live_avatar} disabled={running} onChange={(e) => put({ live_avatar: e.target.checked })} aria-label="Start with the avatar" /> Start with the avatar instead of voice only</label>
    </div>
  );
}

/** Loads its own data: used on the Settings page. */
export default function LiveSettingsCard() {
  const [st, setSt] = useState<LiveStatus | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<LiveStatus>("/api/live/status").then(setSt).catch((e) => setErr((e as Error).message)), []);
  useEffect(() => {
    load();
    api<Provider[]>("/api/providers").then((p) => setProviders(p.filter((x) => x.enabled || x.kind === "vertex_ai"))).catch(() => undefined);
    api<AgentRow[]>("/api/agents").then(setAgents).catch(() => undefined);
  }, [load]);
  const put = async (patch: Partial<LiveSettings>) => {
    try { const s = await api<LiveSettings>("/api/live/settings", { method: "PUT", body: JSON.stringify(patch) }); setSt((x) => (x ? { ...x, settings: s } : x)); }
    catch (e) { setErr((e as Error).message); }
  };
  if (!st) return err ? <p className="err" role="alert">{err}</p> : null;
  return (
    <div className="card" data-testid="live-settings-card">
      <div className="card-head"><b>Live mode</b><span className={`pill ${st.local.ready ? "ok" : ""}`}>{st.local.ready ? "local voice installed" : "not installed"}</span></div>
      <div className="card-desc">Talk to a model by voice, voice only or with an animated avatar: on this PC, or through Google Gemini Live. Open the <a href="/live/">Live page</a> to install the local voice and start.</div>
      {err && <div className="err small" role="alert">{err}</div>}
      <LiveSettingsForm s={st.settings} voices={st.local.voices} providers={providers} agents={agents} running={false} put={put} />
    </div>
  );
}
