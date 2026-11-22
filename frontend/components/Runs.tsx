"use client";
// Multi-agent run visualisation shared by the chat Agents panel and the Runs inspector:
//   RunTimeline - one lane per agent run, model-call / tool-call segments, delegation links, zoom, search highlight
//   RunThread   - the selected agent's role, task and activity grouped as "Thought for Xs" + tool calls with durations
//   CostChart   - cumulative cost over time
import { Fragment, useMemo, useState } from "react";
import { usd } from "@/lib/api";
import Markdown from "./Markdown";
import { IChevron, ITool } from "./Icons";

export type Step = { agent: string; run_id?: string; type: "call" | "result"; tool: string; args?: string; result?: string; ts?: number; ms?: number; ok?: boolean };
export type Call = { agent: string; run_id?: string; provider: string; model: string; cost_usd: number; input_tokens: number; output_tokens: number; t0?: number; t1?: number };
export type Run = {
  run_id: string; parent: string | null; agent: string; spawned: boolean; definition?: any; task: string; status: string; instruction?: string;
  started?: number; ended?: number | null; steps: Step[]; texts: string[]; text_ts?: number[]; calls: Call[]; result?: string | null; cost_usd: number;
  turn?: number; uid?: string; external?: boolean; external_cost?: number | null; thoughts?: string[];
};
export type Lane = { key: string; label: string; agent: string; runs: Run[]; color: string; spawned: boolean; external?: boolean };

const PALETTE = ["#4f7fd9", "#e0794f", "#2aa58f", "#d9a43a", "#5aa452", "#8a66d2", "#d0574f", "#3d9fcc", "#c46aa6", "#7b8a3b"];
const hash = (s: string) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); };
export const colorFor = (name: string) => PALETTE[hash(name.replace(/_\d+$/, "")) % PALETTE.length];
const uid = (r: Run) => r.uid ?? `${r.turn ?? 0}:${r.run_id}`;

/** Root runs of every turn share one lane per root agent; every sub-agent run gets its own lane (numbered when repeated). */
export function buildLanes(runs: Run[]): Lane[] {
  const lanes: Lane[] = [];
  const seen: Record<string, number> = {};
  const total: Record<string, number> = {};
  for (const r of runs) if (r.run_id !== "root") total[r.agent] = (total[r.agent] ?? 0) + 1;
  for (const r of runs) {
    if (r.run_id === "root") {
      const k = `root:${r.agent}`;
      const l = lanes.find((x) => x.key === k);
      if (l) l.runs.push(r); else lanes.push({ key: k, label: r.agent, agent: r.agent, runs: [r], color: colorFor(r.agent), spawned: false });
    } else {
      seen[r.agent] = (seen[r.agent] ?? 0) + 1;
      const base = r.agent.replace(/^.*__/, "");
      lanes.push({ key: uid(r), label: total[r.agent] > 1 ? `${base} ${seen[r.agent]}` : base, agent: r.agent, runs: [r], color: colorFor(base), spawned: r.spawned, external: !!r.external });
    }
  }
  return lanes;
}

export function runText(r: Run): string {
  return [r.agent, r.task, r.instruction, r.result, ...r.texts, ...r.steps.map((s) => `${s.tool} ${s.args ?? ""} ${s.result ?? ""}`)].join("\n").toLowerCase();
}

