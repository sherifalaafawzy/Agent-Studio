import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Under a full-suite run many jsdom workers compete for CPU; the default 1s waitFor/findBy timeout is too tight.
configure({ asyncUtilTimeout: 8000 });

// jsdom does not implement these browser APIs; the app calls them.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  try { localStorage.clear(); } catch { /* storage may be unavailable */ }
  document.body.className = "";
  document.documentElement.removeAttribute("data-theme");
});
