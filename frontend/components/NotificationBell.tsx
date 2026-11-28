"use client";
// The bell in the sidebar: messages from workflows' "Notify me" boxes and from the scheduler. Nothing is sent outside this computer;
// a desktop pop-up is shown only for notifications that ask for one, and only after you allowed pop-ups for this app.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";

export type Note = { id: string; created: number; title: string; body: string; level: string; source: string; link: string; read: boolean; desktop: boolean };
type List = { items: Note[]; unread: number };

const EVERY_MS = 20_000;
const TONE: Record<string, "ok" | "warn" | "err"> = { info: "ok", success: "ok", warning: "warn", error: "err" };

function ago(t: number): string {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
}

function desktop(n: Note) {
  if (!n.desktop || typeof window === "undefined" || !("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(n.title, { body: n.body.slice(0, 200), tag: n.id }); } catch { /* some browsers only allow it from a service worker */ }
}

export default function NotificationBell() {
  const [data, setData] = useState<List>({ items: [], unread: 0 });
  const [open, setOpen] = useState(false);
  const [perm, setPerm] = useState<string>(typeof window !== "undefined" && "Notification" in window ? Notification.permission : "unsupported");
  const newest = useRef<number | null>(null);
  const load = useCallback(async () => {
    try {
      const r = await api<List>("/api/notifications?limit=30");
      const fresh = newest.current === null ? [] : r.items.filter((n) => n.created > (newest.current as number) && !n.read);
      newest.current = Math.max(newest.current ?? 0, ...r.items.map((n) => n.created), 0);
      fresh.reverse().forEach((n) => { toast(n.title, TONE[n.level] ?? "ok"); desktop(n); });
      setData(r);
    } catch { /* offline: try again next time */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, EVERY_MS); return () => clearInterval(t); }, [load]);
  const act = async (path: string, method = "POST") => { try { await api(path, { method }); } catch { /* shown as unchanged */ } load(); };
  const allow = async () => { try { setPerm(await Notification.requestPermission()); } catch { setPerm("denied"); } };
  return (
    <span style={{ position: "relative" }}>
      <button className="icon-btn" onClick={() => setOpen((o) => !o)} aria-label={`Notifications${data.unread ? `, ${data.unread} unread` : ""}`} aria-expanded={open} title="Notifications" data-testid="bell">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
        {data.unread > 0 && <span className="pill bad" data-testid="bell-count" style={{ position: "absolute", top: -6, right: -8, fontSize: 11, padding: "0 4px" }}>{data.unread > 99 ? "99+" : data.unread}</span>}
      </button>
      {open && (
        <div className="card bell-panel" role="dialog" aria-label="Notifications" data-testid="bell-panel">
          <div className="row bell-head"><b className="grow">Notifications</b>
            {data.unread > 0 && <button className="ghost small" onClick={() => act("/api/notifications/read-all")}>Mark all read</button>}
            {data.items.length > 0 && <button className="ghost small" onClick={() => act("/api/notifications", "DELETE")}>Clear</button>}</div>
          {perm === "default" && <button className="small" style={{ marginTop: 6 }} onClick={allow}>Allow desktop pop-ups</button>}
          {data.items.length === 0 && <p className="small mut">Nothing yet. Add a "Notify me" box to a workflow, or switch on a schedule.</p>}
          {data.items.map((n) => (
            <div key={n.id} data-testid="note" className={`bell-note ${n.read ? "read" : ""}`}>
              <div className="row"><span className={`pill ${n.level === "error" ? "bad" : n.level === "warning" ? "warn" : n.level === "success" ? "ok" : ""}`}>{n.level}</span>
                <b className="grow small">{n.title}</b><span className="small mut">{ago(n.created)}</span></div>
              {n.body && <div className="small bell-body" title={n.body.length > 160 ? n.body.slice(0, 600) : undefined}>{n.body.length > 300 ? `${n.body.slice(0, 300)}…` : n.body}</div>}
              <div className="row bell-actions">
                {n.link && <Link className="small" href={n.link} onClick={() => { if (!n.read) act(`/api/notifications/${n.id}/read`); setOpen(false); }}>Open</Link>}
                {!n.read && <button className="ghost small" onClick={() => act(`/api/notifications/${n.id}/read`)}>Mark read</button>}
                <button className="ghost small" aria-label={`Delete ${n.title}`} onClick={() => act(`/api/notifications/${n.id}`, "DELETE")}>Delete</button>
              </div>
            </div>))}
        </div>)}
    </span>
  );
}
