"use client";
// The settings form of one box, drawn from the node catalog (so a new kind of box needs no new screen).
import { useState } from "react";
import { NO_REFS, RULE_OPS, SAFE, type Field, type FormField, type NodeSpec, type Refs, type WNode } from "@/lib/workflows";

export type Ctx = {
  agents: { id: string; name: string }[]; tools: { name: string }[];
  srcs: { id: string; name: string; allow_write: boolean; operations: { id: string; method: string; path: string; enabled: boolean }[] }[];
  workflows: { id: string; name: string }[]; refs?: Refs;
};
type Step = { kind: string; agent?: string; prompt?: string; tool?: string; args?: Record<string, unknown>; source?: string; operation?: string; confirmed?: boolean };

function JsonField({ label, value, onChange }: { label: string; value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(JSON.stringify(value ?? {}));
  const [bad, setBad] = useState(false);
  return (
    <label>{label} (JSON, may use {"{{ $prev }}"})
      <input value={text} aria-label={label} onChange={(e) => { setText(e.target.value); try { const v = JSON.parse(e.target.value || "{}"); if (v && typeof v === "object" && !Array.isArray(v)) { onChange(v); setBad(false); } else setBad(true); } catch { setBad(true); } }} />
      {bad && <span className="small" style={{ color: "var(--bad-text)" }}>Not valid JSON yet</span>}</label>
  );
}

function LinesField({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState((value ?? []).join("\n"));                  // the raw text is kept here so a new empty line can be typed
  return <label>{label}<textarea rows={3} value={text} aria-label={label} onChange={(e) => { setText(e.target.value); onChange(e.target.value.split("\n").map((x) => x.trim()).filter(Boolean)); }} /></label>;
}

const NO_VALUE = new Set(["is_empty", "not_empty"]);

/** Rows of "<a> <comparison> <b>": the Filter box (field / value) and the Compare box (left / right). */
function RuleRows({ label, rows, a, b, aPlaceholder, onChange }: { label: string; rows: Record<string, string>[]; a: string; b: string; aPlaceholder: string; onChange: (v: Record<string, string>[]) => void }) {
  const set = (i: number, p: Record<string, string>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <div><b className="small">{label}</b>
      {rows.map((r, i) => (
        <div key={i} className="row" style={{ marginTop: 4, flexWrap: "wrap" }}>
          <input value={r[a] ?? ""} aria-label={`Rule ${i + 1} ${a}`} placeholder={aPlaceholder} onChange={(e) => set(i, { [a]: e.target.value })} />
          <select value={r.op ?? "equals"} aria-label={`Rule ${i + 1} comparison`} onChange={(e) => set(i, { op: e.target.value })}>{RULE_OPS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
          {!NO_VALUE.has(r.op ?? "equals") && <input value={r[b] ?? ""} aria-label={`Rule ${i + 1} ${b}`} placeholder="value or {{ … }}" onChange={(e) => set(i, { [b]: e.target.value })} />}
          <button className="small" aria-label={`Remove rule ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove</button>
        </div>))}
      <button className="small" style={{ marginTop: 4 }} onClick={() => onChange([...rows, { [a]: "", op: "equals", [b]: "" }])}>Add a rule</button>
    </div>
  );
}

const TYPES = ["text", "number", "select", "bool"];
/** The options of a choice, typed as one comma-separated line (the raw text is kept here so a comma can be typed before the next option). */
function OptionsInput({ label, options, onChange }: { label: string; options: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(options.join(", "));
  return <input value={text} aria-label={label} placeholder="options, separated by commas" onChange={(e) => { setText(e.target.value); onChange(e.target.value.split(",").map((x) => x.trim()).filter(Boolean)); }} />;
}
/** The details an "Ask a person" box can ask for (name, question, kind of answer, options for a choice, required or not). */
function FormRows({ label, rows, onChange }: { label: string; rows: FormField[]; onChange: (v: FormField[]) => void }) {
  const set = (i: number, p: Partial<FormField>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <div><b className="small">{label}</b>
      {rows.map((r, i) => (
        <div key={i} className="row" style={{ marginTop: 4, flexWrap: "wrap" }}>
          <input value={r.name} aria-label={`Detail ${i + 1} name`} placeholder="name (letters, digits, _)" onChange={(e) => set(i, { name: e.target.value })} />
          <input value={r.label ?? ""} aria-label={`Detail ${i + 1} question`} placeholder="what to ask" onChange={(e) => set(i, { label: e.target.value })} />
          <select value={r.type} aria-label={`Detail ${i + 1} kind`} onChange={(e) => set(i, { type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
          {r.type === "select" && <OptionsInput label={`Detail ${i + 1} options`} options={r.options ?? []} onChange={(options) => set(i, { options })} />}
          <label className="check"><input type="checkbox" checked={!!r.required} onChange={(e) => set(i, { required: e.target.checked })} /> needed</label>
          <button className="small" aria-label={`Remove detail ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove</button>
        </div>))}
      <button className="small" style={{ marginTop: 4 }} onClick={() => onChange([...rows, { name: "", label: "", type: "text", options: [], required: false }])}>Add a detail</button>
    </div>
  );
}

function StepFields({ s, onChange, ctx }: { s: Step; onChange: (s: Step) => void; ctx: Ctx }) {
  const set = (p: Partial<Step>) => onChange({ ...s, ...p });
  if (s.kind === "agent") return (<>
    <label>Agent<select value={s.agent ?? ""} aria-label="Agent" onChange={(e) => set({ agent: e.target.value })}><option value="">choose…</option>{ctx.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    <label>What to ask it<textarea rows={3} value={s.prompt ?? ""} aria-label="Prompt" onChange={(e) => set({ prompt: e.target.value })} /></label></>);
  if (s.kind === "tool") return (<>
    <label>Tool<select value={s.tool ?? ""} aria-label="Tool" onChange={(e) => set({ tool: e.target.value })}><option value="">choose…</option>{ctx.tools.map((t) => <option key={t.name} value={t.name}>{t.name}{SAFE.has(t.name) ? "" : " (changes things)"}</option>)}</select></label>
    <JsonField label="Arguments" value={s.args} onChange={(args) => set({ args: args as Record<string, unknown> })} />
    {s.tool && !SAFE.has(s.tool) && <label className="check"><input type="checkbox" checked={!!s.confirmed} onChange={(e) => set({ confirmed: e.target.checked })} /> I confirm this tool may change things</label>}</>);
  const src = ctx.srcs.find((x) => x.id === s.source);
  const op = src?.operations.find((o) => o.id === s.operation);
  return (<>
    <label>API<select value={s.source ?? ""} aria-label="API" onChange={(e) => set({ source: e.target.value, operation: "" })}><option value="">choose…</option>{ctx.srcs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Endpoint<select value={s.operation ?? ""} aria-label="Endpoint" onChange={(e) => set({ operation: e.target.value })}><option value="">choose…</option>{(src?.operations ?? []).filter((o) => o.enabled).map((o) => <option key={o.id} value={o.id}>{o.method.toUpperCase()} {o.path}</option>)}</select></label>
    <JsonField label="Arguments" value={s.args} onChange={(args) => set({ args: args as Record<string, unknown> })} />
    {op && !["get", "head", "options"].includes(op.method) && <label className="check"><input type="checkbox" checked={!!s.confirmed} onChange={(e) => set({ confirmed: e.target.checked })} /> I confirm this call may change data{src && !src.allow_write ? " (writes are also off for this API)" : ""}</label>}</>);
}

function Steps({ steps, onChange, ctx }: { steps: Step[]; onChange: (s: Step[]) => void; ctx: Ctx }) {
  return (<>
    {steps.map((s, i) => (
      <div key={i} className="card" data-testid="step" style={{ marginTop: 8 }}>
        <div className="row"><b>Step {i + 1}</b>
          <select value={s.kind} aria-label={`Step ${i + 1} kind`} onChange={(e) => onChange(steps.map((x, j) => (j === i ? { kind: e.target.value } : x)))}><option value="agent">Agent</option><option value="tool">Tool</option><option value="openapi">API call</option></select>
          <span className="grow" /><button className="small" onClick={() => onChange(steps.filter((_, j) => j !== i))} aria-label={`Remove step ${i + 1}`}>Remove</button></div>
        <StepFields s={s} onChange={(ns) => onChange(steps.map((x, j) => (j === i ? ns : x)))} ctx={ctx} />
      </div>))}
    <div className="row" style={{ marginTop: 8 }}><button className="small" onClick={() => onChange([...steps, { kind: "agent", agent: "", prompt: "{{ $prev }}" }])}>Add a step</button></div>
  </>);
}

function One({ f, value, onChange, ctx, type, cfg }: { f: Field; value: any; onChange: (v: unknown) => void; ctx: Ctx; type: string; cfg: any }) {
  const hint = f.help ? <span className="small mut">{f.help}</span> : null;
  switch (f.kind) {
    case "agent": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">choose…</option>{ctx.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>;
    case "tool": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">choose…</option>{ctx.tools.map((t) => <option key={t.name} value={t.name}>{t.name}{SAFE.has(t.name) ? "" : " (changes things)"}</option>)}</select></label>;
    case "source": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">choose…</option>{ctx.srcs.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>;
    case "operation": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">choose…</option>{(ctx.srcs.find((s) => s.id === cfg.source)?.operations ?? []).filter((o) => o.enabled).map((o) => <option key={o.id} value={o.id}>{o.method.toUpperCase()} {o.path}</option>)}</select></label>;
    case "workflow": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">choose…</option>{ctx.workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>;
    case "textarea": case "expr": return <label>{f.label}<textarea rows={f.kind === "expr" ? 2 : 4} value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)} />{hint}</label>;
    case "number": return <label>{f.label}<input type="number" min={f.min} max={f.max} value={value ?? 0} aria-label={f.label} onChange={(e) => onChange(Number(e.target.value))} /></label>;
    case "select": return <label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}>{(f.options ?? []).map((o) => <option key={o} value={o}>{optionWord(type, o)}</option>)}</select></label>;
    case "bool": {
      const risky = f.name === "confirmed";
      return <label className="check"><input type="checkbox" checked={!!value} aria-label={f.label} onChange={(e) => onChange(e.target.checked)} /> {f.label}{risky && type === "tool" && cfg.tool && SAFE.has(cfg.tool) ? " (not needed for a read-only tool)" : ""}</label>;
    }
    case "ref": {
      const list = (ctx.refs ?? NO_REFS)[(f.ref ?? "") as keyof Refs] ?? [];
      return (<label>{f.label}<select value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)}><option value="">{f.required ? "choose…" : "none"}</option>
        {list.map((r) => <option key={r.id} value={r.id}>{r.label}{r.ready === false ? " (not ready)" : ""}</option>)}</select>{hint}</label>);
    }
    case "rules": return <RuleRows label={f.label} rows={value ?? []} a="field" b="value" aPlaceholder="field (customer.email)" onChange={onChange} />;
    case "conditions": return <RuleRows label={f.label} rows={value ?? []} a="left" b="right" aPlaceholder="value or {{ … }}" onChange={onChange} />;
    case "formfields": return <FormRows label={f.label} rows={value ?? []} onChange={onChange} />;
    case "lines": return <LinesField label={f.label} value={value} onChange={onChange} />;
    case "json": return <JsonField label={f.label} value={value} onChange={onChange} />;
    case "steps": return <Steps steps={value ?? []} onChange={onChange} ctx={ctx} />;
    case "pairs": {
      const rows: { name: string; value: string }[] = value ?? [];
      return (<div><b className="small">{f.label}</b>{hint}
        {rows.map((r, i) => <div key={i} className="row" style={{ marginTop: 4 }}>
          <input value={r.name} aria-label={`Field ${i + 1} name`} placeholder="name" onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <input value={r.value} aria-label={`Field ${i + 1} value`} placeholder="value or {{ … }}" onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
          <button className="small" aria-label={`Remove field ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove</button></div>)}
        <button className="small" style={{ marginTop: 4 }} onClick={() => onChange([...rows, { name: "", value: "" }])}>Add a field</button></div>);
    }
    default: return <label>{f.label}<input value={value ?? ""} aria-label={f.label} onChange={(e) => onChange(e.target.value)} /></label>;
  }
}

/** A Schedule box says its choices in words; the saved values stay the short ones ("cron", "run_once"). */
const SCHEDULE_WORDS: Record<string, string> = { interval: "Every few minutes", daily: "Every day at a set time", cron: "A cron pattern (advanced)", run_once: "Run it once when the app is open again", skip: "Skip it" };
export const optionWord = (type: string, option: string) => (type === "schedule" && SCHEDULE_WORDS[option]) || option;

/** Fields that do not apply to the chosen way of repeating are left out of a Schedule box (their saved values are kept). */
const SCHEDULE_ONLY: Record<string, string> = { every_minutes: "interval", at: "daily", cron: "cron" };
export const fieldApplies = (type: string, cfg: Record<string, unknown>, name: string) =>
  type !== "schedule" || !SCHEDULE_ONLY[name] || SCHEDULE_ONLY[name] === String(cfg.mode ?? "interval");

export default function Fields({ spec, node, onChange, ctx }: { spec: NodeSpec; node: WNode; onChange: (config: any) => void; ctx: Ctx }) {
  const cfg = node.config ?? {};
  const kinds = spec.fields.filter((f) => fieldApplies(node.type, cfg, f.name));
  if (!kinds.length) return <div className="small mut">{spec.desc}</div>;
  return (
    <div className="stack" data-testid="node-fields">
      {kinds.map((f) => <One key={f.name} f={f} value={cfg[f.name]} cfg={cfg} type={node.type} ctx={ctx} onChange={(v) => onChange({ ...cfg, [f.name]: v, ...(f.kind === "source" ? { operation: "" } : {}) })} />)}
    </div>
  );
}
