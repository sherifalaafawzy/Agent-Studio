"use client";
// The palette as real colours. A canvas (ECharts, MapLibre) cannot read CSS variables, so they are resolved here and refreshed when the theme changes.
import { useEffect, useState } from "react";
import type { Colors } from "@/lib/uikit/timeseries";

const FALLBACK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6", "#008300", "#e34948"];

export function readColors(): Colors {
  if (typeof document === "undefined") return { series: FALLBACK, fg: "#555", grid: "#ddd", axis: "#bbb", panel: "#fff", other: "#999" };      // server render: no styles to read
  const cs = getComputedStyle(document.documentElement), g = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return { series: FALLBACK.map((d, i) => g(`--viz-${i + 1}`, d)), fg: g("--fg2", "#555"), grid: g("--viz-grid", "#ddd"), axis: g("--viz-axis", "#bbb"), panel: g("--panel", "#fff"), other: g("--viz-other", "#999") };
}

export default function useColors(): Colors {
  const [c, setC] = useState<Colors>(readColors);
  useEffect(() => {
    const update = () => setC(readColors());
    const mo = new MutationObserver(update); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)"); mq?.addEventListener?.("change", update);
    return () => { mo.disconnect(); mq?.removeEventListener?.("change", update); };
  }, []);
  return c;
}
