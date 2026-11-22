"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IServer } from "@/components/Icons";

type BrowserCfg = { browser: string; headless: boolean; isolated: boolean; viewport: string; allowed_origins: string[]; blocked_origins: string[] };

export default function BrowserPanel({ onChange }: { onChange: () => void }) {
  const [c, setC] = useState<BrowserCfg | null>(null);
  const [allow, setAllow] = useState("");
  const [block, setBlock] = useState("");
  useEffect(() => { api<BrowserCfg>("/api/browser/settings").then((x) => { setC(x); setAllow(x.allowed_origins.join("\n")); setBlock(x.blocked_origins.join("\n")); }).catch(() => {}); }, []);
  if (!c) return null;
  const lines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);
  async function save(next: Partial<BrowserCfg>) {
    const body = { ...c, ...next };
    try { const r = await api<BrowserCfg>("/api/browser/settings", { method: "PUT", body: JSON.stringify(body) }); setC(r); toast("Browser settings saved — applies to the next browser session"); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  return (
    <div className="card browser-card mcp-subcard" data-tour="browser-card">
      <div className="card-head"><IServer size={18} /><b>Browser use</b><span className="pill">Playwright</span><span className="grow" />
        <span className="small mut">Agents can open pages, click, fill forms and read them.</span></div>
      <div className="browser-grid">
        <label>Browser
          <select value={c.browser} onChange={(e) => save({ browser: e.target.value })}>
            {["chromium", "chrome", "msedge", "firefox", "webkit"].map((b) => <option key={b} value={b}>{b}</option>)}</select></label>
        <label>Viewport
          <select value={c.viewport} onChange={(e) => save({ viewport: e.target.value })}>
            {["1280x720", "1440x900", "1920x1080", "1024x768", "390x844"].map((v) => <option key={v} value={v}>{v}{v === "390x844" ? " (phone)" : ""}</option>)}</select></label>
        <label className="browser-tog"><span>Show the window</span><span className="switch"><input type="checkbox" checked={!c.headless} onChange={(e) => save({ headless: !e.target.checked })} /><i /></span></label>
        <label className="browser-tog"><span>Clean profile each time</span><span className="switch"><input type="checkbox" checked={c.isolated} onChange={(e) => save({ isolated: e.target.checked })} /><i /></span></label>
      </div>
      <div className="browser-grid" style={{ marginTop: 10 }}>
        <label>Only these sites (one per line; empty = any)
          <textarea rows={3} value={allow} onChange={(e) => setAllow(e.target.value)} placeholder="https://docs.example.com" /></label>
        <label>Never these sites (one per line)
          <textarea rows={3} value={block} onChange={(e) => setBlock(e.target.value)} placeholder="https://ads.example" /></label>
      </div>
      <div className="row" style={{ marginTop: 10, justifyContent: "flex-end" }}>
        <span className="small mut" style={{ marginRight: "auto" }}>Browsers other than Chromium may need installing first (npx playwright install).</span>
        <button className="acc small" onClick={() => save({ allowed_origins: lines(allow), blocked_origins: lines(block) })}>Save sites</button>
      </div>
    </div>
  );
}
