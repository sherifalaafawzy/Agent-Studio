"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import { toast } from "@/components/Nav";
import type { Level } from "@/components/DiscoverySettings";
import DataProducts from "@/components/DataProducts";

// Discovery & AI-BOM: preview, validate and download the AI bill of materials (CycloneDX 1.7) and the ARD catalog that points to it,
// and (only on request) push the BOM to a Dependency-Track server. Previews never publish anything.

type Prop = { name: string; value: string };
type Item = { "bom-ref": string; name: string; type?: string; version?: string; group?: string; properties?: Prop[]; licenses?: { license?: { id?: string; name?: string }; expression?: string }[] };
type Bom = { specVersion: string; serialNumber: string; metadata: { timestamp: string; properties?: Prop[] }; components: Item[]; services?: Item[]; dependencies: { ref: string }[] };
type Check = { ok: boolean; schema?: string; error_count: number; errors: { path: string; message: string }[]; warnings?: { path: string; message: string }[] };
type Scopes = Record<string, { ref: string; name: string }[]>;
type Manifest = {
  manifest: { entries: unknown[] } | null; entries: { identifier: string; type: string; displayName: string; url: string; inline: boolean; digests: string[] }[]; reasons: string[];
  publishable: boolean; validation: { ok: boolean; manifest: Check; boms: Record<string, Check> } | null; level?: string; publisher?: string; base_url?: string; signed?: boolean;
  problems?: string[]; files?: { path: string; kind: string; bytes: number }[];
};
type PushPlan = { method: string; url: string; project: { name: string; version: string }; redaction_level: string; confirm: string;
  bom: { specVersion: string; bytes: number; sha256: string; components: number; services: number }; note: string };

const msg = (e: unknown) => (e as Error).message;
const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const prop = (it: Item, name: string) => it.properties?.find((p) => p.name === `agentstudio:${name}`)?.value;
const lic = (it: Item) => it.licenses?.map((l) => l.expression ?? l.license?.id ?? l.license?.name).filter(Boolean).join(", ") || "unknown";

