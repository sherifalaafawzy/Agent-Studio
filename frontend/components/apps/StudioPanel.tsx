"use client";
// The Build view: the builder chat on one side and the live preview on the other. Click "Pick an element" in the preview, click something on the page and say what should
// change about it: the builder is told exactly which element you mean. On a phone the two sides are tabs (both stay loaded, so the preview keeps checking itself).
import { useState } from "react";
import { AppRow, builderMessage, elementLabel, PickedEl, pickedMessage } from "@/lib/apps";
import BuilderPanel, { BuilderRequest, BuildStart } from "@/components/apps/BuilderPanel";
import PreviewPanel from "@/components/apps/PreviewPanel";

type Props = { app: AppRow; tick: number; consoleTick: number; onChanged: () => void; start: BuildStart | null; onStarted: () => void; request: BuilderRequest | null; onAsk: (text: string, label?: string) => void };

export default function StudioPanel({ app, tick, consoleTick, onChanged, start, onStarted, request, onAsk }: Props) {
  const [pane, setPane] = useState<"chat" | "preview">("chat");
  const [picked, setPicked] = useState<PickedEl | null>(null);
  const [change, setChange] = useState("");
  const send = () => { if (!picked || !change.trim()) return; onAsk(pickedMessage(app, picked, change), `${elementLabel(picked)}: ${change.trim()}`); setPicked(null); setChange(""); setPane("chat"); };
  return (
    <div className="studio" data-testid="studio-panel" data-pane={pane}>
      <div className="studio-switch" role="tablist" aria-label="Chat or preview">
        <button role="tab" aria-selected={pane === "chat"} className={`tab ${pane === "chat" ? "on" : ""}`} onClick={() => setPane("chat")}>Chat</button>
        <button role="tab" aria-selected={pane === "preview"} className={`tab ${pane === "preview" ? "on" : ""}`} onClick={() => setPane("preview")}>Preview</button>
      </div>
      <div className="studio-chat"><BuilderPanel app={app} onChanged={onChanged} start={start} onStarted={onStarted} request={request} refreshKey={tick} /></div>
      <div className="studio-preview">
        {picked && (
          <div className="picked card" data-testid="picked-bar">
            <div className="row small"><b>Selected</b> <code>{elementLabel(picked)}</code><span className="mut"> in {picked.page}</span><span className="grow" /><button className="chip small" onClick={() => setPicked(null)}>Cancel</button></div>
            <div className="row" style={{ marginTop: 6, alignItems: "flex-end" }}>
              <textarea className="grow" rows={2} value={change} aria-label="What should change about this element?" placeholder="Make it bigger, change the colour, say something else…" onChange={(e) => setChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <button className="acc" disabled={!change.trim()} onClick={send}>Change this</button>
            </div>
          </div>
        )}
        <PreviewPanel app={app} refreshKey={tick} consoleKey={consoleTick} onPicked={(el) => { setPicked(el); setPane("preview"); }} onFix={(report) => onAsk(builderMessage(app, `The preview reported these problems. Fix exactly these with small patches, then run app_verify:\n${report}`), "Fix the problems the preview reported")} />
      </div>
    </div>
  );
}
