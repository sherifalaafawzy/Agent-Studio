"use client";
// Model picker: loads the provider's models from its API (via the backend) — no typing model names.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type ModelInfo = { id: string; label: string; input_per_m: number | null; output_per_m: number | null };
type ModelsResp = { provider: string; source: "live" | "catalog" | "manual"; error: string | null; models: ModelInfo[]; cached: boolean };

const cache = new Map<string, Promise<ModelsResp>>();
export function loadModels(pid: string, refresh = false): Promise<ModelsResp> {
  if (refresh || !cache.has(pid)) cache.set(pid, api<ModelsResp>(`/api/providers/${pid}/models${refresh ? "?refresh=true" : ""}`).catch((e) => { cache.delete(pid); throw e; }));
  return cache.get(pid)!;
}
export const forgetModels = (pid: string) => cache.delete(pid);

const price = (m: ModelInfo) => m.input_per_m == null ? "" : m.input_per_m === 0 && m.output_per_m === 0 ? " · free" : ` · $${fmt(m.input_per_m)}/$${fmt(m.output_per_m ?? 0)} per M`;
const fmt = (n: number) => (n >= 10 ? n.toFixed(0) : n >= 1 ? n.toFixed(2).replace(/\.?0+$/, "") : n.toFixed(3).replace(/\.?0+$/, ""));

export default function ModelSelect({ provider, value, onChange, emptyLabel, className, compact, showStatus }: {
  provider: string; value: string; onChange: (v: string) => void; emptyLabel?: string; className?: string; compact?: boolean; showStatus?: boolean;
}) {
  const [resp, setResp] = useState<ModelsResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [custom, setCustom] = useState(false);
  const load = useCallback((refresh = false) => {
    if (!provider) return;
    setLoading(true); setErr("");
    loadModels(provider, refresh).then(setResp).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  }, [provider]);
  useEffect(() => { setResp(null); setCustom(false); load(); }, [load]);

  const models = resp?.models ?? [];
  const known = !value || models.some((m) => m.id === value);
  if (custom) return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", width: compact ? undefined : "100%" }}>
      <input autoFocus className={className} value={value} placeholder="exact model id" onChange={(e) => onChange(e.target.value)} style={{ flex: 1, minWidth: 140 }} />
      <button className="ghost small" onClick={() => setCustom(false)}>List</button>
    </span>
  );
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: 3, width: compact ? undefined : "100%" }}>
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
        <select className={className} value={value} disabled={loading && !models.length} style={{ flex: 1, minWidth: 0 }}
          onChange={(e) => e.target.value === "__custom" ? setCustom(true) : e.target.value === "__refresh" ? load(true) : onChange(e.target.value)}>
          <option value="">{loading && !models.length ? "Loading models…" : emptyLabel ?? "Default"}</option>
          {!known && <option value={value}>{value} (not in list)</option>}
          {models.map((m) => <option key={m.id} value={m.id}>{m.label !== m.id ? `${m.label} (${m.id})` : m.id}{compact ? "" : price(m)}</option>)}
          <option disabled>──────────</option>
          <option value="__refresh">↻ Reload list from provider</option>
          <option value="__custom">Enter a model id…</option>
        </select>
      </span>
      {showStatus && (err ? <span className="small err">{err}</span> : resp && (
        <span className="small mut">{resp.source === "live" ? `${models.length} models from the provider` : resp.source === "manual" ? `Showing the ${models.length} model(s) you typed in${resp.error ? ` — couldn't list live: ${resp.error}` : ""}` : `Showing LiteLLM's catalog (${models.length}) — couldn't list live: ${resp.error}`}</span>))}
    </span>
  );
}