function Problems({ c, label }: { c: Check; label: string }) {
  return (
    <div style={{ marginTop: 8 }} aria-label={label}>
      <span className={`pill ${c.ok ? "ok" : "bad"}`}>{c.ok ? "valid" : `${c.error_count} error${c.error_count === 1 ? "" : "s"}`}</span>
      {c.schema && <span className="small mut"> {c.schema}</span>}
      {c.errors.map((e, i) => <div key={i} className="small err dc-wrap">{e.path}: {e.message}</div>)}
      {(c.warnings ?? []).map((w, i) => <div key={i} className="small dc-wrap" style={{ color: "var(--warn-text)" }}>warning · {w.path}: {w.message}</div>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ AI-BOM
function BomPanel({ levels }: { levels: Level[] }) {
  const [level, setLevel] = useState("public");
  const [scope, setScope] = useState("system");
  const [scopes, setScopes] = useState<Scopes>({});
  const [bom, setBom] = useState<Bom | null>(null);
  const [sha, setSha] = useState("");
  const [raw, setRaw] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const q = `level=${level}&scope=${encodeURIComponent(scope)}`;

  useEffect(() => { api<Scopes>(`/api/aibom/scopes?level=${level}`).then(setScopes).catch((e) => setErr(msg(e))); }, [level]);

  async function preview() {
    setBusy(true); setErr(""); setCheck(null); setRaw(false);
    try {
      const res = await fetch(`${API_BASE}/api/aibom?${q}`);
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`);
      setSha(res.headers.get("X-AIBOM-SHA256") ?? "");
      setBom(await res.json());
    } catch (e) { setErr(msg(e)); setBom(null); } finally { setBusy(false); }
  }
  async function validate() {
    setErr("");
    try { setCheck(await api<Check>(`/api/aibom/validate?${q}`)); } catch (e) { setErr(msg(e)); }
  }
  const byType = (bom?.components ?? []).reduce<Record<string, number>>((m, c) => ({ ...m, [c.type ?? "?"]: (m[c.type ?? "?"] ?? 0) + 1 }), {});
  const models = (bom?.components ?? []).filter((c) => c.type === "machine-learning-model");
  const limits = bom?.metadata.properties?.find((p) => p.name === "agentstudio:limitations")?.value;
  const text = bom ? JSON.stringify(bom, null, 2) : "";

  return (
    <div className="card" data-tour="aibom-panel">
      <div className="card-head"><b>AI bill of materials</b><span className="pill">CycloneDX 1.7</span></div>
      <p className="card-desc">Everything the system is made of: libraries with versions and licences, models, agents and their graph, MCP servers, skills, knowledge bases (hashes only) and local models with pinned revisions and file hashes.</p>
      <div className="grid">
        <label>Redaction level
          <select aria-label="Level" value={level} onChange={(e) => { setLevel(e.target.value); setScope("system"); setBom(null); setCheck(null); }}>
            {levels.map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}</select></label>
        <label>Scope
          <select aria-label="Scope" value={scope} onChange={(e) => { setScope(e.target.value); setBom(null); setCheck(null); }}>
            <option value="system">The whole system</option>
            {Object.entries(scopes).filter(([, list]) => list.length).map(([group, list]) => (
              <optgroup key={group} label={group}>{list.map((s) => <option key={s.ref} value={s.ref}>{s.name}</option>)}</optgroup>))}
          </select></label>
      </div>
      <div className="small mut" style={{ marginTop: 6 }}>{levels.find((l) => l.id === level)?.description}</div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="acc" onClick={preview} disabled={busy}>Preview</button>
        <button onClick={validate} disabled={busy}>Validate</button>
        <a className="chip" href={`${API_BASE}/api/aibom/download?${q}`} download>Download JSON</a>
      </div>
      {err && <div className="dc-banner bad" role="alert" style={{ marginTop: 8 }}>{err}</div>}
      {check && <Problems c={check} label="BOM validation" />}
      {bom && (<>
        <div className="dc-stats" aria-label="BOM summary">
          <span><b>{bom.components.length}</b> components</span><span><b>{bom.services?.length ?? 0}</b> services</span>
          <span><b>{bom.dependencies.length}</b> graph nodes</span><span className="dc-wrap">serial <code>{bom.serialNumber.slice(9, 21)}…</code></span>
          <span className="dc-wrap">sha-256 <code>{sha.slice(0, 12)}…</code></span>
        </div>
        <div className="small mut" style={{ marginTop: 4 }}>{Object.entries(byType).map(([t, n]) => `${n} ${t}`).join(" · ")}</div>
        <h4 className="kn-h">Services ({bom.services?.length ?? 0})</h4>
        {(bom.services ?? []).slice(0, 40).map((s) => (
          <div key={s["bom-ref"]} className="kn-row"><span className="kn-grow"><b>{s.name}</b> <span className="mut small">{s["bom-ref"]}</span></span>
            {prop(s, "tier") && <span className="pill">{prop(s, "tier")}</span>}{prop(s, "enabled") === "false" && <span className="pill">disabled</span>}</div>))}
        <h4 className="kn-h">Models ({models.length})</h4>
        {models.length === 0 && <div className="small mut">No models are configured yet.</div>}
        {models.slice(0, 40).map((m) => (
          <div key={m["bom-ref"]} className="kn-row"><span className="kn-grow"><b>{m.name}</b> <span className="mut small">{m.group} · {prop(m, "hosted") === "false" || prop(m, "runs-locally") ? "runs locally" : "hosted"}</span></span>
            <span className="small mut">{lic(m)}</span></div>))}
        {limits && <details style={{ marginTop: 10 }}><summary className="small">What this BOM cannot tell you</summary>
          <ul className="small mut dc-list">{(JSON.parse(limits) as string[]).map((l) => <li key={l}>{l}</li>)}</ul></details>}
        <div style={{ marginTop: 10 }}><button className="chip small" onClick={() => setRaw((r) => !r)}>{raw ? "Hide JSON" : "Show JSON"}</button></div>
        {raw && <pre className="dc-json" aria-label="BOM JSON">{text.length > 60000 ? text.slice(0, 60000) + `\n… (${kb(text.length)} in total: download for the rest)` : text}</pre>}
      </>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ catalog
function CatalogPanel() {
  const [m, setM] = useState<Manifest | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Manifest>("/api/discovery/manifest").then((r) => { setM(r); setErr(""); }).catch((e) => setErr(msg(e))), []);
  useEffect(() => { load(); }, [load]);
  const copy = (t: string) => { navigator.clipboard?.writeText(t); toast("Copied"); };
  if (err) return <div className="card"><div className="dc-banner bad" role="alert">{err}</div></div>;
  if (!m) return <div className="card"><span className="mut">Loading…</span></div>;
  const v = m.validation;
  return (
    <div className="card" data-tour="ard-catalog">
      <div className="card-head"><b>ARD catalog</b><span className={`pill ${m.publishable ? "ok" : ""}`}>{m.publishable ? "ready to publish" : "preview only"}</span></div>
      <p className="card-desc">The entries below would be served at <code>/.well-known/ard.json</code>. Agents and MCP servers carry their description inline; skills and BOMs are linked, with the sha-256 of each file in the entry&apos;s trust manifest.</p>
      {m.reasons.length > 0 && <div className="dc-banner warn" role="status"><b>Not published yet:</b><ul className="dc-list">{m.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <Link href="/settings/#discovery">Open the settings</Link></div>}
      {m.manifest && (<>
        <div className="dc-stats"><span><b>{m.entries.length}</b> entries</span><span className="dc-wrap">publisher <code>{m.publisher}</code></span><span>level <b>{m.level}</b></span>
          <span>{m.signed ? "signed with your key (nothing is verified by Agent Studio)" : "not signed"}</span></div>
        {(m.problems ?? []).map((p) => <div key={p} className="small err">{p}</div>)}
        {v && <><Problems c={v.manifest} label="Catalog validation" />
          <div className="small mut" style={{ marginTop: 6 }}>{Object.keys(v.boms).length} BOM file{Object.keys(v.boms).length === 1 ? "" : "s"}: {Object.values(v.boms).every((b) => b.ok) ? "all valid CycloneDX 1.7" : `${Object.values(v.boms).filter((b) => !b.ok).length} invalid`}</div></>}
        <div className="row" style={{ marginTop: 12 }}>
          <a className="chip" href={`${API_BASE}/api/discovery/manifest/download`} download>Download ard.json</a>
          <a className="chip" href={`${API_BASE}/api/discovery/export.zip`} download>Download the whole site (zip)</a>
          <button className="chip" onClick={load}>Rebuild</button>
        </div>
        <h4 className="kn-h">Entries</h4>
        <div aria-label="Catalog entries">{m.entries.map((e) => (
          <div key={e.identifier} className="kn-row"><span className="kn-grow"><b>{e.displayName}</b><div className="small mut dc-wrap">{e.identifier}</div><div className="small mut dc-wrap">{e.type}</div></span>
            <button className="chip small" aria-label={`Copy URL of ${e.displayName}`} onClick={() => copy(e.url)}>{e.inline ? "Copy catalog URL" : "Copy URL"}</button></div>))}</div>
      </>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ push
function PushPanel() {
  const [plan, setPlan] = useState<PushPlan | null>(null);
  const [out, setOut] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function preview() {
    setBusy(true); setErr(""); setOut("");
    try { setPlan(await api<PushPlan>("/api/aibom/push/preview", { method: "POST" })); } catch (e) { setErr(msg(e)); setPlan(null); } finally { setBusy(false); }
  }
  async function send(p: PushPlan) {
    setBusy(true); setErr("");
    try {
      const r = await api<{ token: string | null; sent_to: string }>("/api/aibom/push", { method: "POST", body: JSON.stringify({ confirm: p.confirm }) });
      setOut(`Sent to ${r.sent_to}.${r.token ? ` Processing token ${r.token}.` : ""}`); setPlan(null);
    } catch (e) { setErr(msg(e)); } finally { setBusy(false); }
  }
  return (
    <div className="card" data-tour="aibom-push">
      <div className="card-head"><b>Push to Dependency-Track</b></div>
      <p className="card-desc">Review exactly what would be sent, then confirm. If anything changes in between, nothing is sent and you review again. Configure the server in <Link href="/settings/#discovery">Settings</Link>.</p>
      <div className="row"><button onClick={preview} disabled={busy}>Review what would be sent</button></div>
      {err && <div className="dc-banner bad" role="alert" style={{ marginTop: 8 }}>{err}</div>}
      {out && <div className="dc-banner ok" role="status" style={{ marginTop: 8 }}>{out}</div>}
      {plan && (
        <div aria-label="Push preview" style={{ marginTop: 10 }}>
          <div className="small dc-wrap"><b>{plan.method}</b> <code>{plan.url}</code></div>
          <div className="small mut">Project “{plan.project.name}” version {plan.project.version} · level <b>{plan.redaction_level}</b> · CycloneDX {plan.bom.specVersion}, {kb(plan.bom.bytes)}, {plan.bom.components} components, {plan.bom.services} services</div>
          <div className="small mut dc-wrap">sha-256 {plan.bom.sha256}</div>
          <div className="small mut">{plan.note}</div>
          <button className="acc" style={{ marginTop: 8 }} onClick={() => send(plan)} disabled={busy}>Send to {new URL(plan.url).host}</button>
        </div>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ page
type Tab = "bom" | "catalog" | "data" | "push";
const TABS: [Tab, string][] = [["bom", "AI-BOM"], ["catalog", "ARD catalog"], ["data", "Data & agents"], ["push", "Push"]];

export default function DiscoveryPage() {
  const [tab, setTab] = useState<Tab>("bom");
  const [levels, setLevels] = useState<Level[]>([]);
  useEffect(() => { api<Level[]>("/api/aibom/levels").then(setLevels).catch(() => setLevels([])); }, []);
  return (
    <div>
      <div className="page-head"><h2>Discovery &amp; AI-BOM</h2><span className="grow" /><Link href="/settings/#discovery" className="chip">Settings</Link></div>
      <p className="page-sub">What this system is made of, in standard formats (CycloneDX 1.7), and, only if you choose, how other tools can find it (Agentic Resource Discovery). Previewing never publishes anything.</p>
      <div className="tabs">{TABS.map(([k, label]) => <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{label}</button>)}</div>
      {tab === "bom" && <BomPanel levels={levels} />}
      {tab === "catalog" && <CatalogPanel />}
      {tab === "data" && <DataProducts />}
      {tab === "push" && <PushPanel />}
    </div>
  );
}
