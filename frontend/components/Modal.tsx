"use client";
// A plain modal dialog and a "are you sure?" dialog, shared by the workflow screens.
import { useEffect, type ReactNode } from "react";
import { IX } from "@/components/Icons";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" style={wide ? { width: "min(960px,100%)" } : undefined} onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="row"><h3 style={{ margin: 0 }}>{title}</h3><span className="grow" /><button className="icon-btn" aria-label="Close" onClick={onClose}><IX size={16} /></button></div>
        {children}
      </div>
    </div>
  );
}

/** Asks before something is removed. `usedBy` lists what depends on it; when it is not empty the button says "Delete anyway". */
export function ConfirmDelete({ what, usedBy, onConfirm, onClose }: { what: string; usedBy?: string[]; onConfirm: () => void; onClose: () => void }) {
  const used = usedBy ?? [];
  return (
    <Modal title={`Delete ${what}?`} onClose={onClose}>
      <p className="small">This cannot be undone.</p>
      {used.length > 0 && <p className="small" role="alert" style={{ color: "var(--warn-text)" }}>It is used by: {used.join(", ")}. Those workflows will show a problem until you pick something else.</p>}
      <div className="row" style={{ marginTop: 10 }}><button onClick={onClose}>Cancel</button><button className="pri" onClick={onConfirm}>{used.length ? "Delete anyway" : "Delete"}</button></div>
    </Modal>
  );
}
