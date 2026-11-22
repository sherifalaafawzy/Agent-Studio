"use client";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { Modal } from "@/components/Modal";
import { CatalogForm, MODE_LABEL, type CatalogServer } from "@/components/CatalogMcp";
import { IPlay } from "@/components/Icons";
import { leavesPc, type Test } from "./shared";

type Step = "choose" | "catalog" | "catalog-form" | "custom" | "openapi" | "done";
const CAPTION: Record<Step, string> = {
  choose: "Step 1 of 3 · Choose how to add it", catalog: "Step 2 of 3 · Pick a server", "catalog-form": "Step 2 of 3 · Settings",
  custom: "Step 2 of 3 · Settings", openapi: "Step 2 of 3 · Settings", done: "Step 3 of 3 · Test it",
};
const DOMAIN_LABEL: Record<string, string> = { accounting: "Accounting", sales: "Sales", marketing: "Marketing", manufacturing: "Manufacturing", engineering: "Engineering", data: "Data", "machine-learning": "Machine learning", "deep-learning": "Deep learning" };

type Props = { catalog: CatalogServer[]; startWith?: string | null; onClose: () => void; onAdded: (id: string) => void };

/** One panel, three steps: choose where the server comes from, fill in its settings, then test it. */
export default function AddServerFlow({ catalog, startWith, onClose, onAdded }: Props) {
  const start = startWith ? catalog.find((c) => c.id === startWith) : undefined;
  const [step, setStep] = useState<Step>(start ? "catalog-form" : "choose");
  const [picked, setPicked] = useState<CatalogServer | null>(start ?? null);
  const [added, setAdded] = useState<{ id: string; label: string } | null>(null);
  return (
    <Modal title="Add an MCP server" onClose={onClose}>
      <p className="small mut" style={{ margin: "4px 0 12px" }} aria-live="polite">{CAPTION[step]}</p>
      {step === "choose" && <Choose onPick={setStep} />}
      {step === "catalog" && <CatalogPicker catalog={catalog} onPick={(c) => { setPicked(c); setStep("catalog-form"); }} onBack={() => setStep("choose")} />}
      {step === "catalog-form" && picked && (
        <div>
          <div className="row"><b>{picked.label}</b><span className="small mut">{picked.publisher}</span></div>
          <CatalogForm s={picked} onDone={(s) => { setAdded({ id: s.id, label: s.label }); setStep("done"); }} />
          <div className="row" style={{ marginTop: 8 }}><button className="ghost small" onClick={() => setStep(start ? "choose" : "catalog")}>← Back</button></div>
        </div>)}
      {step === "custom" && <CustomForm onBack={() => setStep("choose")} onDone={(a) => { setAdded(a); setStep("done"); }} />}
      {step === "openapi" && <OpenApiForm onBack={() => setStep("choose")} onDone={(a) => { setAdded(a); setStep("done"); }} />}
      {step === "done" && added && <Done added={added} onOpen={() => { onAdded(added.id); onClose(); }} />}
    </Modal>
  );
}

function Choose({ onPick }: { onPick: (s: Step) => void }) {
  const opts: [Step, string, string][] = [
    ["catalog", "From the catalog", "Checked public servers for accounting, sales, data, ML and more. You see what each can change before it is added."],
    ["custom", "A server of my own", "Any Model Context Protocol server: a local command or a remote URL. Local servers run on this PC as you — add only ones you trust."],
    ["openapi", "From an OpenAPI file", "Turn every operation of an API description into tools. Read operations only, unless you allow writes."],
  ];
  return (
    <div className="mcp-choose">
      {opts.map(([id, title, body]) => (
        <button key={id} className="mcp-choice" onClick={() => onPick(id)}><b>{title}</b><span className="small mut">{body}</span></button>))}
    </div>
  );
}

