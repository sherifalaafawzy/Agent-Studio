"use client";
// Connect an app to git: start a repository in the app's folder, commit, and push to a remote you choose. Nothing is pushed without your confirmation and the remote's token is write-only.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { GitStatus } from "@/lib/apps";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";

type LogRow = { hash: string; author: string; date: string; message: string };

export default function GitPanel({ appId, refreshKey }: { appId: string; refreshKey: number }) {
  const [st, setSt] = useState<GitStatus | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);
  const [msg, setMsg] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [branch, setBranch] = useState("main");
  const [diff, setDiff] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"push" | "pull" | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try {
      const s = await api<GitStatus>(`/api/apps/${appId}/git`);
      setSt(s); setErr("");
      if (s.remote) setUrl((u) => u || s.remote);
      if (s.initialised) setLog(await api<LogRow[]>(`/api/apps/${appId}/git/log`)); else setLog([]);
    } catch (e) { setErr((e as Error).message); }
  }, [appId]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const post = async (path: string, body: unknown, ok: string) => { try { await api(`/api/apps/${appId}/git/${path}`, { method: path === "remote" ? "PUT" : "POST", body: JSON.stringify(body) }); toast(ok); setErr(""); await load(); } catch (e) { setErr((e as Error).message); } };
  const showDiff = async () => { try { setDiff((await api<{ diff: string }>(`/api/apps/${appId}/git/diff`)).diff || "(no changes)"); } catch (e) { setErr((e as Error).message); } };
  const go = async () => { const c = confirm!; setConfirm(null); await post(c, { confirm: true }, c === "push" ? "Pushed" : "Pulled"); };
  const identity = err.includes("Settings > Apps");
  if (!st) return <div className="mut small">{err ? <span role="alert" className="err">{err}</span> : "Loading git status…"}</div>;
  return (
    <div data-testid="git-panel">
      {err && <div className="err small" role="alert">{err}{identity && <> <a href="/settings/">Open Settings</a></>}</div>}
      {!st.initialised ? (
        <div>
          <div className="small mut">This app is not a git repository yet. Starting one only creates a hidden .git folder inside the app&apos;s folder.</div>
          <div className="row" style={{ marginTop: 6, gap: 6 }}><input value={branch} aria-label="Branch name" style={{ width: 120 }} onChange={(e) => setBranch(e.target.value)} /><button className="acc" onClick={() => post("init", { branch }, "Repository started")}>Start a repository</button></div>
        </div>
      ) : (
        <div>
          <div className="small">Branch <b>{st.branch}</b>{st.last_commit && <> · last commit: {st.last_commit}</>}</div>
          {st.changes.length === 0 ? <div className="small mut">Nothing to commit.</div> : (
            <ul className="small" aria-label="Changed files" style={{ margin: "4px 0", paddingLeft: 18 }}>{st.changes.map((c) => <li key={c.path}><code>{c.status}</code> {c.path}</li>)}</ul>
          )}
          <div className="row" style={{ gap: 6, marginTop: 6 }}>
            <input className="grow" value={msg} aria-label="Commit message" placeholder="Commit message" onChange={(e) => setMsg(e.target.value)} />
            <button className="acc" disabled={!msg.trim()} onClick={async () => { await post("commit", { message: msg }, "Committed"); setMsg(""); }}>Commit</button>
            <button onClick={showDiff}>Show changes</button>
          </div>
          {diff !== null && <pre className="apps-logs" aria-label="Git diff">{diff}</pre>}

          <h4 style={{ margin: "14px 0 4px" }}>Remote</h4>
          <div className="small mut">An https address (for example https://github.com/you/app.git) or the full path of a folder on this PC made with <code>git init --bare</code>.</div>
          <div className="grid" style={{ marginTop: 4 }}>
            <label>Address<input value={url} aria-label="Remote address" onChange={(e) => setUrl(e.target.value)} /></label>
            <label>Branch<input value={branch} aria-label="Remote branch" onChange={(e) => setBranch(e.target.value)} /></label>
          </div>
          <label style={{ marginTop: 4 }}>Access token (optional, never shown again)
            <input type="password" autoComplete="off" value={token} aria-label="Remote token" placeholder={st.has_token ? "set (hidden)" : "not set"} onChange={(e) => setToken(e.target.value)} /></label>
          <div className="row" style={{ gap: 6, marginTop: 6 }}>
            <button onClick={async () => { await post("remote", { url, branch, ...(token ? { token } : {}) }, "Remote saved"); setToken(""); }} disabled={!url.trim()}>Save remote</button>
            <button disabled={!st.remote} onClick={() => setConfirm("push")}>Push</button><button disabled={!st.remote} onClick={() => setConfirm("pull")}>Pull</button>
          </div>
          {log.length > 0 && <ul className="small" aria-label="Commit history" style={{ margin: "10px 0 0", paddingLeft: 18 }}>{log.map((l) => <li key={l.hash}><code>{l.hash}</code> {l.message} <span className="mut">· {l.author}, {l.date}</span></li>)}</ul>}
        </div>
      )}
      {confirm && (
        <Modal title={confirm === "push" ? "Push this app?" : "Pull changes?"} onClose={() => setConfirm(null)}>
          <p className="small">{confirm === "push" ? `This sends the app's committed files to ${st.remote}. Anyone who can read that repository can read them.` : `This brings changes from ${st.remote} into the app's files. The app is saved as a version first.`}</p>
          <div className="row"><button onClick={() => setConfirm(null)}>Cancel</button><button className="acc" onClick={go}>{confirm === "push" ? "Push" : "Pull"}</button></div>
        </Modal>
      )}
    </div>
  );
}
