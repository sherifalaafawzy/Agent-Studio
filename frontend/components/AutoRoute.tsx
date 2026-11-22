"use client";
// "Auto -> model": a collapsed explanation of what Auto chose for a message, and why.
import { AutoRoute } from "@/lib/api";

const ENGINE: Record<string, string> = { rules: "built-in rules", "d1-2b": "d1-2B", "d1-3b": "d1-3B" };
const tierName = (t: string) => (t === "pro" ? "strong" : "fast");

export default function RouteNote({ route, open, showSkipped }: { route: AutoRoute; open?: boolean; showSkipped?: boolean }) {
  const s = route.signals;
  const flags = [s.code && "code", s.reasoning && "deep reasoning", s.tools && "tools", s.vision && "images"].filter(Boolean).join(", ");
  return (
    <details className="auto-note" open={open}>
      <summary><span className="auto-tag">Auto</span> → <b>{route.model}</b><span className="mut"> · {route.provider} · {tierName(route.tier)} tier</span></summary>
      <div className="auto-body">
        <div>{route.reason}</div>
        {route.engine_note && <div className="auto-warn">{route.engine_note}</div>}
        <div className="small mut">
          Read as <b>{s.complexity}</b>{flags ? ` · ${flags}` : ""} · decided by {ENGINE[route.engine] ?? route.engine} · confidence {Math.round(route.confidence * 100)}%
          {route.price.known ? ` · $${route.price.input_per_m}/$${route.price.output_per_m} per M tokens` : " · price unknown"}
        </div>
        {route.alternates.length > 0 && <div className="small mut">Next best if it fails: {route.alternates.map((a) => `${a.provider}/${a.model}`).join(", ")}</div>}
        {showSkipped && route.skipped.length > 0 && (
          <ul className="auto-skipped small mut">{route.skipped.map((k, i) => <li key={i}><b>{k.provider}{k.model ? `/${k.model}` : ""}</b>: {k.why}</li>)}</ul>)}
      </div>
    </details>
  );
}
