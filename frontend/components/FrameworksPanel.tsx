"use client";
// Settings > Agents & sandbox: the agent frameworks, what each can do here (capability matrix) and a one-click install.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Fw = { id: string; label: string; blurb: string; strengths: string[]; licence: string; native: boolean; caps: Record<string, string>; packages: string[]; size_mb: number;
  notes: string; billing: string; models: string; state: string; version: string | null; error: string | null };
type Matrix = { columns: { id: string; label: string; means: string }[]; frameworks: Fw[]; install: { running: boolean; framework: string | null; log: string[]; error: string | null } };

const MARK: Record<string, string> = { yes: "●", partial: "◐", no: "○" };
const WORD: Record<string, string> = { yes: "yes", partial: "partly", no: "no" };

export default function FrameworksPanel() {
  const [m, setM] = useState<Matrix | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Matrix>("/api/frameworks").then((x) => { setM(x); setErr(""); return x; }).catch((e) => { setErr(e.message); return null; }), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!m?.install.running) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [m?.install.running, load]);
  const install = async (id: string) => {
    try { await api(`/api/frameworks/${id}/install`, { method: "POST", body: JSON.stringify({ accepted: ok }) }); setOpen(null); setOk(false); await load(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const remove = async (f: Fw) => {
    try { await api(`/api/frameworks/${f.id}`, { method: "DELETE" }); toast(`${f.label} removed`); await load(); } catch (e) { toast((e as Error).message, "err"); }
  };
  return (
    <div className="card" data-testid="frameworks-panel">
      <div className="card-head"><b>Agent frameworks</b><InfoTip id="frameworks" /></div>
      <div className="card-desc">Pick which library runs each agent in the agent editor. Google ADK is built in. The others install into their own folder with one click (nothing is installed until you accept its licence and size). Models, tools, memory and guardrails are always Agent Studio&apos;s.</div>
      {err && <div className="small" role="status" style={{ color: "var(--bad-text)" }}>Frameworks could not be loaded: {err}</div>}
      {m && (
        <div style={{ overflowX: "auto" }}>
          <table className="tbl" aria-label="What each framework can do">
            <thead><tr><th>Framework</th>{m.columns.map((c) => <th key={c.id} title={c.means}>{c.label}</th>)}<th /></tr></thead>
            <tbody>
              {m.frameworks.map((f) => (
                <tr key={f.id}>
                  <td><b>{f.label}</b><div className="small mut">{f.native ? "built in" : f.state === "ready" ? `installed${f.version ? ` ${f.version}` : ""}` : f.state === "installing" ? "installing…" : f.state === "failed" ? "install failed" : `${f.licence}, about ${f.size_mb} MB`}</div></td>
                  {m.columns.map((c) => <td key={c.id} title={`${c.label}: ${WORD[f.caps[c.id]]}`} aria-label={`${f.label} ${c.label}: ${WORD[f.caps[c.id]]}`}><span aria-hidden="true">{MARK[f.caps[c.id]]}</span><span className="sr-only"> {WORD[f.caps[c.id]]}</span></td>)}
                  <td style={{ whiteSpace: "nowrap" }}>
                    {!f.native && f.state !== "ready" && f.state !== "installing" && <button onClick={() => { setOpen(open === f.id ? null : f.id); setOk(false); }}>Install</button>}
                    {!f.native && f.state === "ready" && <button onClick={() => remove(f)}>Remove</button>}
                  </td>
                </tr>))}
            </tbody>
          </table>
          <div className="small mut">● yes &nbsp; ◐ partly &nbsp; ○ no. Hover a column name to see what it means.</div>
          {m.frameworks.filter((f) => open === f.id).map((f) => (
            <div key={f.id} className="card" role="group" aria-label={`Install ${f.label}`} style={{ marginTop: 10 }}>
              <div><b>{f.label}</b>: {f.blurb}</div>
              <ul className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>
                <li>Good for: {f.strengths.join("; ")}.</li><li>{f.billing}</li><li>Models: {f.models}.</li><li>{f.notes}</li>
                <li>Installs {f.packages.join(", ")} (about {f.size_mb} MB, {f.licence}), only versions published at least 14 days ago.</li>
              </ul>
              {f.state === "failed" && f.error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>The last install failed: {f.error}</div>}
              <label className="check"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I accept the licence ({f.licence}) and the download size</label>
              <div className="row" style={{ marginTop: 8 }}><button className="pri" disabled={!ok} onClick={() => install(f.id)}>Install {f.label}</button><button onClick={() => setOpen(null)}>Cancel</button></div>
            </div>))}
          {m.install.running && <pre className="small mut" data-testid="install-log" style={{ whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto" }}>Installing {m.install.framework}…{"\n"}{m.install.log.join("\n")}</pre>}
          {!m.install.running && m.install.error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{m.install.error}</div>}
        </div>)}
    </div>
  );
}
