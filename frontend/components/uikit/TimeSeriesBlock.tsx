"use client";
// The `timeseries` component: zoom and pan, range buttons, resampling, a moving average, indexing to 100, log scale, annotations, thresholds, unusual points and a forecast
// with its uncertainty band. Drawn by Apache ECharts (loaded only when a time series appears). The picture is never the only way in: a statistics table and the aligned data
// are one click away, and a screen reader gets a sentence.
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";
import { downloadFile, fileSafe, toCsv } from "@/lib/tables";
import { downloadDataUrl } from "@/lib/uikit/exporters";
import type { Spec } from "@/lib/uikit/safe";
import { alignedTable, buildOption, canLog, DEFAULT_VIEW, rangeFor, readModel, statsTable, summarize, viewSeries, type AggFn, type Resample } from "@/lib/uikit/timeseries";
import UiFrame from "./UiFrame";
import useColors from "./useColors";

echarts.use([LineChart, BarChart, ScatterChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);

const RANGES = ["1W", "1M", "3M", "6M", "1Y"];

export default function TimeSeriesBlock({ spec }: { spec: Spec }) {
  const model = useMemo(() => readModel(spec), [spec]);
  const [view, setView] = useState(DEFAULT_VIEW);
  const [utc, setUtc] = useState(false);
  const colors = useColors();
  const box = useRef<HTMLDivElement>(null), chart = useRef<echarts.ECharts | null>(null);
  const option = useMemo(() => buildOption(model, view, colors, utc), [model, view, colors, utc]);
  const summary = useMemo(() => summarize(model), [model]);
  const title = model.title || "Time series";

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const c = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => c.resize()) : null;
    ro?.observe(el);
    return () => { ro?.disconnect(); c.dispose(); chart.current = null; };
  }, [utc]);
  useEffect(() => { chart.current?.setOption(option, true); }, [option, utc]);

  const zoom = (key: string) => {
    const r = key === "All" ? null : rangeFor(model, key);
    chart.current?.dispatchAction(r ? { type: "dataZoom", startValue: r[0], endValue: r[1] } : { type: "dataZoom", start: 0, end: 100 });
  };
  const set = (patch: Partial<typeof view>) => setView((v) => ({ ...v, ...patch }));
  const shown = viewSeries(model, view), big = model.series.some((s) => s.points.length > 2000);
  const file = fileSafe(title, "time-series");
  const toolbar = (
    <>
      <span className="ts-ranges" role="group" aria-label="Range">{[...RANGES, "All"].map((k) => <button key={k} className="ghost small" onClick={() => zoom(k)}>{k}</button>)}</span>
      <label>Group by <select aria-label="Group by" value={view.resample} onChange={(e) => set({ resample: e.target.value as Resample })}>{["none", "hour", "day", "week", "month"].map((x) => <option key={x} value={x}>{x === "none" ? "no grouping" : x}</option>)}</select></label>
      <label>Combine with <select aria-label="Combine with" value={view.fn} disabled={view.resample === "none"} onChange={(e) => set({ fn: e.target.value as AggFn })}>{["mean", "sum", "last", "min", "max", "count"].map((x) => <option key={x}>{x}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={view.ma} onChange={(e) => set({ ma: e.target.checked })} /> 7-point average</label>
      <label className="check"><input type="checkbox" checked={view.index} onChange={(e) => set({ index: e.target.checked })} /> Index to 100</label>
      <label className="check"><input type="checkbox" checked={view.log && canLog(model)} disabled={!canLog(model)} onChange={(e) => set({ log: e.target.checked })} /> Log scale</label>
      <label>Time zone <select aria-label="Time zone" value={utc ? "utc" : "local"} onChange={(e) => setUtc(e.target.value === "utc")}><option value="local">Local</option><option value="utc">UTC</option></select></label>
    </>
  );
  return (
    <UiFrame spec={spec} title={title} summary={summary} table={statsTable(title, shown)} toolbar={toolbar} testId="timeseries"
      foot={<>{big && <span className="mut">Long series are thinned for speed (largest-triangle sampling): zoom in for every point.</span>}{model.forecast && <span className="mut">Dashed line: forecast{model.forecast.lo.length ? `; shaded: ${model.forecast.loKey} to ${model.forecast.hiKey} quantile range` : ""}.</span>}</>}
      exporters={[
        { label: "Data, all series (CSV)", run: () => { const t = alignedTable(title, shown); downloadFile(`${file}-data.csv`, toCsv(t.columns, t.rows), "text/csv;charset=utf-8"); } },
        { label: "Picture (PNG)", run: () => { const url = chart.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: colors.panel }); if (url) downloadDataUrl(`${file}.png`, url); } },
      ]}>
      <div ref={box} className="ts-box" role="img" aria-label={summary} />
    </UiFrame>
  );
}
