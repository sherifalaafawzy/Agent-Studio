"use client";
// Red-team tools (garak, PyRIT, promptfoo): installed on their own, then pointed at ONE of your agents through a temporary address on this
// server. The agent keeps its real tools, guardrails and spend limits. What we can count for sure: probes sent, stopped by a guardrail, failed.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Tool = { id: string; label: string; blurb: string; licence: string; size_mb: number; notes: string; options: string; default_options: string[]; state: string; version: string | null; error: string | null };
type Status = { tools: Tool[]; install: { running: boolean; tool: string | null; log: string[]; error: string | null }; runs_running: number };
type Plan = { label: string; licence: string; download_mb: number; packages: string[]; notes: string; blocked: string | null; free_gb: number | null; disk_gb_needed: number };
type Help = { code: string; short: string; message: string; options: string[] };
type Run = { id: string; tool: string; agent: string; status: string; started: number; max_requests: number; requests: number; blocked: number; errors: number; error: string; log_tail?: string[]; help?: Help | null };
type Pre = { account: "none" | "needs_verification" | "verified"; hosted_ready: boolean; verify_command: string; local_defaults: string[] };

export default function RedTeamPanel() {
  const [st, setSt] = useState<Status | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [provs, setProvs] = useState<{ id: string; label: string; enabled: boolean }[]>([]);
  const [plan, setPlan] = useState<{ tool: string; p: Plan } | null>(null);
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState("");
  const [f, setF] = useState({ tool: "", agent: "", provider: "", max: "50", options: "", purpose: "" });
  const [tails, setTails] = useState<Record<string, string[]>>({});
  const [local, setLocal] = useState(true);
  const [pre, setPre] = useState<Pre | null>(null);

  const load = useCallback(() => Promise.all([api<Status>("/api/redteam/status"), api<{ runs: Run[] }>("/api/redteam/runs")])
    .then(([s, r]) => { setSt(s); setRuns(r.runs); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => {
    load();
    api<{ id: string; name: string }[]>("/api/agents").then(setAgents).catch(() => setAgents([]));
    api<{ id: string; label: string; enabled: boolean }[]>("/api/providers").then(setProvs).catch(() => setProvs([]));
  }, [load]);
  const loadPre = useCallback(() => api<Pre>("/api/redteam/preflight?tool=promptfoo").then(setPre).catch(() => setPre(null)), []);
  useEffect(() => { loadPre(); }, [loadPre]);
  const busy = !!st && (st.install.running || runs.some((r) => r.status === "running"));
  useEffect(() => { if (!busy) return; const t = setInterval(load, 3000); return () => clearInterval(t); }, [busy, load]);

  const call = async (path: string, init: RequestInit, done: string) => {
    try { await api(path, init); toast(done); await load(); return true; } catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!st) return err ? <p className="err" role="alert">{err}</p> : null;

  const ready = st.tools.filter((t) => t.state === "ready");
  const pick = ready.find((t) => t.id === f.tool) ?? ready[0];
  const isPf = pick?.id === "promptfoo";
  const hostedBlocked = isPf && !local && !(pre?.hosted_ready);
  const copy = (t: string) => { try { navigator.clipboard.writeText(t); toast("Copied"); } catch { /* the command is shown in full anyway */ } };
  const options = f.options.trim() ? f.options.split("\n").map((x) => x.trim()).filter(Boolean) : undefined;
  const open = async (id: string) => { try { setPlan({ tool: id, p: await api<Plan>(`/api/redteam/plan/${id}`) }); setOk(false); } catch (e) { setErr((e as Error).message); } };
  const tail = async (id: string) => { try { setTails({ ...tails, [id]: (await api<Run>(`/api/redteam/runs/${id}?tail=15`)).log_tail ?? [] }); } catch (e) { setErr((e as Error).message); } };

  return (
    <div data-testid="redteam">
      <div className="card">
        <div className="card-head"><b>Red-team tools</b><InfoTip id="redteam" /></div>
        <p className="small mut">These programs attack an agent with tricky prompts to find weak spots. Each is installed on its own, only after you accept its size and licence. They never get a key or an address on the internet: they talk to a temporary address on this PC that hands every probe to the agent you choose. The agent keeps its guardrails and spend limits, and every probe uses model credits.</p>
        {err && <p className="err" role="alert">{err}</p>}
        <div className="cat-grid">{st.tools.map((t) => (
          <div key={t.id} className="card cat-card" data-testid="rt-tool">
            <div className="card-head"><b>{t.label}</b><span className={`pill ${t.state === "ready" ? "ok" : t.state === "failed" ? "bad" : ""}`}>{t.state === "ready" ? `installed ${t.version ?? ""}` : t.state.replace("_", " ")}</span></div>
            <div className="small">{t.blurb}</div>
            <div className="small mut">{t.licence} · about {t.size_mb} MB · {t.notes}</div>
            {t.error && <div className="small err">{t.error}</div>}
            <div className="row" style={{ marginTop: 8 }}>
              {t.state === "ready"
                ? <button className="small" onClick={() => { if (confirm(`Remove ${t.label}?`)) call("/api/redteam/uninstall", { method: "POST", body: JSON.stringify({ tool: t.id }) }, `${t.label} removed`); }}>Remove</button>
                : t.state === "installing" ? <span className="small mut">Installing…</span> : <button className="small" onClick={() => open(t.id)}>Install…</button>}
            </div>
            {plan?.tool === t.id && <div className="cat-panel">
              <div className="small">Download about {plan.p.download_mb} MB · needs about {plan.p.disk_gb_needed} GB free{plan.p.free_gb != null ? ` (${plan.p.free_gb} GB free)` : ""} · {plan.p.packages.join(", ")}</div>
              {plan.p.blocked ? <p className="small err" role="alert">{plan.p.blocked}</p> : <>
                <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />I accept the {plan.p.licence} licence and the download size</label>
                <div className="row"><button className="pri small" disabled={!ok} onClick={async () => { if (await call("/api/redteam/install", { method: "POST", body: JSON.stringify({ tool: t.id, accepted: true }) }, "Install started")) setPlan(null); }}>Install</button>
                  <button className="small" onClick={() => setPlan(null)}>Cancel</button></div></>}
            </div>}
          </div>))}</div>
        {st.install.running && <pre className="log" aria-label="Install progress">{st.install.log.join("\n")}</pre>}
      </div>

      <div className="card">
        <div className="card-head"><b>Run an attack</b></div>
        {ready.length === 0 ? <p className="small mut">Install a tool above first.</p> : (<>
          <div className="grid">
            <label>Tool<select value={pick?.id} onChange={(e) => setF({ ...f, tool: e.target.value, options: "" })}>{ready.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
            <label>Agent to attack<select value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })}><option value="">Choose an agent…</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
            <label>Model provider<select value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })}><option value="">Choose…</option>{provs.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
            <label>At most this many probes<input type="number" min={1} max={1000} value={f.max} onChange={(e) => setF({ ...f, max: e.target.value })} /></label>
          </div>
          <label style={{ marginTop: 8 }}>{pick.options === "probes" ? "Probes" : pick.options === "plugins" ? "Risks to test" : "Objectives"} (one per line; empty = defaults)
            <textarea rows={3} value={f.options} placeholder={(isPf && local && pre ? pre.local_defaults : pick.default_options).join("\n")} onChange={(e) => setF({ ...f, options: e.target.value })} /></label>
          {isPf && <fieldset className="small" style={{ marginTop: 8, border: "1px solid var(--line)", borderRadius: 8, padding: 8 }} data-testid="pf-mode">
            <legend>Who writes the attacks?<InfoTip id="redteam-local" /></legend>
            <label style={{ flexDirection: "row", alignItems: "flex-start", gap: 6 }}><input type="radio" name="pfmode" checked={local} onChange={() => setLocal(true)} />
              <span><b>Your own model (Local only, recommended)</b><br /><span className="mut">The provider you chose above writes and grades the attacks, through a private address on this PC. Nothing goes to promptfoo and no e-mail is needed. Fewer kinds of risk (64 of 155) and simpler attack styles. Each probe makes several model calls, so it costs more credits.</span></span></label>
            <label style={{ flexDirection: "row", alignItems: "flex-start", gap: 6, marginTop: 6 }}><input type="radio" name="pfmode" checked={!local} onChange={() => setLocal(false)} />
              <span><b>promptfoo&apos;s hosted service</b><br /><span className="mut">Wider choice of risks, but promptfoo asks for a verified work e-mail that you must give it yourself (Agent Studio never types it for you), and your agent&apos;s purpose text is sent to promptfoo.</span></span></label>
            {!local && <div data-testid="pf-verify" style={{ marginTop: 6 }}>
              {pre?.hosted_ready ? <span className="pill ok">e-mail verified in promptfoo</span> : <>
                <p className="small err" role="alert">{pre?.account === "needs_verification" ? "promptfoo has an e-mail on file but it is not verified yet." : "promptfoo has no verified e-mail on this PC yet."} Do this once in your own terminal, answer its questions yourself, then press &quot;Check again&quot;:</p>
                <pre className="log" aria-label="Command to verify promptfoo yourself">{pre?.verify_command ?? ""}</pre>
                <div className="row"><button className="small" onClick={() => copy(pre?.verify_command ?? "")}>Copy command</button><button className="small" onClick={loadPre}>Check again</button><button className="small" onClick={() => setLocal(true)}>Use Local only instead</button></div></>}
            </div>}
          </fieldset>}
          {isPf && <label style={{ marginTop: 8 }}>{local ? "What the agent is for (given to your own model to write attacks)" : "What the agent is for (sent to promptfoo to write attacks)"}<input value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} placeholder="A support assistant for…" /></label>}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="pri" disabled={!f.agent || !f.provider || busy || hostedBlocked} onClick={() => call("/api/redteam/runs", { method: "POST", body: JSON.stringify({ tool: pick.id, agent: f.agent, provider: f.provider, max_requests: Number(f.max), options, purpose: f.purpose, ...(isPf ? { local_only: local } : {}) }) }, "Run started")}>Start</button>
            <span className="small mut">{isPf ? "promptfoo's update check, telemetry and result sharing are switched off for every run." : ""}</span></div></>)}
      </div>

      <div className="card">
        <div className="card-head"><b>Runs</b></div>
        {runs.length === 0 && <p className="small mut">No runs yet.</p>}
        {runs.map((r) => (
          <div key={r.id} style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }} data-testid="rt-run">
            <div className="row" style={{ flexWrap: "wrap" }}><b>{r.tool}</b> → {r.agent}<span className={`pill ${r.status === "done" ? "ok" : r.status === "running" ? "acc" : "bad"}`}>{r.status}</span><span className="grow" />
              {r.status === "running" && <button className="small" onClick={() => call(`/api/redteam/runs/${r.id}/cancel`, { method: "POST" }, "Run cancelled")}>Cancel</button>}
              <button className="small" onClick={() => tail(r.id)}>Show log</button></div>
            <div className="small">{r.requests} probes sent (limit {r.max_requests}) · <b>{r.blocked}</b> stopped by a guardrail · {r.errors} failed{r.error ? ` · ${r.error}` : ""}</div>
            {r.status === "failed" && r.help && <div className="small" role="note" data-testid="rt-help" style={{ marginTop: 4 }}><p>{r.help.message}</p>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {r.help.options.includes("local_only") && <button className="small" onClick={() => { setF({ ...f, tool: "promptfoo" }); setLocal(true); toast("Local only is selected - press Start"); }}>Use Local only</button>}
                {r.help.options.includes("verify_yourself") && <button className="small" onClick={() => { setF({ ...f, tool: "promptfoo" }); setLocal(false); }}>Verify it myself</button>}
                {r.help.options.includes("other_tools") && <span className="mut">or try garak or PyRIT.</span>}</div></div>}
            {tails[r.id] && <pre className="log">{tails[r.id].join("\n") || "(the tool has written nothing yet)"}</pre>}
          </div>))}
      </div>
    </div>
  );
}
