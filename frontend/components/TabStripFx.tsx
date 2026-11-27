"use client";
// One behaviour for every row of tabs in the app (".tabs", role="tablist", and the step rows of the set-up guide and the agent wizard):
// the selected tab is scrolled into view. On a phone, or with 16 settings tabs, it used to sit off-screen with nothing marking it.
// It only reads the page and moves a row's own scroll position. It never writes an attribute: React owns those, and a row inside a
// part of the page that loads late would report a mismatch. (The fade at an edge with more tabs is plain CSS, in globals.css.)
import { useEffect } from "react";
import { usePathname } from "next/navigation";

const STRIPS = ".tabs, [role=tablist], .setup-chips, .wiz-steps";
const SELECTED = ".tab.on, [role=tab][aria-selected=true], button.on, button.acc, li.on";

const lastShown = new WeakMap<HTMLElement, HTMLElement>();

/** Bring the selected tab of one row into view without moving the page itself. Only when the selection changed: a row you scrolled by hand stays where you left it. */
export function revealSelected(el: HTMLElement) {
  const on = el.querySelector<HTMLElement>(SELECTED);
  if (!on || lastShown.get(el) === on) return;
  lastShown.set(el, on);
  if (el.scrollWidth <= el.clientWidth) return;
  const left = on.offsetLeft - el.offsetLeft, right = left + on.offsetWidth;
  if (left < el.scrollLeft + 24 || right > el.scrollLeft + el.clientWidth - 24) el.scrollLeft = Math.max(0, left - (el.clientWidth - on.offsetWidth) / 2);
}

export default function TabStripFx() {
  const path = usePathname();
  useEffect(() => {
    let frame = 0;
    const sweep = () => document.querySelectorAll<HTMLElement>(STRIPS).forEach(revealSelected);
    const soon = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(sweep); };
    const onClick = (e: Event) => { if ((e.target as HTMLElement | null)?.closest?.(STRIPS)) soon(); };
    const seen = new MutationObserver(soon);                            // tabs arrive after data loads, and a page can add a row later
    seen.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("click", onClick);
    soon();
    return () => { cancelAnimationFrame(frame); seen.disconnect(); document.removeEventListener("click", onClick); };
  }, [path]);
  return null;
}