function CatalogPicker({ catalog, onPick, onBack }: { catalog: CatalogServer[]; onPick: (c: CatalogServer) => void; onBack: () => void }) {
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const domains = useMemo(() => [...new Set(catalog.flatMap((c) => c.domains))].sort(), [catalog]);
  const shown = catalog.filter((c) => (!domain || c.domains.includes(domain)) && (!q || `${c.label} ${c.description} ${c.publisher}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <input className="grow" placeholder="Search the catalog" aria-label="Search the catalog" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Area" value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All areas</option>{domains.map((d) => <option key={d} value={d}>{DOMAIN_LABEL[d] ?? d}</option>)}</select>
      </div>
      <ul className="mcp-pick">
        {shown.map((c) => (
          <li key={c.id}>
            <div className="grow" style={{ minWidth: 0 }}>
              <b>{c.label}</b> <span className="small mut">{c.publisher}</span>
              <div className="small mut">{c.description}</div>
              <div className="mcp-row-badges"><span className={`pill ${MODE_LABEL[c.read_only].tone}`}>{MODE_LABEL[c.read_only].text}</span>
                {leavesPc(undefined, c) && <span className="pill">data leaves PC</span>}
                {c.installed && <span className="pill ok">added</span>}</div>
            </div>
            <button className="acc small" disabled={c.installed} onClick={() => onPick(c)}>{c.installed ? "Added" : "Choose"}</button>
          </li>))}
      </ul>
      {shown.length === 0 && <div className="empty small">No servers match.</div>}
      <div className="row" style={{ marginTop: 8 }}><button className="ghost small" onClick={onBack}>← Back</button></div>
    </div>
  );
}

function CustomForm({ onBack, onDone }: { onBack: () => void; onDone: (a: { id: string; label: string }) => void }) {
  const [f, setF] = useState({ id: "", label: "", description: "", transport: "stdio", command: "npx", args: "", url: "", env: "", on: true });
  const [err, setErr] = useState("");
  const set = (k: string, v: string | boolean) => setF((s) => ({ ...s, [k]: v }));
  async function add() {
    const env = Object.fromEntries(f.env.split("\n").map((l) => l.trim()).filter((l) => l.includes("=")).map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]));
    try {
      await api("/api/mcp", { method: "POST", body: JSON.stringify({ id: f.id, label: f.label, description: f.description, transport: f.transport,
        command: f.transport === "stdio" ? f.command : null, args: f.args.split("\n").map((x) => x.trim()).filter(Boolean), url: f.transport === "http" ? f.url : null, env, enabled: f.on }) });
      toast(`Added ${f.label || f.id}`); onDone({ id: f.id, label: f.label || f.id });
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div>
      <p className="mut small">Any Model Context Protocol server. Local servers run on your machine as your user — only add ones you trust.</p>
      <div className="seg" style={{ marginBottom: 12 }}>{(["stdio", "http"] as const).map((t) => <button key={t} className={f.transport === t ? "on" : ""} onClick={() => set("transport", t)}>{t === "stdio" ? "Local command" : "Remote URL"}</button>)}</div>
      <div className="grid">
        <label>Id<input value={f.id} placeholder="my-server" onChange={(e) => set("id", e.target.value)} /></label>
        <label>Name<input value={f.label} placeholder="My server" onChange={(e) => set("label", e.target.value)} /></label>
      </div>
      <label style={{ marginTop: 10 }}>What it&apos;s for (agents read this)<input value={f.description} onChange={(e) => set("description", e.target.value)} /></label>
      {f.transport === "stdio" ? (
        <div className="grid" style={{ marginTop: 10 }}>
          <label>Command<input value={f.command} placeholder="npx / uvx / node / python" onChange={(e) => set("command", e.target.value)} /></label>
          <label>Arguments (one per line; {"{workspace}"} = current workspace)<textarea rows={3} value={f.args} placeholder={"-y\n@scope/server@1.2.3"} onChange={(e) => set("args", e.target.value)} /></label>
        </div>
      ) : <label style={{ marginTop: 10 }}>Server URL<input value={f.url} placeholder="https://example.com/mcp" onChange={(e) => set("url", e.target.value)} /></label>}
      <details className="mcp-more" style={{ marginTop: 10 }}><summary>Advanced: {f.transport === "stdio" ? "environment" : "headers"}</summary>
        <label style={{ marginTop: 6 }}>{f.transport === "stdio" ? "Environment (NAME=value per line — stored locally, never shown again)" : "Headers as HEADER_NAME=value per line (HEADER_AUTHORIZATION=Bearer …)"}
          <textarea rows={2} autoComplete="off" value={f.env} onChange={(e) => set("env", e.target.value)} /></label>
      </details>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10, color: "var(--fg2)" }}>
        <input type="checkbox" checked={f.on} onChange={(e) => set("on", e.target.checked)} />Switch it on right away</label>
      {err && <div className="err small" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}><button className="ghost" onClick={onBack}>Back</button><button className="acc" disabled={!f.id} onClick={add}>Add server</button></div>
    </div>
  );
}

function OpenApiForm({ onBack, onDone }: { onBack: () => void; onDone: (a: { id: string; label: string }) => void }) {
  const [f, setF] = useState({ id: "", label: "", spec: "", base: "", auth: "", writes: false });
  const [err, setErr] = useState("");
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));
  async function add() {
    try {
      const body: any = { id: f.id, label: f.label, spec: f.spec, base_url: f.base || null, allow_write: f.writes, headers: {} };
      if (f.auth.trim()) body.headers = { Authorization: f.auth.trim() };
      const r = await api<any>("/api/mcp/openapi", { method: "POST", body: JSON.stringify(body) });
      toast(`Added ${r.label}: ${r.operations} operations`); onDone({ id: f.id, label: r.label });
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div>
      <p className="mut small">Every operation in the document becomes a tool your agents can use. Read operations only, unless you allow writes below. To build workflows from an API, use the OpenAPI steps tab on the Workflows page instead.</p>
      <div className="grid">
        <label>Id<input value={f.id} placeholder="billing-api" onChange={(e) => set("id", e.target.value)} /></label>
        <label>Name<input value={f.label} placeholder="Billing API" onChange={(e) => set("label", e.target.value)} /></label>
      </div>
      <label style={{ marginTop: 10 }}>OpenAPI file (absolute path, e.g. C:/specs/billing.yaml) or https URL
        <input value={f.spec} onChange={(e) => set("spec", e.target.value)} /></label>
      <details className="mcp-more" style={{ marginTop: 10 }}><summary>Advanced: server URL and sign-in</summary>
        <label style={{ marginTop: 6 }}>Server URL (optional — defaults to the document&apos;s own)
          <input value={f.base} placeholder="https://api.example.com/v1" onChange={(e) => set("base", e.target.value)} /></label>
        <label style={{ marginTop: 10 }}>Authorization header value (optional, stored locally)
          <input type="password" autoComplete="off" value={f.auth} placeholder="Bearer …" onChange={(e) => set("auth", e.target.value)} /></label>
      </details>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10, color: "var(--fg2)" }}>
        <input type="checkbox" checked={f.writes} onChange={(e) => set("writes", e.target.checked)} />
        Allow changes (POST / PUT / PATCH / DELETE) — agents can then modify data in this API</label>
      {err && <div className="err small" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}><button className="ghost" onClick={onBack}>Back</button>
        <button className="acc" disabled={!f.id || !f.label || !f.spec} onClick={add}>Add API</button></div>
    </div>
  );
}

function Done({ added, onOpen }: { added: { id: string; label: string }; onOpen: () => void }) {
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function check() {
    setBusy(true); setRes({ ok: true, text: "Starting… the first run may download the server." });
    try {
      const r = await api<Test>(`/api/mcp/${added.id}/test`, { method: "POST" });
      setRes(r.ok ? { ok: true, text: `Works: ${r.tools?.length ?? 0} tools (${(r.tools ?? []).slice(0, 5).map((x) => x.name).join(", ")}${(r.tools?.length ?? 0) > 5 ? ", …" : ""})` } : { ok: false, text: r.error ?? "did not start" });
    } catch (e) { setRes({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  }
  return (
    <div>
      <p><b>{added.label}</b> was added. Test it to be sure it starts, then open it to give it to an agent.</p>
      <div className="row"><button className="ghost" disabled={busy} onClick={check}><IPlay size={13} />Test it</button><span className="grow" /><button className="acc" onClick={onOpen}>Open server</button></div>
      {res && <p className={`small ${res.ok ? "mut" : "err"}`} role="status">{res.text}</p>}
      <p className="small mut">A server that needs a credential shows as &ldquo;needs credentials&rdquo; until you add it on its Settings tab; the test cannot pass before that.</p>
    </div>
  );
}
