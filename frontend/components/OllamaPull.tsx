"use client";
// Download (pull) models into the local Ollama from the UI, with live progress.
import { useEffect, useState } from "react";
import { api, streamSse } from "@/lib/api";
import { forgetModels, loadModels } from "./ModelSelect";

type Sugg = { id: string; note: string };

export default function OllamaPull({ pid, onPulled }: { pid: string; onPulled: () => void }) {
  const [sugg, setSugg] = useState<Sugg[]>([]);
  const [installed, setInstalled] = useState<string[] | null>(null);
  const [listErr, setListErr] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState<{ status?: string; completed?: number; total?: number } | null>(null);
  const [msg, setMsg] = useState("");
  const refresh = () => loadModels(pid, true).then((r) => { setInstalled(r.models.map((m) => m.id)); setListErr(r.error ?? ""); }).catch((e) => setListErr(e.message));
  useEffect(() => { api<Sugg[]>(`/api/providers/${pid}/ollama/suggestions`).then(setSugg).catch(() => {}); refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pid]);

  async function pull(model: string) {
    setBusy(true); setMsg(""); setProg({ status: "starting" });
    try {
      await streamSse(`/api/providers/${pid}/pull`, { model }, (e) => {
        if (e.type === "progress") setProg(e);
        else if (e.type === "error") setMsg("⚠ " + e.message);
        else if (e.type === "done") { setMsg(`Installed ${e.model}. Pick it in the model lists above.`); forgetModels(pid); refresh(); onPulled(); }
      });
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
    setBusy(false); setProg(null);
  }
  const pct = prog?.total ? Math.round(((prog.completed ?? 0) / prog.total) * 100) : null;
  const gb = (n?: number) => (n ? (n / 1e9).toFixed(2) + " GB" : "");
  return (
    <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: 4 }}>
      <div className="row"><b>Models on this PC</b><span className="mut small">{installed === null ? "checking…" : installed.length ? installed.join(", ") : "none yet"}</span></div>
      {listErr && <div className="small" style={{ color: "var(--warn)", marginTop: 4 }}>{listErr}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <input value={name} placeholder="model name, e.g. qwen2.5-coder:7b" onChange={(e) => setName(e.target.value)} style={{ flex: 1, minWidth: 220 }} disabled={busy} />
        <button className="acc" disabled={busy || !name.trim()} onClick={() => pull(name.trim())}>{busy ? "Downloading…" : "Download a model"}</button>
      </div>
      <div className="row small" style={{ marginTop: 8, gap: 6 }}>
        {sugg.map((s) => <button key={s.id} className="chip" disabled={busy || installed?.includes(s.id)} title={s.note} onClick={() => { setName(s.id); pull(s.id); }}>
          {s.id}{installed?.includes(s.id) ? " ✓" : ""}</button>)}
      </div>
      <div className="small mut" style={{ marginTop: 6 }}>For agents, choose a model that supports tool calls (qwen2.5-coder, qwen3, llama3.1/3.2, mistral). Browse more at ollama.com/library. Downloads are large; they stay on this PC.</div>
      {prog && <div style={{ marginTop: 8 }}>
        <div className="small mut">{prog.status}{pct !== null ? ` · ${pct}% (${gb(prog.completed)} of ${gb(prog.total)})` : ""}</div>
        {pct !== null && <div className="bar" style={{ marginTop: 4 }}><i style={{ width: `${pct}%` }} /></div>}
      </div>}
      {msg && <div className="small" style={{ marginTop: 6 }}>{msg}</div>}
    </div>
  );
}
