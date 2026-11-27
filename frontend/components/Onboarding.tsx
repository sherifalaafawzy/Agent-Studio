"use client";
// The first-run set-up guide: from a fresh install to a working agent. Skippable, resumable, and reopenable from the help button.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { markFirstRunDone } from "@/lib/tour";
import AgentInstaller from "./AgentInstaller";
import FolderPicker from "./FolderPicker";
import InfoTip from "./InfoTip";
import { toast } from "./Nav";
import OllamaLibrary from "./OllamaLibrary";
import OllamaSetup from "./OllamaSetup";
import { startTour } from "./TourRunner";

type Rec = { status: string; step: string; choice: string; completed: string[]; skipped: string[] };
type Check = { id: string; label: string; state: "done" | "todo" | "optional"; detail: string };
type OB = { record: Rec; steps: string[]; checklist: Check[]; show: boolean; resume: boolean };
type Sys = { items: { id: string; label: string; state: string; detail: string; why: string }[] };
type Prov = { id: string; label: string; kind: string; enabled: boolean; auth_ready?: boolean; auth_detail?: string };

const INFO: Record<string, { title: string; why: string; cost: string }> = {
  welcome: { title: "Welcome to Agent Studio", why: "Agent Studio lets AI agents work on your behalf: answer questions, read your files, write code and use other tools, while you stay in control. This guide gets you set up in a few minutes.", cost: "Nothing here costs money until you add an online provider." },
  power: { title: "How should your agents think?", why: "Every agent needs an AI model. You can run one on this PC (private, free, but needs a reasonably modern computer) or use an online provider (very capable, billed by use), or have both.", cost: "Local: free. Online: pay per use, with a spend limit you control." },
  ollama: { title: "Run models on this PC (Ollama)", why: "Ollama is the free program that runs AI models locally. If it is missing, one click installs it for your user account.", cost: "Free. The installer is about 2 GB." },
  model: { title: "Choose a local model", why: "A model is the AI's brain. Bigger ones are smarter but need more memory. The list shows which ones suit your PC; models with the “tools” badge work best for agents.", cost: "Free. Each model is a 1 to 20 GB download, stored on this PC." },
  key: { title: "Connect an online provider", why: "To use Claude, GPT, Gemini and others you need a key from their website. Paste it in Settings (never in a chat) and it stays on this PC.", cost: "Billed by the provider. Agent Studio enforces the spend limit you set." },
  workspace: { title: "Choose your work folder", why: "Agents can only read and change files inside one folder. Point it at the project you want help with. Everything outside stays private.", cost: "Free." },
  agents: { title: "Install coding agents (optional)", why: "Tools like Claude Code and Codex are expert coders you may already use. Install them and Agent Studio can hand them big jobs.", cost: "They use their own subscription or key; Agent Studio's spend limits do not cover them." },
  assistant: { title: "Meet the assistant", why: "The assistant in the corner explains screens, opens pages and points at things while you learn. It uses the model you set up.", cost: "A little model use; local models are free." },
  first: { title: "Start your first chat or agent", why: "Now try it out. A chat is the quickest way; the agent wizard builds a reusable helper step by step.", cost: "Depends on the model you picked." },
  done: { title: "You are set up", why: "Here is where things stand. Anything marked to-do can be finished later from the help button at the bottom of the sidebar.", cost: "" },
};
const SHORT: Record<string, string> = { welcome: "Welcome", power: "Power", ollama: "Ollama", model: "Model", key: "Online key", workspace: "Folder", agents: "Coding agents", assistant: "Assistant", first: "First chat", done: "Done" };
const POWER = [["local", "On this PC", "Private and free. Needs a decent computer."], ["online", "Online provider", "Most capable. You pay per use, with a limit."], ["both", "Both", "Use local models for easy work, online for hard work."]] as const;
const TONE: Record<string, string> = { ok: "ok", warn: "warn", missing: "", done: "ok", todo: "warn", optional: "" };
const WORD: Record<string, string> = { done: "done", todo: "to do", optional: "optional" };

