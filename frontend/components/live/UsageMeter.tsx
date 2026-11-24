"use client";
// The live cost meter: what this session has used and costs so far, updating about once a second. It never presents an estimate as exact: every figure says whether
// it was REPORTED by Google, ESTIMATED from token rates, or a mix; a local session shows its usage with no cloud cost.
import type { Usage } from "@/lib/live";

const KIND_TEXT: Record<string, string> = { reported: "reported by Google", estimated: "estimated", mixed: "reported + estimated since the last report", local: "this PC (no cloud cost)", none: "nothing used yet" };
const usd = (n: number) => (n === 0 ? "$0.00" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`);
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function UsageMeter({ u }: { u: Usage | null }) {
  if (!u) return <div className="card" data-testid="usage-meter"><div className="card-head"><b>Cost and usage</b></div><p className="small mut">Appears while a session is running, once a second.</p></div>;
  const approx = u.cloud && u.source !== "reported" ? "≈ " : u.cloud ? "" : "";
  return (
    <div className="card" data-testid="usage-meter" aria-live="off">
      <div className="card-head"><b>Cost and usage</b><span className={`pill ${u.level === "stop" ? "bad" : u.level === "warn" ? "warn" : ""}`} data-testid="usage-level">{u.level === "stop" ? "budget reached" : u.level === "warn" ? "80% of budget" : mmss(u.elapsed_s)}</span></div>
      <div className="row" style={{ alignItems: "baseline" }}>
        <b style={{ fontSize: 26 }} data-testid="usage-cost">{u.cloud ? `${approx}${usd(u.cost_usd)}` : "$0.00"}</b>
        <span className="small mut">{u.cloud ? "so far" : "cloud cost"}</span><span className="grow" />
        <span className="small" data-testid="usage-permin">{u.per_min_usd != null ? `${approx}${usd(u.per_min_usd)} per minute` : "per minute: soon"}</span>
      </div>
      <div className="small mut" data-testid="usage-source">{KIND_TEXT[u.source] ?? u.source}{u.cloud && u.prices_as_of ? ` · prices as of ${u.prices_as_of}, verify` : ""}</div>
      {u.budget_usd != null && <div style={{ margin: "8px 0" }} data-testid="usage-budget">
        <div className="bar"><i className={u.level === "stop" ? "b" : u.level === "warn" ? "w" : ""} style={{ width: `${Math.min(100, u.budget_pct)}%` }} /></div>
        <div className="small mut">{u.budget_pct.toFixed(0)}% of the ${u.budget_usd.toFixed(2)} budget (warning at 80%, stop at 100%)</div></div>}
      <table className="small" style={{ width: "100%" }} aria-label="Usage by stage"><tbody>
        <tr><td>Your voice in</td><td style={{ textAlign: "right" }} data-testid="usage-audio-in">{u.seconds.audio_in.toFixed(1)} s</td></tr>
        <tr><td>Its voice out</td><td style={{ textAlign: "right" }} data-testid="usage-audio-out">{u.seconds.audio_out.toFixed(1)} s</td></tr>
        {u.cloud && u.seconds.video > 0 && <tr><td>Avatar video streamed</td><td style={{ textAlign: "right" }} data-testid="usage-video">{u.seconds.video.toFixed(1)} s</td></tr>}
        <tr><td>Tokens in / out</td><td style={{ textAlign: "right" }} data-testid="usage-tokens">{u.tokens.in.toLocaleString()} / {u.tokens.out.toLocaleString()}</td></tr>
      </tbody></table>
      {u.rows.length > 0 && <details className="small" style={{ marginTop: 6 }}><summary>Breakdown by stage</summary>
        <table style={{ width: "100%" }} data-testid="usage-rows"><tbody>{u.rows.map((r) => (
          <tr key={r.label} title={KIND_TEXT[r.kind]}><td>{r.label}</td><td style={{ textAlign: "right" }}>{r.tokens != null ? `${r.tokens.toLocaleString()} tok` : r.seconds != null ? `${r.seconds.toFixed(1)} s` : ""}</td>
            <td style={{ textAlign: "right" }}>{r.kind === "estimated" || r.kind === "mixed" ? "≈ " : ""}{usd(r.usd)}</td><td className="mut">{r.kind}</td></tr>))}</tbody></table></details>}
    </div>
  );
}
