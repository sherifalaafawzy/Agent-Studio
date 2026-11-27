"use client";
// The app's manifest as a form: its name, which of your agents it calls (and only those), what it may reach, its environment variables (names here, values write-only) and its own API key.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AgentLite, AppRow, EnvRow, KeyInfo, Manifest } from "@/lib/apps";
import InfoTip from "@/components/InfoTip";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";

export default function SettingsPanel({ app, onChanged }: { app: AppRow; onChanged: () => void }) {
  const [m, setM] = useState<Manifest | null>(null);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [env, setEnv] = useState<EnvRow[]>([]);
  const [key, setKey] = useState<KeyInfo | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [allow, setAllow] = useState("");
  const [err, setErr] = useState("");
  const [newKey, setNewKey] = useState("");
  const isStatic = app.type === "static";

  const load = useCallback(async () => {
    try {
      const [man, ag, e, k] = await Promise.all([api<Manifest>(`/api/apps/${app.id}/manifest`), api<AgentLite[]>("/api/agents"), api<EnvRow[]>(`/api/apps/${app.id}/env`), api<KeyInfo>(`/api/apps/${app.id}/key`)]);
      setM(man); setAgents(ag.filter((a) => a.id !== "app_builder")); setEnv(e); setKey(k); setAllow(man.permissions.allow.join("\n")); setErr("");
    } catch (x) { setErr((x as Error).message); }
  }, [app.id]);
  useEffect(() => { load(); }, [load]);
  if (!m) return <div className="mut small">{err ? <span role="alert" className="err">{err}</span> : "Loading settings…"}</div>;

  const toggleAgent = (id: string, on: boolean) => setM({ ...m, agents: on ? [...m.agents, { id, purpose: "" }] : m.agents.filter((a) => a.id !== id) });
  const purpose = (id: string, p: string) => setM({ ...m, agents: m.agents.map((a) => (a.id === id ? { ...a, purpose: p } : a)) });
  const save = async () => {
    const body: Partial<Manifest> = { name: m.name, description: m.description, version: m.version, agents: m.agents, env: m.env,
      permissions: { network: m.permissions.network, allow: allow.split("\n").map((s) => s.trim()).filter(Boolean) }, ...(isStatic ? {} : { port: Number(m.port), start: m.start }) };
    try { await api(`/api/apps/${app.id}/manifest`, { method: "PUT", body: JSON.stringify(body) }); toast("App settings saved"); onChanged(); await load(); }
    catch (x) { setErr((x as Error).message); }
  };
  const setValue = async (name: string, clear = false) => {
    try { setEnv(await api<EnvRow[]>(`/api/apps/${app.id}/env`, { method: "PUT", body: JSON.stringify({ [name]: clear ? "" : values[name] ?? "" }) })); setValues({ ...values, [name]: "" }); setErr(""); }
    catch (x) { setErr((x as Error).message); }
  };
  const keyAct = async (what: "rotate" | "revoke") => {
    try { const r = await api<any>(`/api/apps/${app.id}/key/${what}`, { method: "POST" }); if (what === "rotate") setNewKey(r.key); setKey(await api<KeyInfo>(`/api/apps/${app.id}/key`)); setErr(""); }
    catch (x) { setErr((x as Error).message); }
  };
  const enable = async (id: string) => { try { await api(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify({ api_access: true }) }); await load(); } catch (x) { setErr((x as Error).message); } };

  return (
    <div data-testid="settings-panel">
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="grid">
        <label>Name<input value={m.name} aria-label="App name" onChange={(e) => setM({ ...m, name: e.target.value })} /></label>
        <label>Version<input value={m.version} aria-label="App version" onChange={(e) => setM({ ...m, version: e.target.value })} /></label>
      </div>
      <label style={{ marginTop: 6 }}>What it does<input value={m.description} aria-label="App description" onChange={(e) => setM({ ...m, description: e.target.value })} /></label>
      {!isStatic && (
        <div className="grid" style={{ marginTop: 6 }}>
          <label>Port inside the container<input type="number" value={m.port} aria-label="App port" onChange={(e) => setM({ ...m, port: Number(e.target.value) })} /></label>
          <label>Start command<input value={m.start} aria-label="Start command" onChange={(e) => setM({ ...m, start: e.target.value })} /></label>
        </div>
      )}

      <h4 style={{ margin: "14px 0 4px" }}>Agents it calls <InfoTip id="app-key" /></h4>
      <div className="small mut">The app can call only the agents ticked here. An agent must also have API access switched on.</div>
      {agents.map((a) => {
        const on = m.agents.find((x) => x.id === a.id);
        return (
          <div key={a.id} className="row" style={{ gap: 8, padding: "3px 0", flexWrap: "wrap" }}>
            <label className="check"><input type="checkbox" checked={!!on} onChange={(e) => toggleAgent(a.id, e.target.checked)} aria-label={`Use agent ${a.name}`} /> {a.name}</label>
            {on && <input className="grow" value={on.purpose} aria-label={`What ${a.name} does for this app`} placeholder="What it does for this app" onChange={(e) => purpose(a.id, e.target.value)} />}
            {on && !a.api_access && <button className="chip small" onClick={() => enable(a.id)}>Turn on API access</button>}
          </div>
        );
      })}

      <h4 style={{ margin: "14px 0 4px" }}>What it may reach</h4>
      <select value={m.permissions.network} aria-label="Network access" onChange={(e) => setM({ ...m, permissions: { ...m.permissions, network: e.target.value as Manifest["permissions"]["network"] } })}>
        <option value="none">Nothing but Agent Studio</option><option value="allowlist">Only the addresses listed below</option><option value="open">Any secure (https) address</option>
      </select>
      {m.permissions.network === "allowlist" && <label style={{ marginTop: 4 }}>Allowed addresses, one per line (https://api.example.com)
        <textarea rows={3} value={allow} aria-label="Allowed addresses" onChange={(e) => setAllow(e.target.value)} /></label>}
      {m.permissions.network === "open" && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>The page can then send data to any https address. Only choose this if you trust what the app does.</div>}

      <h4 style={{ margin: "14px 0 4px" }}>Environment variables</h4>
      <div className="small mut">The app lists the names it needs; you enter the values here. Values are never shown again and never written into a file.</div>
      {m.env.map((v, i) => (
        <div key={i} className="row" style={{ gap: 6, margin: "4px 0", flexWrap: "wrap" }}>
          <input value={v.name} aria-label={`Variable ${i + 1} name`} style={{ width: 150 }} onChange={(e) => setM({ ...m, env: m.env.map((x, j) => (j === i ? { ...x, name: e.target.value.toUpperCase() } : x)) })} />
          <input className="grow" value={v.description} aria-label={`Variable ${i + 1} description`} placeholder="What it is" onChange={(e) => setM({ ...m, env: m.env.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)) })} />
          <button aria-label={`Remove variable ${v.name || i + 1}`} onClick={() => setM({ ...m, env: m.env.filter((_, j) => j !== i) })}>Remove</button>
          {env.find((e) => e.name === v.name) && (
            <>
              <input type="password" autoComplete="off" value={values[v.name] ?? ""} aria-label={`Value of ${v.name}`} placeholder={env.find((e) => e.name === v.name)!.set ? "set (hidden)" : "not set"} onChange={(e) => setValues({ ...values, [v.name]: e.target.value })} />
              <button disabled={!values[v.name]} onClick={() => setValue(v.name)} aria-label={`Save value of ${v.name}`}>Save value</button>
              {env.find((e) => e.name === v.name)!.set && <button onClick={() => setValue(v.name, true)} aria-label={`Clear value of ${v.name}`}>Clear</button>}
            </>
          )}
        </div>
      ))}
      <button className="chip small" onClick={() => setM({ ...m, env: [...m.env, { name: "", description: "", required: false }] })}>Add a variable</button>
      <div className="small mut">New variables get a value box after you save the settings.</div>

      <div className="row" style={{ marginTop: 12 }}><button className="pri" onClick={save}>Save app settings</button></div>

      <h4 style={{ margin: "18px 0 4px" }}>The app&apos;s own key</h4>
      {key && (key.exists
        ? <div className="small">Key <code>{key.prefix}…</code> for {key.agents.join(", ")} · {key.uses ?? 0} calls{key.revoked ? " · revoked" : ""}
            <div className="row" style={{ gap: 6, marginTop: 4 }}><button onClick={() => keyAct("rotate")}>Rotate key</button><button disabled={key.revoked} onClick={() => keyAct("revoke")}>Revoke key</button></div></div>
        : <div className="small mut">{key.note}
            {key.agents.length > 0 && <div style={{ marginTop: 4 }}><button onClick={() => keyAct("rotate")}>Make a key to use outside Agent Studio</button></div>}</div>)}
      {newKey && (
        <Modal title="Your new key" onClose={() => setNewKey("")}>
          <p className="small">Copy it now: it is shown only once. Use it as AGENT_STUDIO_API_KEY where the app runs. It can call only this app&apos;s agents.</p>
          <input readOnly value={newKey} aria-label="New app key" onFocus={(e) => e.target.select()} />
          <div className="row" style={{ marginTop: 10 }}><button onClick={() => navigator.clipboard?.writeText(newKey)}>Copy</button><button className="acc" onClick={() => setNewKey("")}>Done</button></div>
        </Modal>
      )}
    </div>
  );
}
