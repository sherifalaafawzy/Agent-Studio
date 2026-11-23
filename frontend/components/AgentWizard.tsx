"use client";
// A guided way to create an agent. It builds exactly the same agent definition the advanced editor builds and saves it
// through the same API, so nothing created here is different from anything created there.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, streamSse } from "@/lib/api";
import AgentAgui, { aguiBadge, type AguiFlags } from "./AgentAgui";
import InfoTip from "./InfoTip";
import { MemoryPicker, type MemCfg } from "./MemoryUI";
import ModelSelect from "./ModelSelect";
import { toast } from "./Nav";

export type NewAgent = {
  id: string; name: string; description: string; instruction: string; provider: string | null; tier: string; model: string | null; tools: string[]; mcp: string[];
  skills: string[]; sub_agents: string[]; builtin: boolean; can_spawn: boolean; acp_agents: string[]; acp_policy: string; rag: string[]; memory?: MemCfg; agui?: AguiFlags;
};
type Template = { id: string; name: string; tagline: string; color: string; risk: string; agent: Partial<NewAgent> & { name: string } };
type Tool = { name: string; label: string; plain: string; risk: string; risk_text: string };
type Wiz = { templates: Template[]; tools: Tool[]; tiers: { id: string; label: string; text: string }[]; risk_text: Record<string, string> };
type Prov = { id: string; label: string; kind: string; enabled: boolean; auth_ready?: boolean };
type Mcp = { id: string; label: string; ready: boolean };
type Skill = { name: string; description: string; group?: string };
type Kb = { id: string; label: string };
type Ext = { id: string; label: string; installed: boolean; enabled: boolean };
type Brain = "same" | "model" | "local";

const STEPS: [string, string][] = [["goal", "Purpose"], ["name", "Name"], ["brain", "Model"], ["use", "Abilities"], ["allow", "Permissions"], ["how", "Instructions"], ["review", "Review"]];
const RISK_TONE: Record<string, string> = { safe: "ok", changes: "warn", runs: "warn", saves: "warn", app: "bad" };
const RISK_WORD: Record<string, string> = { safe: "only reads", changes: "changes files", runs: "runs commands", saves: "saves results", app: "changes Agent Studio" };
const toggle = (l: string[], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v]);

/** A valid agent id from a name: lowercase letters, digits and underscores, starting with a letter, unique among `taken`. */
export function makeId(name: string, taken: string[]): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 26);
  if (!/^[a-z]/.test(base)) base = "agent_" + base;
  let id = base, n = 2;
  while (taken.includes(id)) id = `${base}_${n++}`;
  return id.length >= 2 ? id : id + "_1";
}

