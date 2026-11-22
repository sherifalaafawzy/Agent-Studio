"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IKey, IPlay, IShield, ITrash } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";
import { CatalogForm, MODE_LABEL, type CatalogServer } from "@/components/CatalogMcp";
import BrowserPanel from "./BrowserPanel";
import DecisionModelPanel from "./DecisionModelPanel";
import TimeSeriesPanel from "@/components/TimeSeriesPanel";
import TrainerPanel from "@/components/TrainerPanel";
import CloudAccounts from "@/components/CloudAccounts";
import { access, ago, initials, leavesPc, tint, type Issue, type Row, type Test } from "./shared";

type Tab = "overview" | "settings" | "tools" | "health";
const TABS: [Tab, string][] = [["overview", "Overview"], ["settings", "Settings"], ["tools", "Tools"], ["health", "Health & logs"]];

type Props = { row: Row; issues: Issue[]; onChange: () => void; onBack: () => void; onAdd: (id: string) => void; headRef: React.Ref<HTMLHeadingElement> };

/** The right column: one server, in four tabs. Rows that are not installed yet only get an overview and an Add button. */
export default function ServerDetail({ row, issues, onChange, onBack, onAdd, headRef }: Props) {
  const { m, c } = row;
  const [tab, setTab] = useState<Tab>("overview");
  const [env, setEnv] = useState<Record<string, string>>({});
  const [test, setTest] = useState<Test | null>(null);
  const [testing, setTesting] = useState(false);

  const put = async (patch: any, msg: string) => {
    try { await api(`/api/mcp/${row.id}`, { method: "PUT", body: JSON.stringify(patch) }); setEnv({}); toast(msg); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const runTest = async () => {
    setTesting(true); setTest(null);
    try { const r = await api<Test>(`/api/mcp/${row.id}/test`, { method: "POST" }); setTest(r); toast(r.ok ? `${row.label}: ${r.tools?.length} tools available` : `${row.label} failed to start`, r.ok ? "ok" : "err"); onChange(); }
    catch (e) { setTest({ ok: false, error: (e as Error).message }); }
    setTesting(false);
  };
  async function writes(s: CatalogServer, allow: boolean) {
    if (allow && !window.confirm(`Allow ${s.label} to change things?\n\n${s.writes}`)) return;
    try { await api(`/api/catalog/mcp/${s.id}/mode`, { method: "PUT", body: JSON.stringify({ allow_writes: allow }) }); toast(allow ? "Writes allowed" : "Back to read-only"); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function remove() {
    if (!m || !confirm(`Delete MCP server “${m.label}”?`)) return;
    try { await api(`/api/mcp/${m.id}`, { method: "DELETE" }); toast(`${m.label} deleted`); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  const where = m ? (m.transport === "http" ? m.url : [m.command, ...(m.args ?? [])].filter(Boolean).join(" ")) : "";
  const names = test?.ok ? test.tools : m?.last_test?.ok ? (m.last_test.names ?? []).map((n) => ({ name: n, description: "" })) : undefined;
  const acc = access(c);

  const head = (
    <div className="mcp-dhead">
      <button className="ghost small mcp-back" onClick={onBack}>← All servers</button>
      <div className="mcp-dtitle">
        <div className="mono" style={{ background: tint(row.id) }} aria-hidden>{initials(row.label)}</div>
        <div className="grow" style={{ minWidth: 0 }}>
          <h3 ref={headRef} tabIndex={-1}>{row.label}</h3>
          <div className="mcp-row-badges">
            {m ? <span className={`pill ${row.tone}`}>{row.statusText}</span> : <span className="pill">not added</span>}
            {m?.default_on && <span className="pill" title="Attached to every agent">default</span>}
            {c && acc === "read" && <span className="pill ok">read-only</span>}
            {c && acc === "write" && <span className="pill warn">can write</span>}
            {leavesPc(m, c) && <span className="pill">data leaves PC</span>}
            {m && !m.builtin && !c && <span className="pill">{m.status === "pending_approval" ? "agent-proposed" : "added by you"}</span>}
            {m?.builtin && <span className="pill">built-in</span>}
            {m?.last_test?.ok && <span className="small mut">{m.last_test.tools} tools · tested {ago(m.last_test.ts)}</span>}
          </div>
        </div>
        {m
          ? <label className="switch" title={m.enabled ? "Turn off" : m.status === "pending_approval" ? "Approve & turn on" : "Turn on"}>
            <input type="checkbox" aria-label={`${m.label} on or off`} checked={m.enabled} onChange={(e) => put({ enabled: e.target.checked }, `${m.label} ${e.target.checked ? "on" : "off"}`)} /><i /></label>
          : <button className="acc" onClick={() => onAdd(row.id)}>Add this server</button>}
      </div>
    </div>
  );

  const catalogInfo = c && (
    <div className="kv mcp-kv">
      <span>Publisher</span><span>{c.publisher}{c.official ? " · official" : " · community"}</span>
      <span>Access</span><span><span className={`pill ${MODE_LABEL[c.read_only].tone}`}>{MODE_LABEL[c.read_only].text}</span></span>
      <span>What it can change</span><span>{c.writes}</span>
      <span>Where your data goes</span><span>{c.data_leaves}</span>
      <span>Licence</span><span>{c.license}</span>
      {c.setup && <><span>Before you start</span><span>{c.setup}</span></>}
      <span>Documentation</span><span><a href={c.docs} target="_blank" rel="noreferrer">{c.docs}</a></span>
    </div>
  );

  if (!m) {
    return (
      <div className="mcp-detail-in">
        {head}
        <div className="mcp-dbody">
          <p style={{ marginTop: 0 }}>{row.description}</p>
          {catalogInfo}
          <p className="small mut">Nothing runs until you add it. You will see what it can change and where data goes before anything is saved. <InfoTip id="catalog-readonly" /></p>
        </div>
      </div>
    );
  }

  return (
    <div className="mcp-detail-in">
      {head}
      <div className="mcp-tabs" role="tablist" aria-label={`${row.label} sections`}>
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" id={`mcp-tab-${id}`} aria-selected={tab === id} aria-controls="mcp-tabpanel" className={`tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            {label}{id === "health" && issues.length > 0 && <span className="pill bad">{issues.length}</span>}</button>))}
      </div>
      <div className="mcp-dbody" role="tabpanel" id="mcp-tabpanel" aria-labelledby={`mcp-tab-${tab}`}>
        {tab === "overview" && <>
          <p style={{ marginTop: 0 }}>{m.description || "No description."}</p>
          {m.status === "pending_approval" && <div className="section-note"><IShield size={16} /><span>An agent proposed this server. Check how it runs on the Settings tab before switching it on — it runs on your machine.</span></div>}
          <div className="kv mcp-kv">
            <span>Runs</span><span>{m.transport === "http" ? "On a remote server (HTTP)" : "On this PC, as a local command"}</span>
            <span>Used by</span><span>{m.used_by.length ? m.used_by.map((a) => <a key={a.id} href="/agents/" className="pill">{a.name}</a>) : "No agent yet — give it to one in Agent Studio."}</span>
          </div>
          {catalogInfo}
        </>}

        {tab === "settings" && <>
          {m.requires_env.length > 0 && <section className="mcp-sec">
            <h4>Credentials</h4>
            <p className="small mut">Stored on this PC. A saved value is never shown again.</p>
            {m.requires_env.map((k) => (
              <div className="row" key={k} style={{ gap: 6 }}>
                <span className="small" style={{ display: "inline-flex", gap: 5, alignItems: "center", color: "var(--fg2)" }}><IKey size={13} />{k}</span>
                {m.env_set[k] && !env[k] ? <span className="pill ok">stored</span> : null}
                <input type="password" autoComplete="off" placeholder={m.env_set[k] ? "replace…" : "paste value"} value={env[k] ?? ""} onChange={(e) => setEnv({ ...env, [k]: e.target.value })} style={{ flex: 1, minWidth: 120 }} />
                <button disabled={!env[k]} onClick={() => put({ env: { [k]: env[k] }, enabled: true }, `${m.label}: ${k} saved`)}>Save</button>
              </div>))}
          </section>}

          {c && c.installed && <section className="mcp-sec">
            <h4>Catalog settings</h4>
            <div className="row" style={{ flexWrap: "wrap" }}>
              {c.read_only === "enforced" && <button className={c.allow_writes ? "ghost" : ""} onClick={() => writes(c, !c.allow_writes)}>{c.allow_writes ? "Make read-only" : "Allow writes…"}</button>}
            </div>
            <details className="mcp-more"><summary>Change this server&apos;s saved settings</summary><CatalogForm key={c.id} s={c} onDone={onChange} /></details>
          </section>}

          {row.id === "playwright" && <BrowserPanel onChange={onChange} />}
          {row.id === "d1" && <DecisionModelPanel onChange={onChange} />}
          {row.id === "timeseries" && <TimeSeriesPanel onChange={onChange} />}
          {row.id === "trainer" && <TrainerPanel />}
          {row.id === "cloudml" && <CloudAccounts />}

          {!m.requires_env.length && !(c && c.installed) && !["playwright", "d1", "timeseries", "trainer", "cloudml"].includes(row.id) &&
            <p className="small mut" style={{ marginTop: 0 }}>This server needs no credentials or settings. Switch it on at the top right.</p>}

          <details className="mcp-more mcp-adv"><summary>Advanced</summary>
            <div className="small mut" style={{ margin: "6px 0 2px" }}>How it runs</div>
            <code className="small" style={{ wordBreak: "break-all", display: "block" }}>{m.transport === "http" ? "HTTP" : "local"} · {where}</code>
            {!m.builtin && <div style={{ marginTop: 12 }}><button className="ghost small danger" title="Delete" onClick={remove}><ITrash size={14} />Delete server</button></div>}
          </details>
        </>}

        {tab === "tools" && <>
          <div className="row" style={{ marginBottom: 10 }}>
            <button className="ghost small" disabled={!m.ready || testing} onClick={runTest} title={m.ready ? "Start it, list its tools, stop it" : "Turn it on (and add credentials) first"}><IPlay size={13} />{testing ? "Starting…" : "List tools"}</button>
            <span className="small mut">{names ? `${names.length} tools` : "Starts the server briefly to ask what it offers."}</span>
          </div>
          {names?.length ? <dl className="mcp-toolist">{names.map((t) => <div key={t.name}><dt><code>{t.name}</code></dt>{t.description && <dd className="small mut">{t.description}</dd>}</div>)}</dl>
            : <div className="empty small">No tools listed yet.</div>}
        </>}

        {tab === "health" && <>
          <div className="row" style={{ marginBottom: 10 }}>
            <button className="ghost small" disabled={!m.ready || testing} onClick={runTest} title={m.ready ? "Start it, list its tools, stop it" : "Turn it on (and add credentials) first"}>
              <IPlay size={13} />{testing ? "Starting… (first run downloads it)" : "Test"}</button>
          </div>
          {test?.ok && <p className="small" style={{ color: "var(--ok)" }}>Works: {test.tools?.length} tools.</p>}
          {test && !test.ok && <div className="small err">{test.error}</div>}
          {!test && m.last_test?.ok && <p className="small mut">Last test passed: {m.last_test.tools} tools · tested {ago(m.last_test.ts)}</p>}
          {!test && m.last_test && !m.last_test.ok && <div className="small err" title={m.last_test.error}>Last test: {m.last_test.error}</div>}
          {!test && !m.last_test && <p className="small mut">Not tested yet.</p>}
          <h4 style={{ marginTop: 16 }}>Issues from the harness</h4>
          {issues.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{issues.map((i) => <li key={i.id}><b>{i.title}</b> — {i.suggestion}</li>)}</ul>
            : <p className="small mut" style={{ margin: 0 }}>None open. The harness raises an issue here when this server crashes or fails to start.</p>}
          {issues.length > 0 && <p className="small mut">Use the &ldquo;Needs attention&rdquo; bar at the top to debug or dismiss them.</p>}
        </>}
      </div>
    </div>
  );
}
