"use client";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, usd } from "@/lib/api";
import { buildLanes, clock, CostChart, fmtDur, Run, RunThread, RunTimeline, runText } from "@/components/Runs";
import { IAgents, ISearch } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";

type SessionRow = { id: string; title: string; provider: string; agent: string | null; model: string | null; workspace: string | null; created: number; updated: number; messages: number };
type SideTab = "session" | "events" | "tools";

const ago = (ts: number) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : s < 172800 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`; };

function SessionList() {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => { api<SessionRow[]>("/api/chat/sessions?limit=200").then(setRows).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="page">
      <div className="page-head"><h2>Runs</h2><InfoTip id="runs" /></div>
      <p className="page-sub">Every saved chat as a multi-agent run: a timeline of all agents (including ones agents built themselves), each agent&apos;s thread, tool calls with timings, and cost over time.</p>
      {err && <p className="err">Backend offline: {err}</p>}
      {rows.length === 0 ? <div className="empty">No runs yet — start a chat with an agent.</div> : (
        <div className="card" style={{ padding: 6 }}><table><thead><tr><th>Run</th><th>Agent</th><th>Provider</th><th>Messages</th><th>Updated</th></tr></thead><tbody>
          {rows.map((r) => <tr key={r.id}><td><Link href={`/runs/?s=${r.id}`}><b>{r.title}</b></Link></td><td>{r.agent ?? "plain chat"}</td><td>{r.provider}</td><td>{r.messages}</td><td className="mut">{ago(r.updated)}</td></tr>)}
        </tbody></table></div>
      )}
    </div>
  );
}

function Inspector({ sid }: { sid: string }) {
  const [sess, setSess] = useState<SessionRow | null>(null);
  const [msgs, setMsgs] = useState<any[]>([]);
  const [sel, setSel] = useState<string>("");
  const [q, setQ] = useState("");
  const [zoom, setZoom] = useState(1);
  const [tab, setTab] = useState<SideTab>("session");
  const [live, setLive] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(() => api<{ session: SessionRow; messages: any[] }>(`/api/chat/sessions/${sid}`)
    .then((r) => { setSess(r.session); setMsgs(r.messages); }).catch((e) => setNote(e.message)), [sid]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!live) return; const t = setInterval(load, 3000); return () => clearInterval(t); }, [live, load]);

  const runs: Run[] = useMemo(() => {
    const out: Run[] = [];
    let turn = 0;
    for (const m of msgs) {
      if (m.role !== "bot") continue;
      turn++;
      for (const r of (m.meta?.runs ?? []) as Run[]) out.push({ ...r, turn, uid: `${turn}:${r.run_id}` });
    }
    return out;
  }, [msgs]);
  const memEvents = useMemo(() => msgs.flatMap((m) => (m.role === "bot" ? ((m.meta?.memory ?? []) as { kind: string; type?: string; text?: string; file?: string }[]) : [])), [msgs]);
  const lanes = useMemo(() => buildLanes(runs), [runs]);
  useEffect(() => { if (lanes.length && !lanes.some((l) => l.key === sel)) setSel(lanes[0].key); }, [lanes, sel]);
  const lane = lanes.find((l) => l.key === sel);

  const calls = runs.flatMap((r) => r.calls);
  const steps = runs.flatMap((r) => r.steps.map((s) => ({ ...s, lane: r.agent })));
  const cost = calls.reduce((a, c) => a + c.cost_usd, 0);
  const tin = calls.reduce((a, c) => a + c.input_tokens, 0), tout = calls.reduce((a, c) => a + c.output_tokens, 0);
  const roots = runs.filter((r) => r.run_id === "root");
  const active = roots.reduce((a, r) => a + ((r.ended ?? r.started ?? 0) - (r.started ?? 0)), 0);
  const subs = runs.filter((r) => r.run_id !== "root");
  const tools = Object.entries(steps.filter((s) => s.type === "result").reduce<Record<string, { n: number; ms: number; bad: number }>>((acc, s) => {
    const k = s.tool; acc[k] = acc[k] ?? { n: 0, ms: 0, bad: 0 }; acc[k].n++; acc[k].ms += s.ms ?? 0; if (s.ok === false) acc[k].bad++; return acc;
  }, {})).sort((a, b) => b[1].n - a[1].n);
  const events = useMemo(() => {
    const ev: { t: number; who: string; what: string }[] = [];
    for (const r of runs) {
      if (r.started) ev.push({ t: r.started, who: r.agent, what: r.run_id === "root" ? "turn started" : `started${r.spawned ? " (built by agent)" : ""}` });
      if (r.ended) ev.push({ t: r.ended, who: r.agent, what: `${r.status} · ${usd(r.cost_usd)}` });
      for (const c of r.calls) if (c.t1) ev.push({ t: c.t1, who: r.agent, what: `model ${c.model} · ${c.input_tokens}→${c.output_tokens} tok · ${usd(c.cost_usd)}` });
      for (const s of r.steps) if (s.type === "result" && s.ts) ev.push({ t: s.ts, who: r.agent, what: `${s.tool} · ${Math.round(s.ms ?? 0)}ms${s.ok === false ? " · failed" : ""}` });
    }
    const qq = q.trim().toLowerCase();
    return ev.filter((e) => !qq || (e.who + " " + e.what).toLowerCase().includes(qq)).sort((a, b) => a.t - b.t);
  }, [runs, q]);
  const matches = q.trim() ? runs.filter((r) => runText(r).includes(q.trim().toLowerCase())).length : 0;

  async function save(r: Run) {
    const id = prompt("Save this agent permanently. Id (a-z, 0-9, _):", (r.definition?.name ?? r.agent).toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 28));
    if (!id) return;
    try {
      await api("/api/agents", { method: "POST", body: JSON.stringify({ id, name: r.definition?.name ?? id, description: r.task.slice(0, 120), instruction: r.definition?.instruction ?? r.instruction ?? "",
        tier: r.definition?.tier ?? "flash", tools: r.definition?.tools ?? [], mcp: r.definition?.mcp ?? [] }) });
      setNote(`Saved agent “${id}”.`);
    } catch (e) { setNote("⚠ " + (e as Error).message); }
  }

  return (
    <div className="insp">
      <div className="insp-main">
        <div className="insp-head">
          <div className="crumb"><Link href="/runs/">Runs</Link> / <code>{sid.slice(0, 12)}…</code></div>
          <div className="insp-title">
            <h2>{sess?.title ?? "…"}</h2>
            <span className="metric"><IAgents size={13} />{sess?.agent ?? "plain chat"}</span>
            <span className="metric">{sess?.provider}</span>
            <span className="metric">⏱ {fmtDur(active)}</span>
            <span className="metric">{(tin / 1000).toFixed(1)}K → {(tout / 1000).toFixed(1)}K tok</span>
            <span className="metric">{usd(cost)}</span>
            <span className="metric">{subs.length} sub-agent{subs.length === 1 ? "" : "s"}{subs.some((s) => s.spawned) ? ` · ${subs.filter((s) => s.spawned).length} built` : ""}</span>
            {sess && <span className="mut small">{ago(sess.updated)}</span>}
            <span className="grow" />
            <Link href={`/?s=${sid}`} className="chip">Open chat</Link>
          </div>
        </div>
        <div className="insp-tools">
          <div style={{ position: "relative" }}>
            <input placeholder="Find in transcript" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 30, width: 260 }} />
            <span style={{ position: "absolute", left: 10, top: 9, color: "var(--mut)" }}><ISearch size={14} /></span></div>
          {q && <span className="small mut">{matches} agent run{matches === 1 ? "" : "s"} match</span>}
          <span className="grow" />
          <label className="row small" style={{ flexDirection: "row", gap: 6 }}><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />auto-refresh</label>
          <div className="seg">{[1, 2, 4, 8].map((z) => <button key={z} className={zoom === z ? "on" : ""} onClick={() => setZoom(z)}>{z}×</button>)}</div>
        </div>
        <div className="insp-tl"><RunTimeline runs={runs} selected={sel} onSelect={setSel} query={q} zoom={zoom} height={320} /></div>
        <div className="insp-thread">
          {note && <div className="small mut" style={{ marginBottom: 8 }}>{note}</div>}
          {lane ? <RunThread key={lane.key} lane={lane} query={q} onSave={save} /> : <div className="empty">No agent runs in this chat yet.</div>}
        </div>
      </div>

      <aside className="insp-side">
        <div className="tabs" role="tablist">{(["session", "events", "tools"] as SideTab[]).map((t) => <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
        {tab === "session" && sess && (<>
          <div className="kv">
            <span>ID</span><code style={{ wordBreak: "break-all", fontSize: 12 }}>{sid}</code>
            <span>Agent</span><span>{sess.agent ?? "plain chat"}</span>
            <span>Provider</span><span>{sess.provider}{sess.model ? ` · ${sess.model}` : ""}</span>
            <span>Workspace</span><span style={{ wordBreak: "break-all" }}>{sess.workspace || "default"}</span>
            <span>Created</span><span>{new Date(sess.created * 1000).toLocaleString()}</span>
            <span>Updated</span><span>{new Date(sess.updated * 1000).toLocaleString()}</span>
          </div>
          <div className="row" style={{ margin: "18px 0 4px" }}><b>Cost</b><span className="grow" /><b>{usd(cost)}</b></div>
          <CostChart calls={calls} />
          <div style={{ marginTop: 16 }}><b>Usage</b></div>
          <div className="kv" style={{ marginTop: 8 }}>
            <span>Turns</span><span>{roots.length}</span>
            <span>Model calls</span><span>{calls.length}</span>
            <span>Input tokens</span><span>{tin.toLocaleString()}</span>
            <span>Output tokens</span><span>{tout.toLocaleString()}</span>
            <span>Tool calls</span><span>{steps.filter((s) => s.type === "call").length}</span>
            <span>Sub-agent runs</span><span>{subs.length}</span>
            <span>Built by agents</span><span>{subs.filter((s) => s.spawned).length}</span>
            <span>Active time</span><span>{fmtDur(active)}</span>
            <span>Memories used</span><span>{memEvents.filter((x) => x.kind === "recalled").length}</span>
            <span>Memories saved</span><span>{memEvents.filter((x) => x.kind === "saved" || x.kind === "pending").length}</span>
          </div>
          {memEvents.length > 0 && <details style={{ marginTop: 10 }}><summary className="small">What it remembered</summary>
            {memEvents.map((x, i) => <div key={i} className="small mut">• {x.kind}{x.type ? ` (${x.type})` : ""}: {x.text ?? x.file ?? ""}</div>)}</details>}
          <div className="small mut" style={{ marginTop: 12 }}>Caps cover every one of these calls. External ACP agents are tracked separately (Billing / Telemetry).</div>
        </>)}
        {tab === "events" && (<div>{events.length === 0 && <div className="small mut">No timed events.</div>}
          {events.map((e, i) => <div key={i} className="ev"><span className="t">{clock(e.t)}</span><span><b>{e.who.replace(/^.*__/, "")}</b> <span className="mut">{e.what}</span></span></div>)}</div>)}
        {tab === "tools" && (<table><thead><tr><th>Tool</th><th>Calls</th><th>Avg</th><th>Failed</th></tr></thead><tbody>
          {tools.map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td>{v.n}</td><td>{v.n ? `${Math.round(v.ms / v.n)}ms` : "—"}</td><td>{v.bad || ""}</td></tr>)}
          {tools.length === 0 && <tr><td colSpan={4} className="mut">No tool calls.</td></tr>}</tbody></table>)}
      </aside>
    </div>
  );
}

function RunsPage() {
  const sid = useSearchParams().get("s");
  return sid ? <Inspector sid={sid} /> : <SessionList />;
}

export default function Page() { return <Suspense fallback={null}><RunsPage /></Suspense>; }
