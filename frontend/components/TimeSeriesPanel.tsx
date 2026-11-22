"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { ITimeline, ITrash } from "@/components/Icons";

type Step = { id: string; label: string; size_mb: number; status: "done" | "running" | "failed" | "pending"; model: string | null };
export type TsModel = {
  id: string; label: string; repo: string; params: string; kind: "series" | "ohlcv"; state: "ready" | "not_installed" | "installing" | "queued" | "failed" | "blocked";
  error: string | null; download_bytes: number; max_context: number | null; max_horizon: number | null; quantile_levels: number[]; multivariate: boolean; covariates: boolean;
  license: { name: string; url: string; summary: string[]; commercial: "yes" | "restricted" | "no" }; good_for: string; caveats: string; notes: string[]; skipped: string;
  blocked: string; available_from: string; revision: string; source: { repo: string; sha: string } | null; versions: Record<string, any> | null;
};
type Install = { state: "idle" | "installing" | "failed"; step: string | null; steps: Step[]; percent: number; progress_text: string; log_tail: string[]; error: string | null; queue: string[] };
type TsStatus = {
  env: { ready: boolean; home: string; torch: string | null; python: string | null; free_gb: number | null };
  hardware: { nvidia: boolean; gpu: string | null; cuda: number[] | null; driver: string | null; torch_build: string; variant: string }; expect: string; install: Install; models: TsModel[];
};
type Plan = { steps: Step[]; download_mb: number; disk_gb_needed: number; free_gb: number | null; torch_build: string; variant: string; env_needed: boolean; expect: string };
type Fc = { label: string; median: number[]; quantiles: Record<string, number[]>; device: string; latency_ms: number; loaded_now: boolean; note?: string; approximate_levels?: string[] };
type Backtest = { ok: boolean; error?: string; holdout?: number; scores?: Record<string, { mae: number; rmse: number; smape_pct: number; mase: number | null }>; ranking?: string[]; ranked_by?: string; caution?: string; errors?: Record<string, string> };
export type Result = { ok: boolean; error?: string; horizon?: number; n_context?: number; quantile_levels?: number[]; models?: Record<string, Fc>;
  ensemble?: { median: number[]; quantiles: Record<string, number[]>; members: string[] } | null; not_installed?: string[]; errors?: Record<string, string>; backtest?: Backtest };

