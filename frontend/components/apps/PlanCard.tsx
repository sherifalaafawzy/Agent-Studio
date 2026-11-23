"use client";
// The builder's plan for the app: written before any file is changed, and ticked off as the steps are done.
import { BuildPlan } from "@/lib/apps";

export default function PlanCard({ plan, onClear }: { plan: BuildPlan | null; onClear?: () => void }) {
  if (!plan) return null;
  const done = plan.steps.filter((s) => s.done).length;
  return (
    <details className="plan-card" open data-testid="plan-card">
      <summary><b>{plan.title}</b> <span className="chip small" aria-label={`${done} of ${plan.steps.length} steps done`}>{done} of {plan.steps.length} steps</span></summary>
      {plan.summary && <p className="small" style={{ margin: "4px 0" }}>{plan.summary}</p>}
      <ol className="plan-steps">
        {plan.steps.map((s, i) => <li key={i} className={s.done ? "done" : ""}><span aria-hidden="true">{s.done ? "✓" : "○"}</span> {s.title}<span className="sr-only">{s.done ? " (done)" : " (to do)"}</span></li>)}
      </ol>
      {(plan.pages.length > 0 || plan.features.length > 0 || plan.agents.length > 0 || plan.data.length > 0) && (
        <div className="small mut">
          {plan.pages.length > 0 && <div>Pages: {plan.pages.join(", ")}</div>}
          {plan.features.length > 0 && <div>Features: {plan.features.join("; ")}</div>}
          {plan.agents.length > 0 && <div>Agents: {plan.agents.join("; ")}</div>}
          {plan.data.length > 0 && <div>Data: {plan.data.join("; ")}</div>}
        </div>
      )}
      {onClear && <div style={{ marginTop: 4 }}><button className="chip small" onClick={onClear} aria-label="Dismiss the plan">Dismiss</button></div>}
    </details>
  );
}
