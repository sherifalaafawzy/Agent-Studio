"use client";
// Ready-made test sets (Science Lab: citation validity, refusal behaviour). Adding one makes an ordinary, editable test set.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";

type Starter = { id: string; name: string; description: string; cases: number; added: boolean };

export default function EvalStarters({ onAdded }: { onAdded: (id: string) => void }) {
  const [list, setList] = useState<Starter[]>([]);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<{ starters: Starter[] }>("/api/evals/starters").then((r) => setList(r.starters)).catch(() => setList([])), []);
  useEffect(() => { load(); }, [load]);
  const add = async (s: Starter) => {
    setErr("");
    try { await api(`/api/evals/starters/${s.id}`, { method: "POST" }); toast(s.added ? `${s.name} reset to the original` : `${s.name} added`); await load(); onAdded(s.id); }
    catch (e) { setErr((e as Error).message); }
  };
  if (!list.length && !err) return null;
  return (
    <div className="card" data-testid="eval-starters" style={{ marginTop: 8 }}>
      <b>Ready-made sets</b>
      <p className="small mut" style={{ margin: "4px 0" }}>Add one, then run it against your science agent. The checks are free; only the agent&apos;s replies use credits.</p>
      {list.map((s) => (
        <div key={s.id} style={{ marginTop: 6 }}>
          <div className="row"><span className="grow">{s.name} <span className="mut small">· {s.cases} cases</span></span>
            <button className="small" onClick={() => add(s)} aria-label={`${s.added ? "Reset" : "Add"} ${s.name}`}>{s.added ? "Reset" : "Add"}</button></div>
          <div className="small mut">{s.description}</div>
        </div>))}
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{err}</div>}
    </div>
  );
}
