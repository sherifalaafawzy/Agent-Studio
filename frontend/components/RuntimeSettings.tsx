"use client";
// Settings > Runtimes: where agents can be deployed (Docker on this PC, Amazon Bedrock AgentCore, Vertex AI Agent Engine, Microsoft Foundry),
// their settings (credentials are write-only), the image address template and the model keys a deployed agent runs with.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import BrandIcon from "./BrandIcon";

type Field = { name: string; label: string; secret: boolean; required: boolean; help: string; default: string };
export type Target = { id: string; label: string; docs: string; remote: boolean; cost_note: string; platform: string; side_effects: string[]; fields: Field[];
  enabled: boolean; values: Record<string, string>; secrets_set: string[]; configured: boolean; problem: string };
type Overview = { targets: Target[]; image_template: string; model_env_set: string[] };

function TargetCard({ t, onSave }: { t: Target; onSave: (id: string, body: Record<string, unknown>) => Promise<boolean> }) {
  const [vals, setVals] = useState<Record<string, string>>(t.values);
  const [secs, setSecs] = useState<Record<string, string>>({});
  const [test, setTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const save = async (enabled: boolean) => {
    const ok = await onSave(t.id, { targets: { [t.id]: { ...vals, enabled } }, ...(Object.values(secs).some(Boolean) ? { secrets: { [t.id]: Object.fromEntries(Object.entries(secs).filter(([, v]) => v)) } } : {}) });
    if (ok) setSecs({});
  };
  const runTest = async () => { try { setTest(await api(`/api/runtimes/targets/${t.id}/test`, { method: "POST" })); } catch (e) { setTest({ ok: false, detail: (e as Error).message }); } };
  return (
    <div className="card" data-testid={`runtime-${t.id}`}>
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <BrandIcon id={t.id} label={t.label} size={30} /><b>{t.label}</b><span className={`pill ${t.enabled ? "ok" : ""}`}>{t.enabled ? "on" : "off"}</span>
        <span className="pill">image: {t.platform}</span><span className="grow" />
        <label className="switch"><input type="checkbox" aria-label={`${t.id} on`} checked={t.enabled} onChange={(e) => save(e.target.checked)} /><i /></label>
      </div>
      <div className="small mut" style={{ marginTop: 4 }}>{t.cost_note}</div>
      {t.side_effects.length > 0 && <ul className="small" style={{ margin: "6px 0", paddingLeft: 18, color: "var(--warn-text)" }}>{t.side_effects.map((s) => <li key={s}>{s}</li>)}</ul>}
      <div className="grid" style={{ marginTop: 6 }}>
        {t.fields.map((f) => (
          <label key={f.name}>{f.label}{!f.required && " (optional)"}
            {f.secret
              ? <input type="password" autoComplete="off" value={secs[f.name] ?? ""} placeholder={t.secrets_set.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"} onChange={(e) => setSecs({ ...secs, [f.name]: e.target.value })} />
              : <input value={vals[f.name] ?? ""} placeholder={f.default || f.help} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />}
            {f.help && <span className="small mut">{f.help}</span>}
          </label>))}
      </div>
      {!t.configured && t.enabled && <div className="small" role="alert" style={{ color: "var(--warn-text)", marginTop: 6 }}>{t.problem}</div>}
      <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
        <button className="pri small" onClick={() => save(t.enabled)}>Save</button>
        <button className="small" onClick={runTest}>Test</button>
        {t.docs && <a className="small" href={t.docs} target="_blank" rel="noreferrer">Documentation</a>}
        {test && <span className={`pill ${test.ok ? "ok" : "bad"}`} role="status">{test.detail}</span>}
      </div>
    </div>
  );
}

export default function RuntimeSettings() {
  const [o, setO] = useState<Overview | null>(null);
  const [err, setErr] = useState("");
  const [tpl, setTpl] = useState("");
  const [keyName, setKeyName] = useState("");
  const [keyVal, setKeyVal] = useState("");
  const load = useCallback(() => api<Overview>("/api/runtimes").then((x) => { setO(x); setTpl(x.image_template); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const save = async (_id: string, body: Record<string, unknown>) => {
    try { setO(await api<Overview>("/api/runtimes/config", { method: "PUT", body: JSON.stringify(body) })); setErr(""); toast("Saved"); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!o) return err ? <p className="err" role="alert">{err}</p> : null;
  return (
    <>
      <p className="page-sub">Where an agent runs. <b>This PC</b> is the default. You can also deploy an agent to a container on this PC or to a cloud service. A deployed agent only chats: tools, memory and files stay here.</p>
      <div className="card">
        <div className="card-head"><b>Deploying agents</b><InfoTip id="runtimes" /></div>
        {err && <p className="err" role="alert">{err}</p>}
        <p className="small mut">Pick the runtime in an agent&apos;s editor. Deploying always shows a plan first and asks you to confirm, because it creates cloud resources that can cost money.</p>
        <div className="grid">
          <label>Image address template
            <input value={tpl} placeholder="123456789012.dkr.ecr.us-east-1.amazonaws.com/agents:{agent}-{tag}" onChange={(e) => setTpl(e.target.value)} />
            <span className="small mut">Where you push each agent&apos;s image. <code>{"{agent}"}</code> becomes the agent id and <code>{"{tag}"}</code> a fingerprint of its definition.</span></label>
        </div>
        <div className="row" style={{ marginTop: 8 }}><button className="small" disabled={tpl === o.image_template} onClick={() => save("", { image_template: tpl })}>Save address</button></div>
        <div className="lbl" style={{ marginTop: 14 }}>Model keys for deployed agents</div>
        <div className="small mut">A deployed agent calls its model with these keys, so they are stored in the cloud runtime&apos;s configuration there. Use a key with a spending limit. They are never shown again.</div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "6px 0" }}>
          {o.model_env_set.map((n) => <span key={n} className="chip small">{n} <button className="ghost small" aria-label={`Remove ${n}`} onClick={() => save("", { clear_model_env: [n] })}>×</button></span>)}
          {o.model_env_set.length === 0 && <span className="small mut">None set.</span>}
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          <input aria-label="Key name" placeholder="OPENAI_API_KEY" value={keyName} onChange={(e) => setKeyName(e.target.value.toUpperCase())} style={{ width: 200 }} />
          <input aria-label="Key value" type="password" autoComplete="off" placeholder="paste value" value={keyVal} onChange={(e) => setKeyVal(e.target.value)} style={{ width: 240 }} />
          <button className="small" disabled={!keyName || !keyVal} onClick={async () => { if (await save("", { secrets: { model_env: { [keyName]: keyVal } } })) { setKeyName(""); setKeyVal(""); } }}>Add key</button>
        </div>
      </div>
      {o.targets.filter((t) => t.id !== "local").map((t) => <TargetCard key={t.id} t={t} onSave={save} />)}
    </>
  );
}
