"use client";
// How the app looks and what it is built from: a colour theme, a font and a corner style (one stylesheet block the builder and you can both change), and a small library of
// ready-made parts (table, chart, stats, tabs...) you can add with one click. Each click makes one version, so it can be undone.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AppRow, builderMessage, ComponentInfo, ThemeInfo } from "@/lib/apps";
import { toast } from "@/components/Nav";

export default function DesignPanel({ app, onChanged, onAsk }: { app: AppRow; onChanged: () => void; onAsk: (text: string, label?: string) => void }) {
  const [info, setInfo] = useState<ThemeInfo | null>(null);
  const [parts, setParts] = useState<ComponentInfo[]>([]);
  const [theme, setTheme] = useState("warm");
  const [font, setFont] = useState("system");
  const [radius, setRadius] = useState("soft");
  const [chosen, setChosen] = useState<string[]>([]);
  const [usage, setUsage] = useState<Record<string, string> | null>(null);
  const [err, setErr] = useState("");
  const isStatic = app.type === "static";

  const load = useCallback(async () => {
    try {
      const [t, c] = await Promise.all([api<ThemeInfo>(`/api/apps/${app.id}/theme`), api<ComponentInfo[]>("/api/apps/components")]);
      setInfo(t); setParts(c); setTheme(t.theme || "warm"); setFont(t.font || "system"); setRadius(t.radius || "soft"); setErr("");
    } catch (e) { setErr((e as Error).message); }
  }, [app.id]);
  useEffect(() => { load(); }, [load]);
  if (!info) return <div className="mut small">{err ? <span role="alert" className="err">{err}</span> : "Loading the look…"}</div>;

  const apply = async () => {
    try { await api(`/api/apps/${app.id}/theme`, { method: "PUT", body: JSON.stringify({ theme, font, radius }) }); toast("Theme applied"); onChanged(); setErr(""); }
    catch (e) { setErr((e as Error).message); }
  };
  const add = async () => {
    try {
      const r = await api<{ usage: Record<string, string> }>(`/api/apps/${app.id}/components`, { method: "POST", body: JSON.stringify({ names: chosen }) });
      setUsage(r.usage); setChosen([]); toast("Parts added"); onChanged(); setErr("");
    } catch (e) { setErr((e as Error).message); }
  };
  const toggle = (id: string, on: boolean) => setChosen(on ? [...chosen, id] : chosen.filter((c) => c !== id));
  const swatch = (t: ThemeInfo["themes"][number]) => [t.light.bg, t.light.card, t.light.accent, t.light.fg];
  return (
    <div data-testid="design-panel">
      {err && <div className="err small" role="alert">{err}</div>}
      {!isStatic && <div className="small mut" role="note">Themes and parts are for pages (public/style.css). A server app&apos;s page can use them too.</div>}
      <h4 style={{ margin: "6px 0" }}>Theme</h4>
      <div className="theme-grid" role="radiogroup" aria-label="Colour theme">
        {info.themes.map((t) => (
          <label key={t.id} className={`theme-card ${theme === t.id ? "on" : ""}`}>
            <input type="radio" name="theme" value={t.id} checked={theme === t.id} onChange={() => setTheme(t.id)} aria-label={t.label} />
            <span className="theme-dots" aria-hidden="true">{swatch(t).map((c, i) => <i key={i} style={{ background: c }} />)}</span>
            <b className="small">{t.label}</b>{t.note && <span className="small mut">{t.note}</span>}
          </label>
        ))}
      </div>
      <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 8 }}>
        <label>Font<select value={font} aria-label="Font" onChange={(e) => setFont(e.target.value)}>{info.fonts.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}</select></label>
        <label>Corners<select value={radius} aria-label="Corners" onChange={(e) => setRadius(e.target.value)}>{info.radii.map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}</select></label>
        <button className="acc" onClick={apply}>Apply the theme</button>
        <span className="small mut">It follows the viewer&apos;s light or dark setting.</span>
      </div>
      <h4 style={{ margin: "14px 0 6px" }}>Parts</h4>
      <div className="small mut">Ready-made, accessible parts with no outside libraries. They are added to public/ui.css and public/ui.js and linked from index.html.</div>
      <ul className="parts-list" aria-label="Parts">
        {parts.map((p) => (
          <li key={p.id}><label className="check"><input type="checkbox" checked={chosen.includes(p.id)} onChange={(e) => toggle(p.id, e.target.checked)} aria-label={p.id} /> <b>{p.id}</b></label> <span className="small mut">{p.description}</span></li>
        ))}
      </ul>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <button className="acc" disabled={chosen.length === 0} onClick={add}>Add {chosen.length || ""} selected</button>
        <button disabled={chosen.length === 0} onClick={() => { onAsk(builderMessage(app, `Use these ready-made parts where they fit (add them with app_components, then place them in the page): ${chosen.join(", ")}.`), `Use these parts: ${chosen.join(", ")}`); setChosen([]); }}>Ask the builder to use them</button>
      </div>
      {usage && (
        <div data-testid="parts-usage" style={{ marginTop: 8 }}>
          <b className="small">How to use what you added</b>
          {Object.entries(usage).map(([k, v]) => <div key={k} className="small"><b>{k}</b><pre className="apps-logs">{v}</pre></div>)}
        </div>
      )}
    </div>
  );
}
