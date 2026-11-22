"use client";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { TOURS, firstRunDone, markFirstRunDone, normPath, readState, slice, writeState } from "@/lib/tour";

const EVENT = "as:tour-start";

/** True while the set-up guide gate is waiting for the server (see OnboardingGate). */
function gatePending(): boolean {
  try { return sessionStorage.getItem("as-gate-pending") === "1"; } catch { return false; }
}

/** Start a guided tour from anywhere (Settings, the command palette, the side agent). */
export function startTour(tour: string) {
  if (!TOURS[tour]) return;
  writeState({ tour, index: 0 });
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { tour } }));
}

/**
 * Runs the current tour step by step on whichever page it is. Mounted once in the root layout.
 * - A first-time visitor gets the welcome tour on the home page (once; "Skip" or finishing marks it done).
 * - Tours that span pages continue on the next page: the runner saves progress, then navigates.
 */
export default function TourRunner() {
  const path = normPath(usePathname() ?? "/");
  const router = useRouter();
  const active = useRef<Driver | null>(null);

  useEffect(() => {
    const run = () => {
      active.current?.destroy();
      const st = readState();
      if (!st) return;
      const { steps, next, nextPage } = slice(st.tour, st.index, path);
      if (steps.length === 0) {                       // the tour expects another page: go there
        const want = TOURS[st.tour].steps[st.index];
        if (want) router.push(want.page);
        return;
      }
      const hop = (d: Driver) => {
        d.destroy();
        if (next === null || !nextPage) {              // last step of the last page
          writeState(null);
          markFirstRunDone();
          return;
        }
        writeState({ tour: st.tour, index: next });
        router.push(nextPage);
      };
      const d: Driver = driver({
        showProgress: true,
        allowClose: true,
        smoothScroll: true,
        stagePadding: 6,
        stageRadius: 12,
        popoverClass: "as-tour-pop",
        steps: steps.map((s, k) => {
          const last = k === steps.length - 1;
          return {
            element: s.element ? `[data-tour="${s.element}"]` : undefined,
            skipMissingElement: true,
            waitForElement: 2500,
            popover: {
              title: s.title,
              description: s.body,
              side: s.side,
              align: "center" as const,
              ...(last ? { onNextClick: () => hop(d), onDoneClick: () => hop(d) } : {}),
            },
          };
        }),
        onCloseClick: () => {
          d.destroy();
          writeState(null);
          markFirstRunDone();
        },
      });
      active.current = d;
      d.drive();
    };

    run();
    const onStart = () => run();
    window.addEventListener(EVENT, onStart);
    // First visit: offer the welcome tour once the home page has painted.
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The set-up guide gate may still be asking the server whether this is a brand-new install. If so, wait for its answer
    // (it claims the first run when it opens the guide) instead of starting the tour over the top of it.
    const offer = (tries: number) => {
      if (firstRunDone() || readState()) return;
      if (gatePending() && tries < 20) { timer = setTimeout(() => offer(tries + 1), 500); return; }
      startTour("welcome");
    };
    if (path === "/" && !firstRunDone() && !readState()) timer = setTimeout(() => offer(0), 900);
    return () => {
      window.removeEventListener(EVENT, onStart);
      if (timer) clearTimeout(timer);
      active.current?.destroy();
      active.current = null;
    };
  }, [path, router]);

  return null;
}