const POLL_MS = 2000;
const mb = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)} GB` : `${Math.round(n)} MB`);
const COLORS = ["#4f7fd9", "#e0794f", "#2aa58f", "#d9a43a", "#8a66d2", "#d0574f", "#3d9fcc", "#5aa452"];
const LICENCE_TONE = { yes: "ok", restricted: "warn", no: "bad" } as const;
const LICENCE_TEXT = { yes: "commercial use OK", restricted: "commercial use limited", no: "non-commercial only" } as const;
const SAMPLE = Array.from({ length: 72 }, (_, i) => Math.round((100 + i * 0.6 + 12 * Math.sin((i / 24) * 2 * Math.PI)) * 10) / 10).join(", ");

/** Numbers separated by commas, spaces, semicolons or new lines, or a JSON array. */
export function parseSeries(text: string): { values?: number[]; error?: string } {
  const t = text.trim();
  if (!t) return { error: "Paste the history: numbers separated by commas, spaces or new lines." };
  let parts: unknown[];
  if (t.startsWith("[")) {
    try { parts = JSON.parse(t) as unknown[]; } catch { return { error: "That looks like a JSON list but it does not parse." }; }
  } else parts = t.split(/[\s,;]+/).filter(Boolean);
  const values = parts.map((p) => (typeof p === "number" ? p : typeof p === "string" && p.trim() ? Number(p) : NaN));   // null / "" are missing values, not zeros
  const bad = values.findIndex((v) => !Number.isFinite(v));
  if (bad >= 0) return { error: `Value ${bad + 1} ("${String(parts[bad])}") is not a number.` };
  if (values.length < 8) return { error: `Need at least 8 values (got ${values.length}).` };
  return { values };
}

function ForecastChart({ history, result, order }: { history: number[]; result: Result; order: string[] }) {
  const W = 640, Hh = 240, PAD = 28;
  const hist = history.slice(-120);
  const h = result.horizon!;
  const ids = order.filter((id) => result.models?.[id]);
  const ens = result.ensemble;
  const levels = result.quantile_levels!, loKey = String(levels[0]), hiKey = String(levels[levels.length - 1]);
  const bandOf = (q: Record<string, number[]>) => ({ lo: q[loKey], hi: q[hiKey] });
  const band = ens ? bandOf(ens.quantiles) : ids.length === 1 ? bandOf(result.models![ids[0]].quantiles) : null;
  const all = [...hist, ...ids.flatMap((id) => result.models![id].median), ...(ens?.median ?? []), ...(band?.lo ?? []), ...(band?.hi ?? [])];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const n = hist.length + h;
  const x = (i: number) => PAD + (i / Math.max(n - 1, 1)) * (W - 2 * PAD);
  const y = (v: number) => Hh - PAD - ((v - lo) / span) * (Hh - 2 * PAD);
  const line = (vs: number[], from: number) => vs.map((v, i) => `${i ? "L" : "M"}${x(from + i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const bandPath = band
    ? `${band.hi.map((v, i) => `${i ? "L" : "M"}${x(hist.length + i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} ${[...band.lo].map((v, i) => `L${x(hist.length + i).toFixed(1)},${y(v).toFixed(1)}`).reverse().join(" ")} Z`
    : "";
  return (
    <div className="ts-chart">
      <svg viewBox={`0 0 ${W} ${Hh}`} role="img" aria-label="Forecast chart: history followed by each model's median forecast" style={{ width: "100%", height: "auto" }}>
        <line x1={x(hist.length - 1)} x2={x(hist.length - 1)} y1={PAD / 2} y2={Hh - PAD} stroke="var(--line)" strokeDasharray="3 3" />
        {band && <path d={bandPath} fill="var(--mut)" opacity={0.15} data-testid="ts-band" />}
        <path d={line(hist, 0)} fill="none" stroke="var(--mut)" strokeWidth={1.6} />
        {ids.map((id, k) => <path key={id} d={line([hist[hist.length - 1], ...result.models![id].median], hist.length - 1)} fill="none" stroke={COLORS[k % COLORS.length]} strokeWidth={2} data-testid={`ts-line-${id}`} />)}
        {ens && <path d={line([hist[hist.length - 1], ...ens.median], hist.length - 1)} fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="5 4" data-testid="ts-line-ensemble" />}
        <text x={PAD} y={Hh - 8} fontSize="10" fill="var(--mut)">history ({hist.length} shown)</text>
        <text x={W - PAD} y={Hh - 8} fontSize="10" fill="var(--mut)" textAnchor="end">next {h}</text>
      </svg>
      <div className="small ts-legend">
        <span><i style={{ background: "var(--mut)" }} />history</span>
        {ids.map((id, k) => <span key={id}><i style={{ background: COLORS[k % COLORS.length] }} />{result.models![id].label}</span>)}
        {ens && <span><i style={{ background: "currentColor" }} />ensemble (mean)</span>}
        {band && <span className="mut">shaded: {result.quantile_levels?.[0]}–{result.quantile_levels?.[result.quantile_levels.length - 1]} band of the {ens ? "ensemble" : "model"}</span>}
      </div>
    </div>
  );
}

function TryIt({ models }: { models: TsModel[] }) {
  const ready = useMemo(() => models.filter((m) => m.state === "ready" && m.kind === "series"), [models]);
  const [text, setText] = useState("");
  const [horizon, setHorizon] = useState("24");
  const [picked, setPicked] = useState<string[] | null>(null);
  const [backtest, setBacktest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [result, setResult] = useState<{ r: Result; history: number[] } | null>(null);
  const sel = picked ?? ready.map((m) => m.id);
  const toggle = (id: string) => setPicked(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  async function run() {
    const parsed = parseSeries(text);
    const h = Number(horizon);
    if (parsed.error) { setMsg(parsed.error); return; }
    if (!Number.isInteger(h) || h < 1) { setMsg("Horizon must be a whole number of steps, 1 or more."); return; }
    const chosen = sel.filter((id) => ready.some((m) => m.id === id));
    if (!chosen.length) { setMsg("Pick at least one installed model."); return; }
    setMsg(""); setBusy(true); setResult(null);
    try {
      const r = await api<Result>("/api/ts/forecast", { method: "POST", body: JSON.stringify({ series: parsed.values, horizon: h, models: chosen, quantiles: [0.1, 0.5, 0.9], backtest }) });
      if (r.ok) setResult({ r, history: parsed.values! }); else setMsg(r.error ?? "The forecast failed.");
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  }
  const r = result?.r;
  return (
    <div style={{ marginTop: 14 }} data-tour="ts-tryit">
      <b className="small">Try it</b>
      <p className="small mut" style={{ margin: "4px 0 8px" }}>Paste a series (oldest first) and pick one model, several, or all. Each run starts the models fresh in a separate process, so the first forecast takes longer than when an agent uses the MCP server.</p>
      <textarea aria-label="Series" rows={4} placeholder="e.g. 112, 118, 132, 129, 121, 135, 148, 148, 136, 119 …" value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} />
      <div className="row ts-controls" style={{ marginTop: 8, gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <button className="ghost small" onClick={() => setText(SAMPLE)}>Use sample data</button>
        <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>Horizon
          <input aria-label="Horizon" type="number" min={1} value={horizon} onChange={(e) => setHorizon(e.target.value)} style={{ width: 80 }} /></label>
        <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={backtest} onChange={(e) => setBacktest(e.target.checked)} />Also backtest (score each model on the last {horizon || "N"} points)</label>
      </div>
      <div className="row" style={{ marginTop: 8, gap: 12, flexWrap: "wrap" }}>
        {ready.map((m) => (
          <label key={m.id} className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" aria-label={`Use ${m.label}`} checked={sel.includes(m.id)} onChange={() => toggle(m.id)} />{m.label}</label>))}
        <button className="small" onClick={() => setPicked(sel.length === ready.length ? [] : ready.map((m) => m.id))}>{sel.length === ready.length ? "None" : "All"}</button>
        <span className="grow" />
        <button className="acc small" disabled={busy} onClick={run}>{busy ? "Forecasting…" : "Forecast"}</button>
      </div>
      {msg && <div className="small err" style={{ marginTop: 8 }} role="alert">{msg}</div>}
      {r && result && (
        <div style={{ marginTop: 10 }}>
          <ForecastChart history={result.history} result={r} order={ready.map((m) => m.id)} />
          <div style={{ overflowX: "auto" }}>
            <table className="small ts-table"><caption className="mut">Median forecast and {r.quantile_levels?.[0]}–{r.quantile_levels?.[r.quantile_levels.length - 1]} band, first steps</caption>
              <thead><tr><th>Model</th><th>Step 1</th><th>Step 2</th><th>Step 3</th><th>Last</th><th>Time</th></tr></thead>
              <tbody>
                {Object.entries(r.models!).map(([id, f]) => {
                  const lo = f.quantiles[String(r.quantile_levels?.[0])], hi = f.quantiles[String(r.quantile_levels?.[r.quantile_levels.length - 1])];
                  const cell = (i: number) => `${f.median[i]?.toFixed(2)} (${lo[i]?.toFixed(1)}–${hi[i]?.toFixed(1)})`;
                  return <tr key={id}><td>{f.label}</td><td>{cell(0)}</td><td>{f.median.length > 1 ? cell(1) : "–"}</td><td>{f.median.length > 2 ? cell(2) : "–"}</td>
                    <td>{cell(f.median.length - 1)}</td><td>{(f.latency_ms / 1000).toFixed(1)} s on {f.device}{f.loaded_now ? " (loaded)" : ""}</td></tr>;
                })}
                {r.ensemble && <tr><td>Ensemble (mean)</td><td>{r.ensemble.median[0].toFixed(2)}</td><td>{r.ensemble.median.length > 1 ? r.ensemble.median[1].toFixed(2) : "–"}</td>
                  <td>{r.ensemble.median.length > 2 ? r.ensemble.median[2].toFixed(2) : "–"}</td><td>{r.ensemble.median[r.ensemble.median.length - 1].toFixed(2)}</td><td>{r.ensemble.members.length} models</td></tr>}
              </tbody></table>
          </div>
          {Object.entries(r.errors ?? {}).map(([id, e]) => <div key={id} className="small err">{id}: {e}</div>)}
          {r.backtest && (r.backtest.ok && r.backtest.scores ? (
            <div style={{ marginTop: 8, overflowX: "auto" }}>
              <table className="small ts-table"><caption className="mut">Backtest on the last {r.backtest.holdout} points (lower is better, ranked by {r.backtest.ranked_by})</caption>
                <thead><tr><th>#</th><th>Model</th><th>MAE</th><th>RMSE</th><th>sMAPE %</th><th>MASE</th></tr></thead>
                <tbody>{(r.backtest.ranking ?? []).map((id, i) => { const s = r.backtest!.scores![id]; return (
                  <tr key={id}><td>{i + 1}</td><td>{r.models?.[id]?.label ?? "Ensemble (mean)"}</td><td>{s.mae}</td><td>{s.rmse}</td><td>{s.smape_pct}</td><td>{s.mase ?? "–"}</td></tr>); })}</tbody></table>
              <div className="small mut">{r.backtest.caution}</div>
            </div>) : <div className="small err" style={{ marginTop: 8 }}>Backtest: {r.backtest.error}</div>)}
        </div>)}
    </div>
  );
}

export default function TimeSeriesPanel({ onChange }: { onChange: () => void }) {
  const [s, setS] = useState<TsStatus | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [accepted, setAccepted] = useState<string[]>([]);
  const [device, setDevice] = useState("auto");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planErr, setPlanErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<TsStatus>("/api/ts/status").then(setS).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const installing = s?.install.state === "installing";
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(async () => {
      try {
        const p = await api<Install & { models: Record<string, string> }>("/api/ts/install/progress");
        if (p.state === "installing") setS((cur) => ({ ...cur!, install: { ...cur!.install, ...p } }));
        else { await load(); onChange(); toast(p.state === "failed" ? "Model install failed" : "Models installed", p.state === "failed" ? "err" : "ok"); }
      } catch { /* backend briefly unreachable: keep polling */ }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [installing, load, onChange]);
  const key = sel.join(",");
  useEffect(() => {
    if (!key) { setPlan(null); setPlanErr(""); return; }
    let live = true;
    api<Plan>(`/api/ts/plan?models=${encodeURIComponent(key)}&device=${device}`).then((p) => { if (live) { setPlan(p); setPlanErr(""); } }).catch((e) => { if (live) { setPlan(null); setPlanErr((e as Error).message); } });
    return () => { live = false; };
  }, [key, device]);
  if (!s) return null;
  const toggleSel = (id: string) => setSel((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const toggleAccept = (id: string) => setAccepted((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const allAccepted = sel.length > 0 && sel.every((id) => accepted.includes(id));
  async function install() {
    setBusy(true);
    try { setS(await api<TsStatus>("/api/ts/install", { method: "POST", body: JSON.stringify({ models: sel, accepted, device }) })); setSel([]); toast("Installing…"); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  }
  async function uninstall(m: TsModel) {
    if (!confirm(`Remove ${m.label} and its downloaded weights?`)) return;
    try { await api(`/api/ts/uninstall/${m.id}`, { method: "POST" }); toast(`${m.label} removed`); await load(); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  const installedCount = s.models.filter((m) => m.state === "ready").length;
  const stateTone = (m: TsModel) => (m.state === "ready" ? "ok" : m.state === "failed" ? "bad" : m.state === "installing" || m.state === "queued" || m.state === "blocked" ? "warn" : "");
  const hw = s.hardware;
  return (
    <div className="card" data-tour="ts-card">
      <div className="card-head"><ITimeline size={18} /><b>Time-series forecasting</b><span className="pill">zero-shot · local</span>
        <span className={`pill ${installedCount ? "ok" : ""}`}>{installedCount} of {s.models.length} installed</span><span className="grow" />
        <span className="small mut">Forecast a series with one model, several, or all at once, and see which is best on your data.</span></div>
      <p className="small" style={{ margin: "8px 0" }}>{s.expect}</p>
      <div className="small mut">{hw.nvidia ? `GPU: ${hw.gpu} (CUDA ${hw.cuda ? hw.cuda.join(".") : "?"}, driver ${hw.driver})` : "No NVIDIA GPU detected"} · build: {hw.torch_build}
        {s.env.free_gb !== null ? ` · ${s.env.free_gb} GB free` : ""}</div>

      <ul className="ts-models" style={{ listStyle: "none", margin: "10px 0 0", padding: 0 }}>
        {s.models.map((m) => {
          const can = (m.state === "not_installed" || m.state === "failed") && !installing;
          return (
            <li key={m.id} className="ts-model" data-model={m.id} data-state={m.state}>
              <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input type="checkbox" aria-label={`Select ${m.label}`} disabled={!can} checked={sel.includes(m.id)} onChange={() => toggleSel(m.id)} />
                <b>{m.label}</b><span className="small mut">{m.params} · {m.kind === "ohlcv" ? "candlesticks only" : "any series"} · {mb(m.download_bytes / 1e6)}</span>
                <span className={`pill ${stateTone(m)}`}>{m.state.replace("_", " ")}</span>
                <span className={`pill ${LICENCE_TONE[m.license.commercial]}`} title={m.license.name}>{LICENCE_TEXT[m.license.commercial]}</span><span className="grow" />
                {m.state === "ready" && <button className="ghost small" aria-label={`Uninstall ${m.label}`} onClick={() => uninstall(m)}><ITrash size={13} />Uninstall</button>}
              </div>
              <div className="small" style={{ margin: "4px 0 0 26px" }}>{m.good_for}</div>
              {m.state === "blocked" && <div className="small err" style={{ margin: "2px 0 0 26px" }}>Not installable yet: {m.error}</div>}
              {m.state === "failed" && <div className="small err" style={{ margin: "2px 0 0 26px" }}>{m.error}</div>}
              {m.state === "ready" && m.versions && <div className="small mut" style={{ margin: "2px 0 0 26px" }}>library {m.versions.library}, torch {m.versions.torch}{m.versions.cuda ? `, CUDA GPU ${m.versions.gpu}` : ", CPU only"}</div>}
              <details style={{ margin: "4px 0 0 26px" }}>
                <summary className="small">Licence, limits and notes</summary>
                <ul className="small" style={{ margin: "6px 0 0 18px" }}>{m.license.summary.map((t) => <li key={t}>{t}</li>)}</ul>
                <a className="small" href={m.license.url} target="_blank" rel="noreferrer">Full licence text ({m.license.name})</a>
                <ul className="small" style={{ margin: "6px 0 0 18px" }}>
                  <li>{m.caveats}</li>
                  <li>Reads up to {m.max_context ? `${m.max_context.toLocaleString()} steps` : "a very long"} history{m.max_horizon ? `, forecasts up to ${m.max_horizon.toLocaleString()} steps at once` : ""}; {m.quantile_levels.length ? `${m.quantile_levels.length} quantile levels` : "no quantile band"}{m.multivariate ? "; multivariate-capable" : ""}.</li>
                  {m.notes.map((t) => <li key={t}>{t}</li>)}
                  {m.skipped && <li>Not downloaded: {m.skipped}.</li>}
                  <li>Pinned: <code>{m.repo}@{m.revision.slice(0, 8)}</code>{m.source ? <> and source <code>{m.source.repo}@{m.source.sha.slice(0, 8)}</code></> : null}.</li>
                </ul>
              </details>
              {sel.includes(m.id) && (
                <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6, margin: "6px 0 0 26px" }}>
                  <input type="checkbox" aria-label={`Accept the ${m.label} licence`} checked={accepted.includes(m.id)} onChange={() => toggleAccept(m.id)} />I accept the {m.license.name} terms</label>)}
            </li>);
        })}
      </ul>

      {installing && (
        <div style={{ marginTop: 10 }}>
          <ul className="small" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {s.install.steps.map((st) => <li key={st.id} data-step={st.id} data-status={st.status}>{{ done: "✓", running: "…", failed: "✗", pending: "○" }[st.status]} {st.label}{st.size_mb ? ` · ~${mb(st.size_mb)}` : ""}</li>)}
          </ul>
          <div style={{ height: 6, borderRadius: 3, background: "var(--line)", marginTop: 8 }} role="progressbar" aria-valuenow={s.install.percent} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ height: 6, borderRadius: 3, background: "var(--acc, #4f7fd9)", width: `${s.install.percent}%` }} /></div>
          <div className="small mut" style={{ marginTop: 4 }}>{s.install.percent}% · {s.install.progress_text}. You can leave this page; it keeps running.</div>
          {s.install.log_tail.length > 0 && <pre className="small err-raw" style={{ maxHeight: 120, overflow: "auto" }}>{s.install.log_tail.join("\n")}</pre>}
        </div>)}
      {s.install.state === "failed" && <div className="small err" style={{ marginTop: 8 }}>{s.install.error}</div>}

      {!installing && sel.length > 0 && (
        <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <span className="small mut" style={{ marginRight: "auto" }}>
            {plan ? `Download about ${mb(plan.download_mb)}${plan.env_needed ? " (including the shared Python environment)" : ""}; needs about ${plan.disk_gb_needed} GB of disk. Nothing downloads until you click Install.` : planErr || "Checking the size…"}</span>
          <select aria-label="Device" value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="auto">{hw.nvidia ? "GPU (CUDA) build" : "Automatic"}</option><option value="cpu">CPU only (smaller)</option></select>
          <button className="acc small" disabled={!allAccepted || busy} onClick={install}>Install {sel.length} selected</button>
        </div>)}
      {!installing && sel.length === 0 && installedCount < s.models.length && <div className="small mut" style={{ marginTop: 8 }}>Tick the models you want, accept each licence, then install. Models share one isolated Python environment; every model is its own download.</div>}
      {installedCount > 0 && <div className="small mut" style={{ marginTop: 6 }}>Turn the &quot;Time-series forecasting&quot; server on below, then give it to agents.</div>}
      {installedCount > 0 && <TryIt models={s.models} />}
    </div>
  );
}
