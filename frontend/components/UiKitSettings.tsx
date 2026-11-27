"use client";
// Settings > Agents & sandbox: rich components (charts, time series, maps, cards). Whether agents can show them, the size limits, and the optional map basemap.
// Maps draw a built-in world outline and load nothing from the internet until you switch a basemap on here.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Basemap = { kind: "none" | "tiles" | "pmtiles"; tile_url: string; attribution: string; pmtiles: string };
type Cfg = { enabled: boolean; max_kb: number; max_rows: number; basemap: Basemap; new_agents?: { components: boolean; endpoint: boolean } };

export default function UiKitSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Cfg>("/api/uikit/config").then((c) => { setCfg(c); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!cfg) return <div className="card" data-testid="uikit-settings">{err ? <span role="status" style={{ color: "var(--bad-text)" }}>The component settings could not be loaded: {err}</span> : "Loading the component settings…"}</div>;
  const set = <K extends keyof Cfg>(k: K, v: Cfg[K]) => setCfg({ ...cfg, [k]: v });
  const na = cfg.new_agents ?? { components: true, endpoint: true };                  // what a new agent starts with (each agent has its own AG-UI switches)
  const base = (patch: Partial<Basemap>) => set("basemap", { ...cfg.basemap, ...patch });
  const save = async () => {
    try { setCfg(await api<Cfg>("/api/uikit/config", { method: "PUT", body: JSON.stringify(cfg) })); setErr(""); toast("Component settings saved"); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-testid="uikit-settings">
      <div className="card-head"><b>Rich components</b><InfoTip id="ui-components" /></div>
      <div className="card-desc">Charts, time series, maps and 40+ kinds of cards an agent can show in an answer, each with a table view and exports. <Link href="/components/">Open the component gallery</Link>.</div>
      <label className="check"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Agents can show rich components</label>
      <div className="small mut" style={{ marginTop: 8 }}>AG-UI switches for <b>new</b> agents (each agent can change its own in the agent editor):</div>
      <div className="row" style={{ gap: 18, flexWrap: "wrap" }}>
        <label className="check"><input type="checkbox" checked={na.components} onChange={(e) => set("new_agents", { ...na, components: e.target.checked })} aria-label="New agents show rich components" /> New agents show rich components</label>
        <label className="check"><input type="checkbox" checked={na.endpoint} onChange={(e) => set("new_agents", { ...na, endpoint: e.target.checked })} aria-label="New agents are reachable over the AG-UI stream" /> New agents are reachable over the AG-UI stream <InfoTip id="agui-agent" /></label>
      </div>
      <div className="grid" style={{ marginTop: 8 }}>
        <label>Largest component (KB)
          <input type="number" min={20} max={2000} value={cfg.max_kb} aria-label="Largest component" onChange={(e) => set("max_kb", Number(e.target.value))} /></label>
        <label>Most table rows in one component
          <input type="number" min={100} max={20000} value={cfg.max_rows} aria-label="Most table rows" onChange={(e) => set("max_rows", Number(e.target.value))} /></label>
      </div>
      <div style={{ marginTop: 10 }}><b>Map basemap</b></div>
      <div className="small mut" role="note">Off by default: maps then use a built-in world outline and load nothing from the internet. A basemap makes your browser load map tiles from the server you choose, which can then see which area you look at.</div>
      <label>Basemap
        <select aria-label="Basemap" value={cfg.basemap.kind} onChange={(e) => base({ kind: e.target.value as Basemap["kind"] })}>
          <option value="none">None: built-in outline, fully offline</option><option value="tiles">Tile server (an address you give)</option><option value="pmtiles">A PMTiles file saved as an artifact</option></select></label>
      {cfg.basemap.kind === "tiles" && <div className="grid">
        <label>Tile address
          <input aria-label="Tile address" placeholder="https://tiles.example.com/{z}/{x}/{y}.png" value={cfg.basemap.tile_url} onChange={(e) => base({ tile_url: e.target.value })} /></label>
        <label>Credit to show on the map
          <input aria-label="Attribution" placeholder="© the provider" value={cfg.basemap.attribution} onChange={(e) => base({ attribution: e.target.value })} /></label>
        <div className="small mut">The address may contain your provider's key. The browser needs it to load tiles, so it is shown here as you type it and is not a write-only secret. Follow your provider's terms (the public OpenStreetMap servers do not allow heavy use).</div></div>}
      {cfg.basemap.kind === "pmtiles" && <label>PMTiles file (artifact id)
        <input aria-label="PMTiles artifact" placeholder="12-character id from the Artifacts page" value={cfg.basemap.pmtiles} onChange={(e) => base({ pmtiles: e.target.value })} /></label>}
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 8 }}><button className="acc" onClick={save}>Save component settings</button></div>
    </div>
  );
}
