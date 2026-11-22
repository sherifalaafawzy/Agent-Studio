"use client";
// Unsaved-changes guard: a form registers itself as "dirty" while it holds edits that are not saved yet. Closing the tab or
// reloading asks first, and the Settings page asks before switching away from a tab with unsaved edits.
import { useEffect } from "react";

const dirty = new Set<string>();
let listening = false;

function warn(e: BeforeUnloadEvent) {                    // only registered while something is dirty (see setDirty)
  e.preventDefault();
  e.returnValue = "";
}

export function setDirty(id: string, value: boolean) {
  if (value) dirty.add(id); else dirty.delete(id);
  if (dirty.size > 0 && !listening) { window.addEventListener("beforeunload", warn); listening = true; }
  if (dirty.size === 0 && listening) { window.removeEventListener("beforeunload", warn); listening = false; }
}

export const anyDirty = () => dirty.size > 0;
export const dirtyIds = () => Array.from(dirty);

/** Mark `id` dirty while `value` is true (and clean again when the form unmounts). */
export function useDirty(id: string, value: boolean) {
  useEffect(() => { setDirty(id, value); return () => setDirty(id, false); }, [id, value]);
}

/** Ask before throwing away unsaved edits. Returns true when it is fine to go on. */
export function confirmDiscard(): boolean {
  return !anyDirty() || window.confirm("You have unsaved changes. Leave without saving?");
}
