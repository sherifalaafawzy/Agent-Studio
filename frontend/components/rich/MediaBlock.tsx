"use client";
// An audio or video player for a file an agent saved. The source is always one of Agent Studio's own artifacts (served with nosniff and a sandboxing policy): a block
// that names anything else is not played, because a remote address is a way for an answer to send your data somewhere.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Artifact, artifactUrl, fmtBytes } from "@/lib/artifacts";
import { IDownload } from "../Icons";
import BlockActions from "./BlockActions";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const PLAYABLE = ["audio/mpeg", "audio/wav", "audio/ogg", "audio/flac", "audio/mp4", "video/mp4", "video/quicktime", "video/webm"];
export const ID12 = /^[0-9a-f]{12}$/;

export function parseMedia(text: string): { media?: { artifact: string; title: string; captions: string | null }; error?: string } {
  const body = text.trim();
  let raw: any = ID12.test(body) ? { artifact: body } : null;
  if (!raw) { try { raw = JSON.parse(body); } catch { return { error: "give the id of a saved file, or JSON with an artifact id" }; } }
  if (!raw || typeof raw !== "object" || !ID12.test(String(raw.artifact ?? ""))) return { error: "only files saved as artifacts can be played (the 12-character id)" };
  const cap = String(raw.captions ?? "");
  return { media: { artifact: raw.artifact, title: String(raw.title ?? "").slice(0, 100), captions: ID12.test(cap) ? cap : null } };
}

export default function MediaBlock({ kind, artifact, title = "", captions = null, plain = false }: { kind: "audio" | "video"; artifact: string; title?: string; captions?: string | null; plain?: boolean }) {
  const [meta, setMeta] = useState<Artifact | null>(null);
  const [err, setErr] = useState("");
  const [speed, setSpeed] = useState(1);
  const el = useRef<HTMLMediaElement | null>(null);
  useEffect(() => { setMeta(null); setErr(""); api<Artifact>(`/api/artifacts/${artifact}`).then(setMeta).catch((e) => setErr((e as Error).message)); }, [artifact]);
  useEffect(() => { if (el.current) el.current.playbackRate = speed; }, [speed, meta]);
  const label = title || meta?.name || `${kind} file`;
  if (err) return <div className="small mut" role="note">Could not load this {kind}: {err}</div>;
  if (!meta) return <div className="small mut" role="status">Loading the {kind}…</div>;
  const ok = meta.type === kind && PLAYABLE.includes(meta.mime);
  return (
    <figure className="media-block" data-testid={`${kind}-block`}>
      <figcaption><b>{label}</b> <span className="mut small">· {fmtBytes(meta.size)}</span></figcaption>
      {!ok
        ? <div className="small mut" role="note">This file ({meta.mime}) cannot be played in the browser. Download it to open it.</div>
        : kind === "audio"
          ? <audio ref={el as React.RefObject<HTMLAudioElement>} controls preload="metadata" src={artifactUrl(artifact)} aria-label={label} />
          : <video ref={el as React.RefObject<HTMLVideoElement>} controls preload="metadata" src={artifactUrl(artifact)} aria-label={label} playsInline {...(captions ? { crossOrigin: "anonymous" as const } : {})}>
            {captions && <track kind="captions" src={artifactUrl(captions)} srcLang="en" label="Captions" default />}</video>}
      <div className="dt-foot small">
        <span className="row">
          {ok && <label>Speed <select aria-label="Playback speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>}
          <a className="ghost small btn" href={artifactUrl(artifact, true)} download={meta.name} aria-label={`Download ${meta.name}`}><IDownload size={12} /> Download</a>
        </span>
        {!plain && <BlockActions label={kind} name={meta.name} artifactId={artifact} />}
      </div>
    </figure>
  );
}
