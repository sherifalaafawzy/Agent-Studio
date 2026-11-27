"use client";
// Provider account credits: auto-fetched where the provider's API allows (OpenRouter), otherwise entered by you.
// The budget guard refuses a call before sending if its worst-case cost exceeds the credits left.
import { useState } from "react";
import { api, usd } from "@/lib/api";

export type CreditsInfo = { mode: "auto" | "manual" | "off"; balance_usd: number | null; as_of: number | null; source: string | null; strict: boolean;
  remaining_usd: number | null; checked_at: number | null; error: string | null; auto_available: boolean; why_not: string | null; spent_since_usd: number | null };

const ago = (t: number | null) => { if (!t) return ""; const s = Date.now() / 1000 - t; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };

export default function CreditsPanel({ pid, credits, onChange }: { pid: string; credits: CreditsInfo; onChange: () => void }) {
  const [bal, setBal] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const c = credits;
  const put = async (body: any) => { try { await api(`/api/providers/${pid}/credits`, { method: "PUT", body: JSON.stringify(body) }); setBal(""); setMsg(""); onChange(); } catch (e) { setMsg("⚠ " + (e as Error).message); } };
  const refresh = async () => { setBusy(true); try { await api(`/api/providers/${pid}/credits/refresh`, { method: "POST" }); onChange(); } catch (e) { setMsg("⚠ " + (e as Error).message); } setBusy(false); };
  const low = c.remaining_usd !== null && c.balance_usd ? c.remaining_usd / c.balance_usd < 0.1 : false;
  return (
    <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: 4 }}>
      <div className="row">
        <b>Account credits</b>
        {c.remaining_usd !== null
          ? <span className={`pill ${low ? "bad" : "ok"}`}>{usd(c.remaining_usd)} left</span>
          : <span className="pill">{c.mode === "off" ? "not tracked" : "unknown"}</span>}
        {c.source && <span className="small mut">{c.source}{c.as_of ? ` · ${ago(c.as_of)}` : ""}{c.spent_since_usd ? ` · ${usd(c.spent_since_usd)} used here since` : ""}</span>}
        <span className="grow" />
        {c.auto_available && <button className="ghost small" disabled={busy} onClick={refresh}>{busy ? "Checking…" : "Refresh from provider"}</button>}
      </div>
      <div className="small mut" style={{ marginTop: 4 }}>
        {c.auto_available ? "Fetched automatically from the provider when you connect and before chats (every 10 min)." : c.why_not}
        {" "}Calls whose worst-case cost exceeds the credits left are refused before they are sent.
      </div>
      {c.error && <div className="small" style={{ color: "var(--warn-text)", marginTop: 4 }}>{c.error}</div>}
      <div className="row" style={{ marginTop: 8 }}>
        <input type="number" min={0} step="0.01" value={bal} placeholder={c.auto_available ? "or enter a balance manually ($)" : "your current balance ($)"}
          onChange={(e) => setBal(e.target.value)} style={{ width: 230 }} />
        <button disabled={bal === ""} onClick={() => put({ balance_usd: Number(bal) })}>Set balance</button>
        {c.mode !== "off" && <button className="ghost small" onClick={() => put({ mode: "off" })}>Stop tracking</button>}
        <span className="grow" />
        <label style={{ flexDirection: "row", alignItems: "center", gap: 8, color: "var(--fg2)" }} title="Refuse every call to this provider while its balance is unknown">
          Strict: block when credits can&apos;t be verified
          <span className="switch"><input type="checkbox" checked={c.strict} onChange={(e) => put({ strict: e.target.checked })} /><i /></span></label>
      </div>
      {msg && <div className="small" style={{ marginTop: 4 }}>{msg}</div>}
    </div>
  );
}