const fmtDur = (s: number) => s < 1 ? `${Math.round(s * 1000)}ms` : s < 60 ? `${s.toFixed(1)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
export { fmtDur };
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export { clock };

export function RunTimeline({ runs, selected, onSelect, query = "", zoom = 1, height = 300, now }: {
  runs: Run[]; selected?: string; onSelect: (key: string) => void; query?: string; zoom?: number; height?: number; now?: number;
}) {
  const lanes = useMemo(() => buildLanes(runs), [runs]);
  const tNow = now ?? Date.now() / 1000;
  const times = runs.flatMap((r) => [r.started, r.ended ?? (r.status === "running" ? tNow : r.started)].filter((x): x is number => !!x));
  if (!lanes.length) return <div className="small mut" style={{ padding: 12 }}>No agent activity yet.</div>;
  const tStart = times.length ? Math.min(...times) : 0, t1 = times.length ? Math.max(...times) : 1;
  // start the axis at the first real activity (model/tool call): agent setup (e.g. MCP servers starting) is shown as a note
  const act = runs.flatMap((r) => [...r.calls.map((c) => c.t0), ...r.steps.map((s) => (s.ts && s.ms != null ? s.ts - s.ms / 1000 : s.ts))]).filter((v): v is number => !!v);
  const firstAct = act.length ? Math.min(...act) : tStart;
  const setup = firstAct - tStart;
  const t0 = setup > 0.25 * (t1 - tStart) && setup > 0.5 ? firstAct - Math.max((t1 - firstAct) * 0.02, 0.01) : tStart;
  const span = Math.max(t1 - t0, 0.001);
  const x = (t: number) => `${((Math.max(t, t0) - t0) / span) * 100}%`;
  const w = (a: number, b: number) => `max(3px, ${((b - Math.max(a, t0)) / span) * 100}%)`;
  const q = query.trim().toLowerCase();
  const laneIdx = (r: Run) => lanes.findIndex((l) => l.runs.includes(r));
  const ticks = 5;
  return (
    <div className="tl" style={{ maxHeight: height }}>
      <div className="tl-grid" style={{ ["--zoom" as any]: zoom }}>
        <div className="tl-labels">
          <div className="tl-axis-label" />
          {lanes.map((l) => {
            const hit = q && l.runs.some((r) => runText(r).includes(q));
            return <div key={l.key} className={`tl-label ${selected === l.key ? "on" : ""} ${q && !hit ? "dim" : ""}`} onClick={() => onSelect(l.key)} title={l.agent}>
              <i style={{ background: l.color }} />{l.label}{l.spawned && <span className="tl-tag">built</span>}{l.external && <span className="tl-tag ext">external</span>}</div>;
          })}
        </div>
        <div className="tl-scroll">
          <div className="tl-inner" style={{ width: `${100 * zoom}%` }}>
            <div className="tl-axis">{Array.from({ length: ticks + 1 }, (_, i) => <span key={i} style={{ left: `${(i / ticks) * 100}%` }}>
              {i === 0 ? `${clock(t0)}${t0 > tStart ? ` (after ${fmtDur(t0 - tStart)} setup)` : ""}` : `+${fmtDur((span * i) / ticks)}`}</span>)}</div>
            {lanes.map((l) => {
              const hit = q && l.runs.some((r) => runText(r).includes(q));
              return (
                <div key={l.key} className={`tl-lane ${selected === l.key ? "on" : ""} ${q && !hit ? "dim" : ""}`} onClick={() => onSelect(l.key)}>
                  {l.runs.map((r) => {
                    const end = r.ended ?? (r.status === "running" ? tNow : (r.started ?? t0));
                    const tools = r.steps.filter((s) => s.type === "result" && s.ts && s.ms != null);
                    return (
                      <Fragment key={uid(r)}>
                        {r.started && <div className={`tl-run ${r.status}`} style={{ left: x(r.started), width: w(r.started, end), borderColor: l.color }} title={`${l.label} · ${r.status} · ${fmtDur(end - r.started)}`} />}
                        {r.calls.filter((c) => c.t0 && c.t1).map((c, i) => <div key={"c" + i} className="tl-seg" style={{ left: x(c.t0!), width: w(c.t0!, c.t1!), background: l.color }} title={`model call · ${c.model} · ${fmtDur(c.t1! - c.t0!)} · ${usd(c.cost_usd)}`} />)}
                        {tools.map((s, i) => <div key={"t" + i} className="tl-seg tool" style={{ left: x(s.ts! - s.ms! / 1000), width: w(s.ts! - s.ms! / 1000, s.ts!), background: l.color }} title={`${s.tool} · ${Math.round(s.ms!)}ms${s.ok === false ? " · failed" : ""}`} />)}
                      </Fragment>
                    );
                  })}
                </div>
              );
            })}
            {/* delegation links: parent lane -> child lane at the moment the child started */}
            {runs.filter((r) => r.parent && r.started).map((r) => {
              const parent = runs.find((p) => p.run_id === r.parent && (p.turn ?? 0) === (r.turn ?? 0));
              const a = parent ? laneIdx(parent) : -1, b = laneIdx(r);
              if (a < 0 || b < 0) return null;
              const top = Math.min(a, b), bot = Math.max(a, b);
              return <div key={"d" + uid(r)} className="tl-link" style={{ left: x(r.started!), top: 24 + top * 28 + 14, height: (bot - top) * 28 }} />;
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function Mark({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return <>{text}</>;
  return <>{text.slice(0, i)}<mark>{text.slice(i, i + q.length)}</mark><Mark text={text.slice(i + q.length)} q={q} /></>;
}

type Item = { k: "call"; t: number; c: Call } | { k: "tool"; t: number; call: Step; res?: Step } | { k: "text"; t: number; text: string };

function itemsOf(r: Run): Item[] {
  const out: Item[] = [];
  let i = 0;
  r.calls.forEach((c, j) => out.push({ k: "call", t: c.t0 ?? (r.started ?? 0) + j * 1e-6, c }));
  const pending: Step[] = [];
  for (const s of r.steps) {
    if (s.type === "call") { pending.push(s); continue; }
    const idx = pending.findIndex((p) => p.tool === s.tool);
    const call = idx >= 0 ? pending.splice(idx, 1)[0] : { ...s, type: "call" as const };
    out.push({ k: "tool", t: call.ts ?? (s.ts ? s.ts - (s.ms ?? 0) / 1000 : (r.started ?? 0) + (++i) * 1e-6), call, res: s });
  }
  for (const p of pending) out.push({ k: "tool", t: p.ts ?? (r.started ?? 0) + (++i) * 1e-6, call: p });
  r.texts.forEach((t, j) => out.push({ k: "text", t: r.text_ts?.[j] ?? (r.ended ?? r.started ?? 0) - 1e-6 * (r.texts.length - j), text: t }));
  return out.sort((a, b) => a.t - b.t);
}

export function RunThread({ lane, query = "", onSave }: { lane: Lane; query?: string; onSave?: (r: Run) => void }) {
  const [more, setMore] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const q = query.trim().toLowerCase();
  const first = lane.runs[0];
  const instruction = first.instruction || first.definition?.instruction || "";
  return (
    <div className="thread">
      <div className="thread-head"><i style={{ background: lane.color }} /><b>{lane.label}</b>
        {lane.spawned && <span className="pill acc">created by {first.definition?.parent ?? "agent"}</span>}
        {lane.external && <span className="pill ok" title="Runs on its own account; not covered by Agent Studio's caps">external · {first.definition?.label ?? lane.agent} · policy {first.definition?.policy}</span>}
        {lane.external && first.external_cost != null && <span className="mut small">reported {usd(first.external_cost)}</span>}
        <span className="mut small">{lane.runs.length > 1 ? `${lane.runs.length} turns` : first.status}</span>
        <span className="grow" />{lane.spawned && onSave && first.status !== "running" && <button className="ghost small" onClick={() => onSave(first)}>Save as agent</button>}</div>
      {instruction && (
        <div className={`role ${more ? "open" : ""}`}>
          <div className="role-title">Role</div>
          <div className="role-body"><Markdown text={instruction} /></div>
          {!more && instruction.length > 380 && <button className="role-more" onClick={() => setMore(true)}>Read more</button>}
          {lane.spawned && first.definition && <div className="small mut" style={{ marginTop: 8 }}>tier {first.definition.tier} · tools [{(first.definition.tools ?? []).join(", ") || "none"}] · MCP [{(first.definition.mcp ?? []).join(", ") || "none"}]</div>}
        </div>
      )}
      {lane.runs.map((r) => {
        const items = itemsOf(r);
        const groups: { call?: Call; rest: Item[] }[] = [];
        for (const it of items) { if (it.k === "call") groups.push({ call: it.c, rest: [] }); else { if (!groups.length) groups.push({ rest: [] }); groups[groups.length - 1].rest.push(it); } }
        return (
          <div key={uid(r)} className="thread-run">
            <div className="msg-chip"><span style={{ background: "var(--panel2)" }}>{r.run_id === "root" ? "you" : (r.parent === "root" ? "lead agent" : "parent agent")}</span>{r.started && <span className="mut small">{clock(r.started)}</span>}</div>
            <div className="task"><Mark text={r.task || "(no task text)"} q={q} /></div>
            {(r.thoughts ?? []).filter(Boolean).length > 0 && (
              <details className="think-box" style={{ marginTop: 8 }}><summary>Thinking ({r.thoughts!.filter(Boolean).length})<IChevron size={13} /></summary>
                <div className="think-body">{r.thoughts!.filter(Boolean).map((t, i) => <Markdown key={i} text={t} />)}</div></details>)}
            <div className="msg-chip"><span style={{ background: lane.color, color: "#fff" }}>{lane.label}</span>{r.started && <span className="mut small">{r.ended ? fmtDur(r.ended - r.started) : "running…"} · {usd(r.cost_usd || r.calls.reduce((a, c) => a + c.cost_usd, 0))}</span>}</div>
            {groups.map((g, gi) => (
              <div key={gi} className="think">
                {g.call && <div className="think-h">Thought for {g.call.t0 && g.call.t1 ? fmtDur(g.call.t1 - g.call.t0) : "a moment"}
                  <span className="mut small"> · {g.call.model} · {g.call.input_tokens}→{g.call.output_tokens} tok · {usd(g.call.cost_usd)}</span></div>}
                {g.rest.map((it, ii) => it.k === "tool" ? (
                  <div key={ii}>
                    <div className={`tool-row ${it.res?.ok === false ? "bad" : ""}`} onClick={() => setOpen((o) => ({ ...o, [`${gi}-${ii}`]: !o[`${gi}-${ii}`] }))}>
                      <ITool size={13} /><b>{it.call.tool}</b><span className="args"><Mark text={it.call.args ?? ""} q={q} /></span>
                      <span className="ms">{it.res?.ms != null ? `${Math.round(it.res.ms)}ms` : it.res ? "" : "…"}</span>
                      <span style={{ transform: open[`${gi}-${ii}`] ? "rotate(90deg)" : "none", display: "grid", color: "var(--faint)" }}><IChevron size={13} /></span></div>
                    {open[`${gi}-${ii}`] && <pre className="tool-out"><Mark text={it.res?.result ?? "(no result yet)"} q={q} /></pre>}
                  </div>
                ) : it.k === "text" ? <div key={ii} className="said"><Markdown text={it.text} /></div> : null)}
              </div>
            ))}
            {r.result && <div className="report"><div className="role-title">{r.run_id === "root" ? "Answer" : "Report to parent"}</div><Markdown text={r.result} /></div>}
            {r.status === "running" && <div className="thinking small">working…</div>}
            {(r.status === "error" || r.status === "cancelled") && <div className="notice">{r.status}</div>}
          </div>
        );
      })}
    </div>
  );
}

export function CostChart({ calls, height = 110 }: { calls: Call[]; height?: number }) {
  const pts = calls.filter((c) => c.t1).sort((a, b) => a.t1! - b.t1!);
  if (pts.length < 1) return <div className="small mut">No timed model calls yet.</div>;
  let acc = 0;
  const series = pts.map((c) => ({ t: c.t1!, v: (acc += c.cost_usd) }));
  const t0 = pts[0].t0 ?? series[0].t, t1 = series[series.length - 1].t, vmax = Math.max(acc, 1e-9);
  const W = 300, H = height, px = (t: number) => ((t - t0) / Math.max(t1 - t0, 1e-6)) * (W - 8) + 4, py = (v: number) => H - 18 - (v / vmax) * (H - 30);
  let d = `M${px(t0)},${py(0)}`;
  for (const p of series) d += ` H${px(p.t)} V${py(p.v)}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="cost-chart">
      <path d={`${d} V${py(0)} Z`} className="area" /><path d={d} className="line" fill="none" />
      <circle cx={px(series[series.length - 1].t)} cy={py(acc)} r={3} className="dotc" />
      <text x={4} y={12} className="lbl">{usd(acc)}</text>
      <text x={4} y={H - 4} className="lbl">{clock(t0)}</text><text x={W - 4} y={H - 4} className="lbl" textAnchor="end">{clock(t1)}</text>
    </svg>
  );
}
