"use client";
// A small "i" button that explains a concept in plain words. Works with mouse (hover), keyboard (focus, Esc) and touch (tap).
// The card is positioned from the button but always clamped inside the screen, so it never overflows on a phone.
import { useEffect, useId, useRef, useState } from "react";
import { HELP, type Help } from "@/lib/help";

const MARGIN = 8;
const WIDTH = 288;

/** Where to put the card: under the button, shifted left/right so it stays fully on screen. Pure so it can be tested. */
export function place(button: { left: number; right: number; bottom: number; top: number }, viewport: { w: number; h: number }, card = { w: WIDTH, h: 150 }) {
  const w = Math.min(card.w, viewport.w - MARGIN * 2);
  const left = Math.max(MARGIN, Math.min(button.left, viewport.w - w - MARGIN));
  const below = button.bottom + 6 + card.h <= viewport.h || button.top - 6 - card.h < 0;
  return { left, width: w, top: below ? button.bottom + 6 : undefined, bottom: below ? undefined : viewport.h - button.top + 6 };
}

export default function InfoTip({ id, text, title }: { id?: string; text?: string; title?: string }) {
  const help: Help | null = text ? { title: title ?? "", body: text } : id && HELP[id] ? HELP[id] : null;
  const uid = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<ReturnType<typeof place> | null>(null);
  const open = hover || pinned;

  useEffect(() => {
    if (!open || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    setPos(place(r, { w: window.innerWidth, h: window.innerHeight }, { w: WIDTH, h: card.current?.offsetHeight || 150 }));
  }, [open]);

  useEffect(() => {
    if (!pinned) return;
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btn.current?.contains(t) && !card.current?.contains(t)) { setPinned(false); setHover(false); }
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { setPinned(false); setHover(false); btn.current?.focus(); } };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [pinned]);

  useEffect(() => {      // hover/focus-only cards also close on Escape
    if (!hover || pinned) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setHover(false); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [hover, pinned]);

  if (!help) return null;
  return (
    <span className="infotip" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button ref={btn} type="button" className="infotip-btn" aria-label="More information" aria-expanded={open} aria-describedby={open ? uid : undefined}
        onClick={() => setPinned((p) => !p)} onFocus={() => setHover(true)} onBlur={() => setHover(false)} />
      {open && (
        <div ref={card} id={uid} role="tooltip" className="infotip-card" style={pos ? { left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom } : { visibility: "hidden" }}>
          {help.title && <b>{help.title}</b>}
          <span>{help.body}</span>
          {help.link && <a href={help.link.href}>{help.link.label} →</a>}
        </div>
      )}
    </span>
  );
}
