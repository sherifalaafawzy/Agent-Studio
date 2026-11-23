"use client";
// Science Lab > Compute: exact, local calculations (no network): expressions, units, constants, molar mass, sequence statistics and numeric calculus.
// Every result is labelled "computed here". Floating-point results are as precise as double precision, never measurements.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { parseObject, tableChart } from "@/lib/science";
import UiBlock from "../uikit/UiBlock";

type Kind = "calc" | "convert" | "constant" | "formula_mass" | "sequence" | "integrate" | "derivative" | "root" | "table";
const KINDS: [Kind, string, string][] = [
  ["calc", "Calculator", "2*pi*sqrt(4)"], ["convert", "Units", "1"], ["constant", "Constants", "c"], ["formula_mass", "Molar mass", "C6H12O6"], ["sequence", "DNA / RNA sequence", "ATGGCCTAA"],
  ["integrate", "Integral", "x**2"], ["derivative", "Derivative", "x**3"], ["root", "Find a root", "x**2-2"], ["table", "Table and chart", "sin(x)"],
];
type Const = { symbol: string; name: string; value: number; unit: string; exact: boolean; uncertainty?: number };

export default function ComputePanel() {
  const [kind, setKind] = useState<Kind>("calc");
  const [expr, setExpr] = useState("2*pi*sqrt(4)");
  const [vars, setVars] = useState("");
  const [from, setFrom] = useState("eV");
  const [to, setTo] = useState("J");
  const [a, setA] = useState("0");
  const [b, setB] = useState("3");
  const [consts, setConsts] = useState<{ source: string; constants: Const[] } | null>(null);
  const [res, setRes] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api("/api/science/constants").then(setConsts).catch(() => undefined); }, []);
  const choose = (k: Kind) => { setKind(k); setExpr(KINDS.find((x) => x[0] === k)![2]); setRes(null); setErr(""); };
  const run = async () => {
    const v = parseObject(vars);
    if (v.error) { setErr(v.error); return; }
    setErr(""); setRes(null);
    try { setRes(await api("/api/science/calc", { method: "POST", body: JSON.stringify({ kind, expression: expr, variables: v.value, a: Number(a), b: Number(b), variable: "x", unit_from: from, unit_to: to }) })); }
    catch (e) { setErr((e as Error).message); }
  };
  const calculus = kind === "integrate" || kind === "root" || kind === "table";
  const shown = res ? Object.entries(res).filter(([k, v]) => !["x", "y", "label", "atoms", "percent", "kind"].includes(k) && typeof v !== "object") : [];
  return (
    <div className="card" data-testid="science-compute">
      <div className="card-head"><b>Compute</b></div>
      <div className="card-desc">Runs on this PC with no network and no code execution. Results are floating-point numbers: a numeric check supports a result but is not a proof.</div>
      <div className="row" style={{ gap: 4, flexWrap: "wrap" }} role="tablist" aria-label="Kind of calculation">
        {KINDS.map(([k, label]) => <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "acc small" : "ghost small"} onClick={() => choose(k)}>{label}</button>)}
      </div>
      <div className="grid" style={{ marginTop: 8 }}>
        {kind === "constant" ? (
          <label>Constant
            <select value={expr} aria-label="Constant" onChange={(e) => setExpr(e.target.value)}>{(consts?.constants ?? [{ symbol: "c", name: "speed of light" } as Const]).map((c) => <option key={c.symbol} value={c.symbol}>{c.symbol}: {c.name}</option>)}</select></label>
        ) : kind === "sequence" ? (
          <label style={{ gridColumn: "1 / -1" }}>Sequence (A, C, G, T/U, N)<textarea value={expr} aria-label="Sequence" rows={3} onChange={(e) => setExpr(e.target.value)} /></label>
        ) : (
          <label>{kind === "convert" ? "Number" : kind === "formula_mass" ? "Formula" : "Expression in x"}<input value={expr} aria-label="Expression" onChange={(e) => setExpr(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void run(); }} /></label>
        )}
        {kind === "convert" && <>
          <label>From<input value={from} aria-label="From unit" onChange={(e) => setFrom(e.target.value)} /></label>
          <label>To<input value={to} aria-label="To unit" onChange={(e) => setTo(e.target.value)} /></label></>}
        {(kind === "calc" || calculus || kind === "derivative") && <label>Variables (JSON, optional)<input value={vars} aria-label="Variables" placeholder='{"m": 2}' onChange={(e) => setVars(e.target.value)} /></label>}
        {(calculus || kind === "derivative") && <>
          <label>{kind === "derivative" ? "At x =" : "From x ="}<input type="number" value={a} aria-label="Start" onChange={(e) => setA(e.target.value)} /></label>
          {kind !== "derivative" && <label>To x =<input type="number" value={b} aria-label="End" onChange={(e) => setB(e.target.value)} /></label>}</>}
      </div>
      <div className="row" style={{ marginTop: 8 }}><button className="acc" onClick={run} disabled={!expr.trim()}>Compute</button></div>
      {err && <div className="err small" role="alert">{err}</div>}
      {res && (
        <div data-testid="science-compute-result" style={{ marginTop: 8 }}>
          <div className="chip" title="Calculated here, not looked up">computed here</div>
          <table className="sci-kv"><tbody>{shown.map(([k, v]) => <tr key={k}><th>{k.replace(/_/g, " ")}</th><td>{String(v)}</td></tr>)}</tbody></table>
          {res.atoms && <div className="small">Atoms: {Object.entries(res.atoms).map(([k, v]) => `${k}${v}`).join(" ")} · Percent by mass: {Object.entries(res.percent as Record<string, number>).map(([k, v]) => `${k} ${v}%`).join(", ")}</div>}
          {kind === "table" && Array.isArray(res.x) && <UiBlock spec={tableChart(res.x, res.y, expr)} />}
          {kind === "constant" && consts && <div className="small mut">{consts.source}</div>}
          {res.source && kind !== "constant" && <div className="small mut">Source: {String(res.source)}</div>}
        </div>
      )}
    </div>
  );
}
