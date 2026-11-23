"use client";
// An app, full screen. The Build tab is the studio: the builder chat beside the live preview (plan, checks, "pick an element and change it"). The other tabs are the files and an
// editor, the changes waiting for review, versions (undo and compare), the look and ready-made parts, connections to your agents and data, settings, git and exporting.
// On a phone the sections are tabs. The studio stays mounted when you switch tabs, so a running build is not lost.
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api, followSse } from "@/lib/api";
import { AppRow, TreeFile, TYPE_LABEL } from "@/lib/apps";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete } from "@/components/Modal";
import { toast } from "@/components/Nav";
import { ChangesPanel, ExportPanel, VersionsPanel } from "@/components/apps/AppPanels";
import { BuilderRequest, BuildStart } from "@/components/apps/BuilderPanel";
import ConnectPanel from "@/components/apps/ConnectPanel";
import DesignPanel from "@/components/apps/DesignPanel";
import FilePanel from "@/components/apps/FilePanel";
import GitPanel from "@/components/apps/GitPanel";
import PreviewPanel from "@/components/apps/PreviewPanel";
import { BUILD_KEY } from "@/components/apps/SimpleCreate";
import SettingsPanel from "@/components/apps/SettingsPanel";
import StudioPanel from "@/components/apps/StudioPanel";

type Tab = "build" | "files" | "changes" | "versions" | "design" | "connect" | "settings" | "git" | "export";
const ADVANCED: [Tab, string][] = [["build", "Build"], ["files", "Files"], ["changes", "Changes"], ["versions", "Versions"], ["design", "Design"], ["connect", "Connect"], ["settings", "Settings"], ["git", "Git"], ["export", "Export"]];
const SIMPLE: [Tab, string][] = [["build", "Build"], ["changes", "Changes"], ["versions", "Versions"], ["design", "Design"], ["connect", "Connect"], ["settings", "Settings"], ["export", "Export"]];

function Workspace() {
  const router = useRouter();
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const building = params.get("build") === "1";
  const [app, setApp] = useState<AppRow | null>(null);
  const [files, setFiles] = useState<TreeFile[]>([]);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<Tab>("build");
  const [tick, setTick] = useState(0);
  const [consoleTick, setConsoleTick] = useState(0);
  const [start, setStart] = useState<BuildStart | null>(null);
  const [request, setRequest] = useState<BuilderRequest | null>(null);
  const [del, setDel] = useState(false);
  const loadedStart = useRef(false);
  const reqId = useRef(0);

  const reload = useCallback(async () => {
    if (!id) return;
    try { const [a, f] = await Promise.all([api<AppRow>(`/api/apps/${id}`), api<TreeFile[]>(`/api/apps/${id}/files`)]); setApp(a); setFiles(f); setErr(""); setTick((t) => t + 1); }
    catch (e) { setErr((e as Error).message); }
  }, [id]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!id) return;
    const ctl = new AbortController();
    followSse(`/api/apps/${id}/events`, (ev) => { if (ev?.type === "console") setConsoleTick((t) => t + 1); else reload(); }, ctl.signal).catch(() => undefined);
    return () => ctl.abort();
  }, [id, reload]);
  useEffect(() => {
    if (!building || loadedStart.current || !id) return;
    loadedStart.current = true;
    try { const raw = sessionStorage.getItem(BUILD_KEY(id)); if (raw) setStart(JSON.parse(raw)); } catch { /* start without the note */ }
  }, [building, id]);

  if (!id) return <div className="card" role="alert">No app was chosen. <Link href="/apps/">Back to your apps</Link></div>;
  if (!app) return <div data-testid="apps-edit">{err ? <div className="err" role="alert">{err} <Link href="/apps/">Back to your apps</Link></div> : <div className="mut">Opening the app…</div>}</div>;

  const simple = app.mode === "simple";
  const tabs = (simple ? SIMPLE : ADVANCED).filter(([t]) => t !== "changes" || !simple || app.pending > 0 || tab === "changes");
  const current: Tab = tabs.some(([t]) => t === tab) ? tab : tabs[0][0];
  const mode = async (m: "simple" | "advanced") => { try { setApp(await api<AppRow>(`/api/apps/${id}`, { method: "PUT", body: JSON.stringify({ mode: m }) })); toast(m === "simple" ? "Simple mode" : "Advanced mode"); } catch (e) { setErr((e as Error).message); } };
  const remove = async () => { try { await api(`/api/apps/${id}?confirm=true`, { method: "DELETE" }); router.push("/apps/"); } catch (e) { setDel(false); setErr((e as Error).message); } };
  const started = () => { try { sessionStorage.removeItem(BUILD_KEY(id)); } catch { /* nothing to remove */ } router.replace(`/apps/edit/?id=${id}`); };
  const ask = (text: string, label?: string) => { setRequest({ id: ++reqId.current, text, label }); setTab("build"); };

  return (
    <div data-testid="apps-edit" className={`apps-ws ${simple ? "simple" : ""}`} data-tab={current}>
      <div className="apps-ws-head">
        <Link href="/apps/" className="chip small">← Apps</Link>
        <h3 style={{ margin: 0 }}>{app.name}</h3><InfoTip id="apps" />
        <span className="chip small">{TYPE_LABEL[app.type]}</span><span className="chip small">v{app.version}</span>
        <span className="grow" />
        {simple ? <button onClick={() => mode("advanced")}>Open in advanced mode</button> : <button onClick={() => mode("simple")}>Simple mode</button>}
        <button onClick={() => setDel(true)} aria-label="Delete this app">Delete</button>
      </div>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="tabs" role="tablist">
        {tabs.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={current === t} className={`tab ${current === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {label}{t === "changes" && app.pending > 0 ? ` (${app.pending})` : ""}
          </button>
        ))}
      </div>
      <div className="apps-ws-body">
        <div hidden={current !== "build"}><StudioPanel app={app} tick={tick} consoleTick={consoleTick} onChanged={reload} start={start} onStarted={started} request={request} onAsk={ask} /></div>
        {current === "files" && (
          <div className="apps-files">
            <FilePanel appId={id} files={files} onChanged={reload} refreshKey={tick} />
            <PreviewPanel app={app} refreshKey={tick} consoleKey={consoleTick} />
          </div>
        )}
        {current === "changes" && <ChangesPanel appId={id} onChanged={reload} refreshKey={tick} />}
        {current === "versions" && <VersionsPanel appId={id} onChanged={reload} refreshKey={tick} />}
        {current === "design" && <DesignPanel app={app} onChanged={reload} onAsk={ask} />}
        {current === "connect" && <ConnectPanel app={app} onChanged={reload} />}
        {current === "settings" && <SettingsPanel app={app} onChanged={reload} />}
        {current === "git" && <GitPanel appId={id} refreshKey={tick} />}
        {current === "export" && <ExportPanel appId={id} slug={app.slug} />}
      </div>
      {del && <ConfirmDelete what={`"${app.name}"`} onConfirm={remove} onClose={() => setDel(false)} />}
    </div>
  );
}

export default function AppEditPage() {
  return <Suspense fallback={null}><Workspace /></Suspense>;
}
