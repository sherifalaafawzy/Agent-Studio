"use client";
// Under an answer: what the guardrails flagged or blocked during this turn. Never the text that was checked.
import { useState } from "react";

export type GuardEvent = { stage: string; agent: string; profile_name?: string; profile?: string; action: string; category?: string; reason?: string };
const STAGE: Record<string, string> = { input: "a message", output: "an answer", tool: "a tool call" };

export default function GuardrailNote({ events }: { events: GuardEvent[] }) {
  const [open, setOpen] = useState(false);
  if (!events?.length) return null;
  const blocked = events.filter((e) => e.action === "blocked").length;
  const word = blocked ? `${blocked} blocked` : `${events.length} flagged`;
  return (
    <div className="memory-note small" data-testid="guardrail-note">
      <button className="ghost small" aria-expanded={open} onClick={() => setOpen((o) => !o)}>🛡 Guardrails: {word}{blocked && events.length > blocked ? ` · ${events.length - blocked} other` : ""}</button>
      {open && <ul>{events.map((e, i) => <li key={i}><b>{e.action}</b> {STAGE[e.stage] ?? e.stage} ({e.profile_name ?? e.profile}{e.agent ? `, ${e.agent}` : ""}){e.reason ? `: ${e.reason}` : ""}</li>)}</ul>}
    </div>
  );
}