export default function Onboarding() {
  const router = useRouter();
  const [ob, setOb] = useState<OB | null>(null);
  const [sys, setSys] = useState<Sys | null>(null);
  const [provs, setProvs] = useState<Prov[]>([]);
  const [ws, setWs] = useState("");
  const [wsSaved, setWsSaved] = useState("");
  const [bot, setBot] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { setOb(await api<OB>("/api/easy/onboarding")); } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => {
    void load();
    api<Sys>("/api/easy/system").then(setSys).catch(() => {});
    api<Prov[]>("/api/providers").then(setProvs).catch(() => {});
    api<{ workspace_dir: string; sidebot_enabled: boolean }>("/api/settings").then((s) => { setWs(s.workspace_dir); setWsSaved(s.workspace_dir); setBot(s.sidebot_enabled); }).catch(() => {});
  }, [load]);

  const refresh = useCallback(() => { void load(); }, [load]);          // stable, so the library does not reload itself on every render
  const save = async (patch: Record<string, unknown>) => {
    try { const rec = await api<Rec>("/api/easy/onboarding", { method: "PUT", body: JSON.stringify(patch) }); setOb((o) => ({ ...(o as OB), record: rec })); return rec; }
    catch (e) { setErr((e as Error).message); return null; }
  };

  if (!ob) return <div className="page-sub">{err || "Loading the set-up guide…"}</div>;
  const steps = ob.steps;
  const idx = Math.max(0, steps.indexOf(ob.record.step));
  const id = steps[idx];
  const info = INFO[id];
  const check = ob.checklist.find((c) => c.id === id);
  const go = async (to: number, how: "done" | "skipped" | "back") => {
    setErr("");
    await save({ step: steps[to], ...(how === "done" ? { completed: [id] } : how === "skipped" ? { skipped: [id] } : {}) });
    void load();
  };
  const finish = async () => { await save({ status: "done", completed: ["done"] }); markFirstRunDone(); router.push("/"); };
  const tour = async () => { await save({ status: "done", completed: ["done"] }); markFirstRunDone(); router.push("/"); setTimeout(() => startTour("welcome"), 400); };
  const skipAll = async () => { await save({ status: "skipped" }); markFirstRunDone(); router.push("/"); };
  const choose = (c: string) => { void save({ choice: c }); };
  const saveWs = async () => { try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ workspace_dir: ws }) }); setWsSaved(ws); toast("Work folder saved"); void load(); } catch (e) { setErr((e as Error).message); } };
  const toggleBot = async () => { try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ sidebot_enabled: !bot }) }); setBot(!bot); void load(); } catch (e) { setErr((e as Error).message); } };
  const choice = ob.record.choice;
  const optionalHere = (id === "ollama" || id === "model") ? choice === "online" : id === "key" ? choice === "local" : false;
  const cloud = provs.filter((p) => p.kind !== "ollama" && p.kind !== "mock");

  return (
    <div className="setup">
      <div className="page-head"><h2>Set-up guide</h2><InfoTip id="onboarding" />
        <span className="grow" /><button className="ghost small" onClick={skipAll}>Skip the guide</button></div>
      <div className="setup-prog" aria-label={`Step ${idx + 1} of ${steps.length}`}>
        <div className="bar"><i style={{ width: `${((idx + 1) / steps.length) * 100}%` }} /></div>
        <div className="small mut">Step {idx + 1} of {steps.length}</div>
      </div>
      <div className="setup-chips">{steps.map((s, i) => {
        const c = ob.checklist.find((x) => x.id === s);
        return <button key={s} className={`chip ${i === idx ? "on" : ""}`} aria-current={i === idx ? "step" : undefined} onClick={() => go(i, "back")}>{c?.state === "done" && s !== "done" ? "✓ " : ""}{SHORT[s]}</button>;
      })}</div>

      <div className="card setup-card">
        <div className="row"><h3 style={{ margin: 0 }}>{info.title}</h3>{check && id !== "welcome" && id !== "done" && <span className={`pill ${TONE[check.state]}`}>{WORD[check.state]}</span>}</div>
        <p className="small" style={{ lineHeight: 1.6 }}>{info.why}</p>
        {info.cost && <p className="small mut"><b>Cost:</b> {info.cost}</p>}
        {optionalHere && <p className="small" style={{ color: "var(--warn-text)" }}>You chose {choice === "online" ? "online providers" : "local models"}, so this step is optional.</p>}
        {check?.detail && id !== "welcome" && id !== "done" && <p className="small mut">Right now: {check.detail}</p>}

        {id === "welcome" && (<div>
          <b className="small">Your PC</b>
          {!sys ? <div className="small mut">Checking…</div> : <ul className="setup-sys">{sys.items.map((i) => (
            <li key={i.id}><span className={`pill ${TONE[i.state]}`}>{i.state === "ok" ? "ready" : i.state === "warn" ? "check" : "not found"}</span> <b>{i.label}</b> <span className="small mut">{i.detail}</span>
              <div className="small mut">{i.why}</div></li>))}</ul>}
          <button className="ghost small" onClick={() => api<Sys>("/api/easy/system").then(setSys).catch(() => {})}>Check again</button>
        </div>)}

        {id === "power" && <div className="wiz-choice">{POWER.map(([k, t, d]) => (
          <button key={k} className={`wiz-opt ${choice === k ? "on" : ""}`} aria-pressed={choice === k} onClick={() => choose(k)}><b>{t}</b><span className="small mut">{d}</span></button>))}</div>}

        {id === "ollama" && <OllamaSetup onChange={() => void load()} />}
        {id === "model" && <OllamaLibrary providerReady onInstalledChange={refresh} />}

        {id === "key" && (<div>
          {cloud.map((p) => <div key={p.id} className="row" style={{ padding: "4px 0" }}><b>{p.label}</b>
            <span className={`pill ${p.enabled && p.auth_ready ? "ok" : ""}`}>{p.enabled && p.auth_ready ? "ready" : p.enabled ? "needs a key" : "off"}</span></div>)}
          <p className="small mut">Open Settings, pick a provider, paste its key into the box and switch it on. Never paste keys into the chat. <InfoTip id="api-key" /></p>
          <Link href="/settings/#providers" className="chip">Open Settings → Model providers</Link>
        </div>)}

        {id === "workspace" && (<div>
          <FolderPicker value={ws} onChange={setWs} placeholder="e.g. H:/my-project" />
          <button className="pri small" disabled={!ws || ws === wsSaved} onClick={saveWs} style={{ marginTop: 8 }}>Save folder</button>
          <div className="small mut" style={{ marginTop: 6 }}>Agents can only touch files in this folder. <InfoTip id="workspace" /></div>
        </div>)}

        {id === "agents" && <AgentInstaller only={["claude", "codex", "gemini", "copilot", "cursor", "grok"]} onChange={() => void load()} />}

        {id === "assistant" && (<div className="row">
          <button className={bot ? "" : "acc"} onClick={toggleBot}>{bot ? "Switch the assistant off" : "Switch the assistant on"}</button>
          <span className={`pill ${bot ? "ok" : ""}`}>{bot ? "on" : "off"}</span><InfoTip id="assistant" />
          <Link href="/settings/#assistant" className="small">Choose its model and rules</Link></div>)}

        {id === "first" && (<div className="row">
          <button className="acc" onClick={() => { void save({ completed: ["first"], step: "done" }); router.push("/"); }}>Start a chat</button>
          <button onClick={() => { void save({ completed: ["first"], step: "done" }); router.push("/agents/?wizard=1"); }}>Create an agent</button></div>)}

        {id === "done" && (<div>
          <p className="small">Next, <Link href="/settings/#security">switch on guardrails</Link> and check your <Link href="/security/">security scorecard</Link>: it shows which safety protections are on and what to do about the rest.</p>
          <ul className="setup-sys">{ob.checklist.filter((c) => c.id !== "welcome" && c.id !== "done").map((c) => (
            <li key={c.id}><span className={`pill ${TONE[c.state]}`}>{WORD[c.state]}</span> <b>{c.label}</b> <span className="small mut">{c.detail}</span></li>))}</ul>
          <p className="small mut" style={{ marginTop: 10 }}>Next, look in the <Link href="/catalog/">Catalog</Link> for ready-made skills and add-ons for accounting, sales, marketing, engineering, data and machine learning. Everything there is off until you add it. Files your agents produce are kept on the <Link href="/artifacts/">Artifacts</Link> page, where you can also choose cloud storage for copies. To make a small web app that uses your agents, open <Link href="/apps/">Apps</Link>. To write or edit something together with an agent, use the <b>Canvas</b> button above a chat. Later, <Link href="/workflows/">Workflows</Link> lets you draw a process of agents and tools (or have an agent draft it), and <Link href="/settings/#access">Settings &gt; API &amp; A2A</Link> lets other programs call your agents. An agent can also run in a container or a cloud service (<Link href="/settings/#runtimes">Settings &gt; Runtimes</Link>), and may read your databases and Salesforce, read-only (<Link href="/settings/#data">Settings &gt; Data sources</Link>).</p>
          <div className="row" style={{ marginTop: 12 }}><button className="acc" onClick={tour}>Take a quick tour</button><button onClick={finish}>Go to Agent Studio</button></div>
        </div>)}
      </div>

      {err && <div className="small err">{err}</div>}
      {id !== "done" && (
        <div className="row" style={{ justifyContent: "space-between" }}>
          <button disabled={idx === 0} onClick={() => go(idx - 1, "back")}>Back</button>
          <span className="row">
            {id !== "welcome" && id !== "power" && <button className="ghost" onClick={() => go(idx + 1, "skipped")}>Skip this step</button>}
            <button className="acc" disabled={id === "power" && !choice} onClick={() => go(idx + 1, "done")}>{id === "first" ? "Finish" : "Next"}</button>
          </span>
        </div>)}
    </div>
  );
}
