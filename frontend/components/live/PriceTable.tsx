"use client";
// The Gemini Live price table: editable, dated, and refreshable from Google's pricing page. Prices change; the label always says when they were read.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PriceTable as Prices } from "@/lib/live";

const IN = ["TEXT", "AUDIO", "VIDEO", "IMAGE"];
const OUT = ["TEXT", "AUDIO", "VIDEO"];
const RATES: [keyof Prices["rates"], string][] = [["audio_in_tokens_per_s", "Your voice, tokens per second (assumed)"], ["audio_out_tokens_per_s", "Its voice, tokens per second (measured)"], ["video_out_tokens_per_s", "Avatar video, tokens per second (measured)"]];

export default function PriceTable() {
  const [p, setP] = useState<Prices | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Prices>("/api/live/prices").then(setP).catch((e) => setMsg({ ok: false, text: (e as Error).message })); }, []);
  if (!p) return msg ? <p className="err small" role="alert">{msg.text}</p> : null;
  const num = (v: string) => (v === "" || Number.isNaN(Number(v)) ? 0 : Number(v));
  const setPrice = (dir: "in" | "out", mod: string, v: string) => setP({ ...p, [dir]: { ...p[dir], [mod]: num(v) } });
  const save = async () => {
    setBusy(true); setMsg(null);
    try { setP(await api<Prices>("/api/live/prices", { method: "PUT", body: JSON.stringify({ in: p.in, out: p.out, rates: p.rates, as_of: new Date().toISOString().slice(0, 10), source: "edited by hand" }) })); setMsg({ ok: true, text: "Saved." }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const refresh = async () => {
    setBusy(true); setMsg(null);
    try { setP(await api<Prices>("/api/live/prices/refresh", { method: "POST" })); setMsg({ ok: true, text: "Read from Google's pricing page." }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  return (
    <details className="small" data-testid="price-table" style={{ margin: "8px 0" }}>
      <summary><b>Prices</b> <span className="mut" data-testid="prices-label">prices as of {p.as_of}, verify</span></summary>
      <p className="mut">Dollars per million tokens for {p.model} ({p.region_class}). Source: <a href={p.source.startsWith("http") ? p.source : undefined} target="_blank" rel="noreferrer">{p.source}</a>. Google changes prices: check them there.</p>
      <table style={{ width: "100%" }}><thead><tr><th /><th>Text</th><th>Audio</th><th>Video</th><th>Image</th></tr></thead><tbody>
        <tr><td>In</td>{IN.map((m) => <td key={m}><input type="number" min={0} step={0.05} aria-label={`Price in ${m}`} value={p.in[m] ?? 0} onChange={(e) => setPrice("in", m, e.target.value)} style={{ width: 70 }} /></td>)}</tr>
        <tr><td>Out</td>{OUT.map((m) => <td key={m}><input type="number" min={0} step={0.05} aria-label={`Price out ${m}`} value={p.out[m] ?? 0} onChange={(e) => setPrice("out", m, e.target.value)} style={{ width: 70 }} /></td>)}<td /></tr>
      </tbody></table>
      <p className="mut">Between Google&apos;s usage reports the meter estimates from these rates. {p.rates_note}.</p>
      {RATES.map(([k, label]) => <label key={k}>{label}<input type="number" min={0} aria-label={label} value={p.rates[k]} onChange={(e) => setP({ ...p, rates: { ...p.rates, [k]: num(e.target.value) } })} /></label>)}
      <div className="row"><button onClick={save} disabled={busy}>Save prices</button><button onClick={refresh} disabled={busy}>Read Google&apos;s pricing page now</button></div>
      {msg && <div className={msg.ok ? "ok" : "err"} role={msg.ok ? "status" : "alert"} data-testid="prices-msg">{msg.text}</div>}
    </details>
  );
}
