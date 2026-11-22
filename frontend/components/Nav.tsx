"use client";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { IAgents, IBook, IChat, ICoin, IGear, IGrid, IHelp, ISetup, IKnowledge, IMonitor, IMoon, IPlug, IPlus, IPulse, IServer, ISide, ISun, ITimeline, IX, Spark, IBrain, IShield, ICatalog, ILock, IFlask, IArchive, IFlow } from "./Icons";
import { startTour } from "./TourRunner";

type NavItem = [string, string, (p: { size?: number }) => React.ReactElement];
const groups: [string, NavItem[]][] = [
  ["Work", [["/dashboard/", "Dashboard", IGrid], ["/", "Chat", IChat], ["/runs/", "Runs", ITimeline], ["/artifacts/", "Artifacts", IArchive], ["/external/", "External agents", IPlug]]],
  ["Build", [["/agents/", "Agent Studio", IAgents], ["/workflows/", "Workflows", IFlow], ["/skills/", "Skills", IBook], ["/knowledge/", "Knowledge", IKnowledge], ["/mcp/", "MCP servers", IServer], ["/catalog/", "Catalog", ICatalog], ["/memory/", "Memory", IBrain]]],
  ["Observe", [["/telemetry/", "Telemetry", IPulse], ["/billing/", "Billing", ICoin], ["/security/", "Security", ILock], ["/evals/", "Evaluations", IFlask], ["/discovery/", "AI-BOM", IShield], ["/settings/", "Settings", IGear]]],
];
const items: NavItem[] = groups.flatMap(([, list]) => list);

/** App-wide toasts: window.dispatchEvent(new CustomEvent("as:toast", { detail: { text, tone } })) or toast(text, tone). */
export function toast(text: string, tone: "ok" | "warn" | "err" = "ok") {
  window.dispatchEvent(new CustomEvent("as:toast", { detail: { text, tone } }));
}

function Toaster() {
  const [list, setList] = useState<{ id: number; text: string; tone: string }[]>([]);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent).detail; const id = Date.now() + Math.random();
      setList((l) => [...l.slice(-3), { id, text: d.text, tone: d.tone }]);
      setTimeout(() => setList((l) => l.filter((x) => x.id !== id)), d.tone === "err" ? 7000 : 3500);
    };
    window.addEventListener("as:toast", on);
    return () => window.removeEventListener("as:toast", on);
  }, []);
  return <div className="toasts" role="status" aria-live="polite">{list.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}</div>;
}

/** Phones: a top bar with a menu button; the sidebar slides in as a drawer. */
export function MobileBar() {
  const path = usePathname() ?? "/";
  useEffect(() => { document.body.classList.remove("nav-open"); }, [path]);
  return (
    <div className="mobile-bar">
      <button className="icon-btn" aria-label="Open menu" onClick={() => document.body.classList.toggle("nav-open")}><ISide size={18} /></button>
      <span className="brand"><span className="brand-mark"><Spark size={14} /></span>Agent Studio</span>
    </div>
  );
}

/** Ctrl/Cmd+K: jump to any page, recent chat, or start a new chat. */
function Palette({ rows }: { rows: Row[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); setQ(""); setI(0); }
      else if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  if (!open) return null;
  const all = [{ label: "New chat", href: `/?new=${Date.now()}`, k: "action" },
    ...items.map(([href, label]) => ({ label, href, k: "page" })),
    { label: "Set-up guide", href: "/setup/", k: "help" },
    ...rows.slice(0, 30).map((r) => ({ label: r.title, href: `/?s=${r.id}`, k: r.running ? "running chat" : "chat" }))];
  const hits = all.filter((x) => !q || x.label.toLowerCase().includes(q.toLowerCase())).slice(0, 16);
  const go = (href: string) => { setOpen(false); router.push(href); };
  return (
    <div className="palette" onClick={() => setOpen(false)}>
      <div className="palette-box" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={q} placeholder="Go to a page or chat…" onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(x + 1, hits.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(x - 1, 0)); }
            else if (e.key === "Enter" && hits[i]) go(hits[i].href);
          }} />
        <div className="palette-list">
          {hits.map((h, n) => <div key={h.href + n} className={`palette-item ${n === i ? "on" : ""}`} onMouseEnter={() => setI(n)} onClick={() => go(h.href)}>
            <span>{h.label}</span><span className="k">{h.k}</span></div>)}
          {hits.length === 0 && <div className="small mut" style={{ padding: 12 }}>No matches.</div>}
        </div>
      </div>
    </div>
  );
}

type Row = { id: string; title: string; agent: string | null; provider: string; updated: number; running?: boolean };
type Theme = "system" | "light" | "dark";

function applyTheme(t: Theme) {
  const el = document.documentElement;
  if (t === "system") el.removeAttribute("data-theme"); else el.setAttribute("data-theme", t);
  try { localStorage.setItem("as-theme", t); } catch { /* storage may be blocked */ }
}