export default function AgentWizard({ agents, mcp, skills, kbs, ext, onCreated, onAdvanced, onCancel }: {
  agents: { id: string; name: string }[]; mcp: Mcp[]; skills: Skill[]; kbs: Kb[]; ext: Ext[];
  onCreated: (id: string) => void; onAdvanced: () => void; onCancel: () => void;
}) {
  const [step, setStep] = useState(0);
  const [wiz, setWiz] = useState<Wiz | null>(null);
  const [provs, setProvs] = useState<Prov[]>([]);
  const [tpl, setTpl] = useState<string>("");
  const [goal, setGoal] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [brain, setBrain] = useState<Brain>("same");
  const [tier, setTier] = useState("flash");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [mcps, setMcps] = useState<string[]>([]);
  const [sk, setSk] = useState<string[]>([]);
  const [rag, setRag] = useState<string[]>([]);
  const [memory, setMemory] = useState<MemCfg>({});
  const [subs, setSubs] = useState<string[]>([]);
  const [spawn, setSpawn] = useState(false);
  const [acp, setAcp] = useState<string[]>([]);
  const [policy, setPolicy] = useState("ask");
  const [agui, setAgui] = useState<AguiFlags | undefined>(undefined);       // untouched = the default chosen in Settings
  const [instr, setInstr] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const top = useRef<HTMLDivElement>(null);
  useEffect(() => { if (wiz) top.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [wiz]);     // on a narrow screen the guide sits below the list: bring it into view
  useEffect(() => {
    api<Wiz>("/api/easy/wizard").then(setWiz).catch((e) => setErr("Could not load the wizard: " + e.message));
    api<Prov[]>("/api/providers").then(setProvs).catch(() => setProvs([]));
  }, []);

  const id = useMemo(() => makeId(name || "agent", agents.map((a) => a.id)), [name, agents]);
  const usable = provs.filter((p) => p.enabled && p.kind !== "mock");
  const known = (list: string[], have: string[]) => list.filter((x) => have.includes(x));

  function applyTemplate(t: Template | null) {
    setTpl(t?.id ?? "");
    if (!t) return;
    const a = t.agent;
    setName((n) => n || a.name); setDesc(a.description ?? ""); setGoal((g) => g || a.description || "");
    setTier(a.tier ?? "flash"); setTools(a.tools ?? []); setInstr(a.instruction ?? "");
    setSk(known(a.skills ?? [], skills.map((s) => s.name))); setSubs(known(a.sub_agents ?? [], agents.map((x) => x.id))); setRag(a.rag ?? []); setMemory(a.memory ?? {});
    setSpawn(!!a.can_spawn); setAcp(known(a.acp_agents ?? [], ext.map((e) => e.id))); setPolicy(a.acp_policy ?? "ask");
  }

  async function draft() {
    setDrafting(true); setErr(""); setNote("");
    try {
      const r = await api<{ instruction: string; cost_usd: number; provider: string; model: string }>("/api/easy/wizard/draft", { method: "POST", body: JSON.stringify({ goal: goal || desc, name, tools }) });
      setInstr(r.instruction); setNote(`Drafted with ${r.provider} · ${r.model}${r.cost_usd ? ` (cost about $${r.cost_usd.toFixed(4)})` : ""}. Read it and change anything you like.`);
    } catch (e) { setErr((e as Error).message); }
    setDrafting(false);
  }

  const brainOk = brain === "same" || (brain === "model" && !!provider && !!model) || (brain === "local" && !!model);
  const valid: boolean[] = [goal.trim().length >= 3 || !!tpl, name.trim().length > 0, brainOk, true, true, true, true];

  const def = (): NewAgent => ({
    id, name: name.trim(), description: desc.trim() || goal.trim().slice(0, 200), instruction: instr, builtin: false,
    provider: brain === "same" ? null : brain === "local" ? "ollama" : provider, tier: brain === "same" ? tier : "custom", model: brain === "same" ? null : model,
    tools, mcp: mcps, skills: sk, sub_agents: subs, can_spawn: spawn, acp_agents: acp, acp_policy: policy, rag, memory, ...(agui ? { agui } : {}),
  });

  async function create() {
    setSaving(true); setErr("");
    try { await api("/api/agents", { method: "POST", body: JSON.stringify(def()) }); setCreated(id); toast(`Created ${name}`); }
    catch (e) { setErr("⚠ " + (e as Error).message); }
    setSaving(false);
  }

  if (!wiz) return <div className="card"><div className="small">{err || "Loading…"}</div><div className="row" style={{ marginTop: 8 }}><button className="ghost small" onClick={onAdvanced}>Advanced editor</button><button className="ghost small" onClick={onCancel}>Cancel</button></div></div>;
  if (created) return <TryIt id={created} name={name} provider={def().provider ?? usable[0]?.id ?? ""} onOpen={() => onCreated(created)} onAnother={() => { setCreated(null); setStep(0); setName(""); setGoal(""); setDesc(""); setInstr(""); setTpl(""); setTools([]); }} />;

  const safeTools = wiz.tools.filter((t) => t.risk === "safe").map((t) => t.name);
  const changeTools = wiz.tools.filter((t) => t.risk !== "app");
  const groups = new Map<string, Skill[]>();
  for (const s of skills) groups.set(s.group || "General", [...(groups.get(s.group || "General") ?? []), s]);
  const installedExt = ext.filter((e) => e.installed && e.enabled);

  return (
    <div className="card wiz" data-tour="agent-wizard" ref={top}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Create an agent</b>
        <span className="row"><button className="ghost small" onClick={onAdvanced}>Advanced editor</button><button className="ghost small" onClick={onCancel}>Cancel</button></span>
      </div>
      <ol className="wiz-steps" aria-label="Steps">{STEPS.map(([k, label], i) => (
        <li key={k} className={`${i === step ? "on" : ""} ${i < step ? "done" : ""}`} aria-current={i === step ? "step" : undefined}><span>{i + 1}</span><em>{label}</em></li>))}</ol>

      {step === 0 && (<div>
        <h3>What should this agent do?</h3>
        <textarea rows={3} value={goal} aria-label="What the agent should do" placeholder="For example: read our support emails each morning and list the urgent ones." onChange={(e) => setGoal(e.target.value)} />
        <div className="small mut" style={{ margin: "14px 0 6px" }}>Or start from a ready-made agent and change what you like:</div>
        <div className="wiz-tpls">{wiz.templates.map((t) => (
          <button key={t.id} className={`wiz-tpl ${tpl === t.id ? "on" : ""}`} aria-pressed={tpl === t.id} onClick={() => applyTemplate(tpl === t.id ? null : t)} style={{ borderLeftColor: t.color }}>
            <b>{t.name}</b><span className="small mut">{t.tagline}</span></button>))}</div>
      </div>)}

      {step === 1 && (<div>
        <h3>Give it a name</h3>
        <label>Name<input value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="e.g. Inbox helper" /></label>
        <label style={{ marginTop: 10 }}>One-line description
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder={goal.slice(0, 80) || "What it is good at"} /><InfoTip id="agent-description" /></label>
        <div className="small mut" style={{ marginTop: 8 }}>Its short id will be <code>{id}</code>. You can use the advanced editor if you need a different one.</div>
      </div>)}

      {step === 2 && (<div>
        <h3>Which model should it think with? <InfoTip id="tiers" /></h3>
        <div className="wiz-choice">
          {([["same", "Same as my chat", "Uses whatever provider you chat with (or Auto). Recommended."], ["model", "A specific model", "Pin it to one provider and model."], ["local", "A model on this PC", "Free and private, through Ollama."]] as [Brain, string, string][]).map(([k, t, d]) => (
            <button key={k} className={`wiz-opt ${brain === k ? "on" : ""}`} aria-pressed={brain === k} onClick={() => setBrain(k)}><b>{t}</b><span className="small mut">{d}</span></button>))}
        </div>
        {brain === "same" && <div className="wiz-choice" style={{ marginTop: 10 }}>{wiz.tiers.filter((t) => t.id !== "custom").map((t) => (
          <button key={t.id} className={`wiz-opt ${tier === t.id ? "on" : ""}`} aria-pressed={tier === t.id} onClick={() => setTier(t.id)}><b>{t.label}</b><span className="small mut">{t.text}</span></button>))}</div>}
        {brain === "model" && (<div className="grid" style={{ marginTop: 10 }}>
          <label>Provider<select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}><option value="">Choose…</option>{usable.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          {provider && <label>Model<ModelSelect provider={provider} value={model} onChange={setModel} emptyLabel="Choose a model" /></label>}
          {usable.length === 0 && <div className="small err">No provider is switched on yet. Add one in <a href="/settings/#providers">Settings</a>.</div>}
        </div>)}
        {brain === "local" && <div style={{ marginTop: 10 }}><label>Model on this PC<ModelSelect provider="ollama" value={model} onChange={setModel} emptyLabel="Choose a downloaded model" showStatus /></label>
          <div className="small mut" style={{ marginTop: 6 }}>Agents need a model that can use tools (look for the “tools” badge). No models yet? <a href="/settings/#local">Download one</a>.</div></div>}
      </div>)}

      {step === 3 && (<div>
        <h3>What can it use? <InfoTip id="tools" /></h3>
        <div className="row small" style={{ gap: 6, marginBottom: 8 }}>Quick picks:
          <button className="chip" onClick={() => setTools(safeTools)}>Only read</button>
          <button className="chip" onClick={() => setTools(changeTools.map((t) => t.name))}>Read, change files and run commands</button>
          <button className="chip" onClick={() => setTools([])}>None</button><InfoTip id="tool-risk" /></div>
        <div className="wiz-tools">{wiz.tools.map((t) => (
          <label key={t.name} className="wiz-tool" title={t.risk_text}>
            <input type="checkbox" checked={tools.includes(t.name)} onChange={() => setTools(toggle(tools, t.name))} />
            <span><b>{t.label}</b> <span className={`pill ${RISK_TONE[t.risk]}`}>{RISK_WORD[t.risk]}</span><br /><span className="small mut">{t.plain}</span></span></label>))}</div>
        {mcp.length > 0 && <><h4>Add-ons (MCP servers) <InfoTip id="mcp" /></h4>
          <div className="row">{mcp.map((m) => <label key={m.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={mcps.includes(m.id)} onChange={() => setMcps(toggle(mcps, m.id))} />{m.label}{m.ready ? "" : " (off)"}</label>)}</div></>}
        {skills.length > 0 && <><h4>Skills <InfoTip id="skills" /></h4>
          {[...groups.entries()].map(([g, list]) => {
            const ref = `group:${g}`;
            const whole = sk.includes(ref);
            return <div key={g} className="small" style={{ marginBottom: 4 }}>
              <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={whole} onChange={() => setSk(whole ? sk.filter((x) => x !== ref) : [...sk, ref])} /><b>{g}</b> <span className="mut">whole group · {list.length}</span></label>
              <div className="row" style={{ marginLeft: 22 }}>{list.map((s) => <label key={s.name} title={s.description} style={{ flexDirection: "row", alignItems: "center", gap: 4, opacity: whole ? 0.5 : 1 }}>
                <input type="checkbox" disabled={whole} checked={whole || sk.includes(s.name)} onChange={() => setSk(toggle(sk, s.name))} />{s.name}</label>)}</div></div>;
          })}</>}
        <h4>Memory <InfoTip id="memory-types" /></h4>
        <MemoryPicker value={memory} onChange={setMemory} />
        <h4>Knowledge bases <InfoTip id="rag" /></h4>
        {kbs.length === 0 ? <div className="small mut">None yet. Create one on the <a href="/knowledge/">Knowledge</a> page to let agents answer from your documents.</div>
          : <div className="row"><label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={rag.includes("*")} onChange={() => setRag(rag.includes("*") ? [] : ["*"])} />All of them</label>
            {!rag.includes("*") && kbs.map((k) => <label key={k.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={rag.includes(k.id)} onChange={() => setRag(toggle(rag, k.id))} />{k.label}</label>)}</div>}
      </div>)}

      {step === 4 && (<div>
        <h3>What is it allowed to do?</h3>
        <ul className="small">
          <li>{tools.some((t) => ["write_file", "edit_file"].includes(t)) ? <b>It can change files in your workspace folder.</b> : "It cannot change any files."} <InfoTip id="workspace" /></li>
          <li>{tools.includes("run_shell") ? <b>It can run commands, inside the sandbox.</b> : "It cannot run commands."} <InfoTip id="sandbox" /></li>
          <li>Online spending is capped per provider in <a href="/settings/#providers">Settings</a>; this agent can never go past those limits. <InfoTip id="spend-caps" /></li>
        </ul>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={spawn} onChange={(e) => setSpawn(e.target.checked)} />Let it build its own short-lived helper agents <InfoTip id="spawn" /></label>
        {agents.length > 0 && <><h4>Helper agents it can hand work to <InfoTip id="sub-agents" /></h4>
          <div className="row">{agents.map((a) => <label key={a.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={subs.includes(a.id)} onChange={() => setSubs(toggle(subs, a.id))} />{a.name}</label>)}</div></>}
        <h4>Outside coding agents it may call <InfoTip id="acp" /></h4>
        {installedExt.length === 0 ? <div className="small mut">None installed yet. You can install some from <a href="/settings/#external">Settings → External agents</a> or the set-up guide.</div>
          : <><div className="row">{installedExt.map((x) => <label key={x.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={acp.includes(x.id)} onChange={() => setAcp(toggle(acp, x.id))} />{x.label}</label>)}</div>
            {acp.length > 0 && <label style={{ marginTop: 8 }}>When they ask permission
              <select value={policy} onChange={(e) => setPolicy(e.target.value)}><option value="ask">ask me each time</option><option value="reads_auto">allow reading, ask for the rest</option><option value="readonly">refuse anything that changes things</option></select><InfoTip id="acp-policy" /></label>}</>}
        <AgentAgui agent={{ agui }} isNew compact onChange={setAgui} />
      </div>)}

      {step === 5 && (<div>
        <h3>Instructions <InfoTip id="instructions" /></h3>
        <div className="small mut" style={{ marginBottom: 6 }}>The standing orders this agent follows. Write them yourself, or let a model draft them from what you said it should do.</div>
        <textarea rows={9} value={instr} aria-label="Instructions" onChange={(e) => setInstr(e.target.value)} placeholder="You are a careful assistant who..." />
        <div className="row" style={{ marginTop: 8 }}><button onClick={draft} disabled={drafting || (goal.trim().length < 3 && desc.trim().length < 3)}>{drafting ? "Drafting…" : "Draft it for me"}</button>
          <span className="small mut">{note || "Uses one short model call; it counts toward your spend limit."}</span></div>
      </div>)}

      {step === 6 && (<div>
        <h3>Check and create</h3>
        <dl className="wiz-sum">
          <dt>Name</dt><dd>{name} <span className="mut small">({id})</span></dd>
          <dt>Does</dt><dd>{desc || goal}</dd>
          <dt>Thinks with</dt><dd>{brain === "same" ? `your chat's model (${wiz.tiers.find((t) => t.id === tier)?.label.toLowerCase()})` : brain === "local" ? `${model} on this PC` : `${provider} · ${model}`}</dd>
          <dt>Can use</dt><dd>{tools.length ? tools.map((t) => wiz.tools.find((x) => x.name === t)?.label ?? t).join(", ") : "no tools (it can only talk)"}</dd>
          {mcps.length > 0 && <><dt>Add-ons</dt><dd>{mcps.join(", ")}</dd></>}
          {sk.length > 0 && <><dt>Skills</dt><dd>{sk.join(", ")}</dd></>}
          {(memory.types?.length ?? 0) > 0 && <><dt>Remembers</dt><dd>{memory.types?.join(", ")} (saves: {memory.auto_save ?? "review"})</dd></>}
          {rag.length > 0 && <><dt>Knowledge</dt><dd>{rag.includes("*") ? "all knowledge bases" : rag.join(", ")}</dd></>}
          {subs.length > 0 && <><dt>Helpers</dt><dd>{subs.join(", ")}</dd></>}
          {acp.length > 0 && <><dt>Outside agents</dt><dd>{acp.join(", ")}</dd></>}
          <dt>Builds helpers</dt><dd>{spawn ? "yes" : "no"}</dd>
          {agui && aguiBadge({ agui }) && <><dt>AG-UI</dt><dd>{aguiBadge({ agui })}</dd></>}
        </dl>
        {!instr.trim() && <div className="small" style={{ color: "var(--warn)" }}>No instructions yet. It will still work, but it will not know its job. Go back to add some.</div>}
      </div>)}

      {err && <div className="small err" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 16, justifyContent: "space-between" }}>
        <button disabled={step === 0} onClick={() => { setStep(step - 1); setErr(""); }}>Back</button>
        {step < STEPS.length - 1
          ? <button className="acc" disabled={!valid[step]} onClick={() => { setStep(step + 1); setErr(""); }}>Next</button>
          : <button className="acc" disabled={saving} onClick={create}>{saving ? "Creating…" : "Create agent"}</button>}
      </div>
    </div>
  );
}

/** After creating: a tiny chat to try the new agent right away. */
function TryIt({ id, name, provider, onOpen, onAnother }: { id: string; name: string; provider: string; onOpen: () => void; onAnother: () => void }) {
  const [msg, setMsg] = useState("");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true); setOut("");
    let text = "";
    try {
      await streamSse("/api/chat/stream", { provider, agent: id, message: msg }, (e) => {
        if (e.type === "text") { text += e.delta; setOut(text); }
        else if (e.type === "done") setOut(e.reply || text || "(no answer)");
        else if (e.type === "error") setOut("⚠ " + (e.message || e.title || "something went wrong"));
        else if (e.type === "refused") setOut("⚠ " + (e.detail?.message ?? "refused"));
      });
    } catch (e) { setOut("⚠ " + (e as Error).message); }
    setBusy(false);
  }
  return (
    <div className="card wiz">
      <b>{name} is ready</b>
      <p className="small mut">Try it with a short message. This sends a real request, so it counts toward your spend limit if you use an online model.</p>
      <div className="row"><input value={msg} aria-label="Try the agent" placeholder="Say hello, or give it a small task" style={{ flex: 1, minWidth: 200 }} onChange={(e) => setMsg(e.target.value)} />
        <button className="acc" disabled={busy || !msg.trim() || !provider} onClick={send}>{busy ? "Working…" : "Try it"}</button></div>
      {!provider && <div className="small err">Switch on a model provider first (Settings) to try it.</div>}
      {out && <div className="wiz-out">{out}</div>}
      <div className="row" style={{ marginTop: 14 }}><button onClick={onOpen}>See it in Agent Studio</button><button className="ghost" onClick={onAnother}>Create another</button></div>
    </div>
  );
}
