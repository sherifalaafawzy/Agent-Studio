"use client";
// The latency readout: what you waited for, stage by stage, for the last answer; plus a self-test that needs no microphone.
import { useState } from "react";
import { api } from "@/lib/api";
import type { Metrics, SelfTest } from "@/lib/live";

const STAGES: [keyof Metrics, string, string][] = [
  ["endpoint_ms", "Wait to be sure you stopped", "The pause that means you finished talking. Shorter = quicker answers, but you may be cut off (Settings below)."],
  ["stt_ms", "Understanding you", "Speech recognition (Whisper) on this PC."],
  ["llm_first_token_ms", "Model's first word", "Time until the model produced its first word."],
  ["tts_first_ms", "Voice start-up", "Time for the voice to make the first piece of audio."],
  ["first_video_ms", "Avatar's first picture (server)", "Gemini Live: time until the first piece of avatar video arrived."],
  ["seen_video_ms", "Avatar's first picture (seen)", "Measured in this browser: until the first video frame was drawn."],
];
const sec = (n?: number) => (n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`);

export function Breakdown({ m, title }: { m: Partial<Metrics>; title: string }) {
  const total = m.user_perceived_ms ?? 0;
  return (
    <div data-testid="live-breakdown">
      <div className="row" style={{ alignItems: "baseline" }}><b>{title}</b><span className="grow" />
        <span className="small mut">first sound after you stop</span><b style={{ fontSize: 22 }} data-testid="live-ttfa">{sec(m.heard_ms ?? m.user_perceived_ms)}</b></div>
      {m.heard_ms == null && m.user_perceived_ms != null && <div className="small mut">Measured on the server; add a few tens of milliseconds for your speakers.</div>}
      <table className="small" style={{ width: "100%", marginTop: 6 }}><tbody>
        {STAGES.filter(([k]) => m[k] != null).map(([k, label, tip]) => (
          <tr key={k} title={tip}><td>{label}</td><td style={{ textAlign: "right" }}>{sec(m[k] as number)}</td>
            <td style={{ width: "40%" }}><div className="bar"><i style={{ width: `${total ? Math.min(100, ((m[k] as number) / total) * 100) : 0}%` }} /></div></td></tr>))}
      </tbody></table>
      {m.cloud && <div className="small mut" data-testid="live-cloud-note">Google Gemini Live{m.cost_usd != null ? ` · estimated cost so far $${m.cost_usd.toFixed(4)}` : ""}</div>}
      {m.interrupted && <div className="small mut">This answer was interrupted.</div>}
    </div>
  );
}

export default function LatencyCard({ last, turns, ready, selfTest = true }: { last: Metrics | null; turns: Metrics[]; ready: boolean; selfTest?: boolean }) {
  const [test, setTest] = useState<SelfTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async () => {
    setBusy(true); setErr("");
    try { setTest(await api<SelfTest>("/api/live/selftest", { method: "POST", body: JSON.stringify({ runs: 3 }) })); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const heard = turns.map((t) => t.heard_ms ?? t.user_perceived_ms).filter((n): n is number => typeof n === "number").sort((a, b) => a - b);
  const median = heard.length ? heard[Math.floor(heard.length / 2)] : null;
  return (
    <div className="card" data-testid="live-latency">
      <div className="card-head"><b>Speed</b>{median != null && <span className="pill">median {sec(median)} over {heard.length}</span>}</div>
      {last ? <Breakdown m={last} title={`Answer ${last.turn}`} /> : <p className="small mut">Talk to see how long each step takes. First sound is counted from the moment you stop talking.</p>}
      {selfTest && <>
      <hr />
      <div className="row"><button onClick={run} disabled={busy || !ready}>{busy ? "Testing… (the first run loads the models)" : "Run a speed test (no microphone needed)"}</button></div>
      <p className="small mut">Speaks a sentence, plays it back as the microphone and times every step of the answer, three times.</p>
      {err && <div className="err small" role="alert">{err}</div>}
      {test && <div data-testid="live-selftest">
        <Breakdown m={test.median} title="Typical (after warm-up)" />
        <div className="small mut" style={{ marginTop: 6 }}>First answer: {sec(test.first.user_perceived_ms)} · heard: “{test.turns[0]?.heard}” · {test.local_model ? "model on this PC" : "cloud model"}</div>
        <div className="small mut">{test.note}</div></div>}
      </>}
    </div>
  );
}