function SidebarInner() {
  const path = usePathname() ?? "/";
  const sp = useSearchParams();
  const router = useRouter();
  const current = path === "/" ? sp.get("s") : null;
  const [rows, setRows] = useState<Row[]>([]);
  const [min, setMin] = useState(false);
  const [theme, setTheme] = useState<Theme>("system");
  const [online, setOnline] = useState<boolean | null>(null);

  const load = useCallback(() => api<Row[]>("/api/chat/sessions?limit=60").then((r) => { setRows(r); setOnline(true); }).catch(() => setOnline(false)), []);
  useEffect(() => {
    load();
    try { setTheme((localStorage.getItem("as-theme") as Theme) || "system"); setMin(localStorage.getItem("as-sb-min") === "1"); } catch { /* ignore */ }
    const on = () => load();
    window.addEventListener("as:sessions", on);
    const t = setInterval(load, 20000);
    return () => { window.removeEventListener("as:sessions", on); clearInterval(t); };
  }, [load]);

  const toggleMin = () => setMin((m) => { try { localStorage.setItem("as-sb-min", m ? "0" : "1"); } catch { /* ignore */ } return !m; });
  const cycleTheme = () => { const n: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system"; setTheme(n); applyTheme(n); };

  async function del(id: string, e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation();
    if (!confirm("Delete this chat and its saved agent runs?")) return;
    await api(`/api/chat/sessions/${id}`, { method: "DELETE" }).catch(() => {});
    if (id === current) router.push(`/?new=${Date.now()}`);
    load();
  }

  return (
    <aside className={`sb ${min ? "min" : ""}`} data-tour="sidebar">
      <Palette rows={rows} />
      <Toaster />
      <div className="sb-top">
        <Link href="/" className="brand"><span className="brand-mark"><Spark size={16} /></span><span className="hide-min">Agent Studio</span></Link>
        <button className="icon-btn" onClick={toggleMin} title={min ? "Expand sidebar" : "Collapse sidebar"}><ISide /></button>
      </div>
      <Link href="/" className="sb-new" data-tour="new-chat" title="New chat" onClick={(e) => { e.preventDefault(); router.push(`/?new=${Date.now()}`); }}><span className="plus"><IPlus size={15} /></span><span className="hide-min">New chat</span></Link>
      <nav className="sb-nav">
        {groups.map(([g, list]) => <div key={g}>
          <div className="sb-group">{g}</div>
          {list.map(([href, label, Icon]) => (
            <Link key={href} href={href} title={label} className={`sb-link ${path === href || (href !== "/" && path.startsWith(href)) ? "on" : ""}`}><Icon /><span className="hide-min">{label}</span></Link>
          ))}
        </div>)}
      </nav>
      <div className="sb-sec hide-min">Recents</div>
      <div className="sb-recents hide-min">
        {rows.length === 0 && <div className="small mut" style={{ padding: "4px 10px" }}>{online === false ? "Backend offline — run npm run dev" : "Your chats will appear here."}</div>}
        {rows.map((r) => (
          <Link key={r.id} href={`/?s=${r.id}`} className={`sb-chat ${r.id === current ? "on" : ""}`} title={`${r.agent ?? "plain chat"} · ${r.provider}`} style={{ textDecoration: "none" }}>
            {r.running && <span className="run-dot" title="running in the background" />}<span className="t">{r.title}</span>
            <button className="x" onClick={(e) => del(r.id, e)} title="Delete chat"><IX size={14} /></button>
          </Link>
        ))}
      </div>
      {min && <div style={{ flex: 1 }} />}
      <div className="sb-foot">
        <span className={`dot ${online ? "ok" : online === false ? "bad" : ""}`} title={online ? "backend connected" : "backend offline"} />
        <span className="hide-min grow">{online ? "Connected" : online === false ? "Offline" : "…"} <kbd title="Command palette">Ctrl K</kbd></span>
        <Link href="/setup/" className="icon-btn" title="Set-up guide: install Ollama, add a model, install coding agents" aria-label="Set-up guide"><ISetup size={16} /></Link>
        <button className="icon-btn" onClick={() => startTour("welcome")} title="Take the guided tour" aria-label="Take the guided tour"><IHelp size={16} /></button>
        <button className="icon-btn" onClick={cycleTheme} title={`Theme: ${theme}`}>{theme === "light" ? <ISun size={16} /> : theme === "dark" ? <IMoon size={16} /> : <IMonitor size={16} />}</button>
      </div>
    </aside>
  );
}

export default function Nav() {
  return <Suspense fallback={<aside className="sb" />}><SidebarInner /></Suspense>;
}

/** Normal pages get a centered reading column; the chat page is full-bleed. */
export function Frame({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "/";
  return path === "/" || path.startsWith("/runs") ? <>{children}</> : <div className="page">{children}</div>;
}
