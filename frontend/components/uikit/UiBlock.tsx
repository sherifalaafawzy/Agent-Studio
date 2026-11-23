"use client";
// Draws one component from its spec. The spec is cleaned again here, drawn by a fixed component for its type, and if anything is wrong the data is shown as a table or
// as JSON with the reason, never as a blank card. Heavy pieces (maps, advanced charts, time series) load only when one is on screen.
import { Component, ReactNode, Suspense, useMemo } from "react";
import { tableFromAnything } from "@/lib/uikit/fallback";
import { normalize } from "@/lib/uikit/safe";
import DataTable from "../rich/DataTable";
import JsonViewer from "../rich/JsonViewer";
import { REGISTRY } from "./registry";

export function UiFallback({ raw, error }: { raw: unknown; error: string }) {
  const table = tableFromAnything(raw);
  return (
    <div className="ui-fallback" role="note" data-testid="ui-fallback">
      <div className="small mut">Could not draw this component: {error}. {table ? "Its data is shown as a table." : "Its data is shown as JSON."}</div>
      {table ? <DataTable table={table} /> : <JsonViewer value={typeof raw === "string" ? raw.slice(0, 20000) : raw} />}
    </div>
  );
}

class Boundary extends Component<{ raw: unknown; children: ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(e: Error) { return { error: e.message || "it failed to draw" }; }
  render() { return this.state.error ? <UiFallback raw={this.props.raw} error={this.state.error} /> : this.props.children; }
}

export default function UiBlock({ spec: raw }: { spec: unknown }) {
  const { spec, error } = useMemo(() => normalize(raw), [raw]);
  if (!spec) return <UiFallback raw={raw} error={error ?? "invalid"} />;
  const Draw = REGISTRY[spec.type];
  if (!Draw) return <UiFallback raw={raw} error={`there is no drawing for ${spec.type}`} />;
  return (
    <Boundary raw={raw}>
      <Suspense fallback={<div className="ui-loading small mut" role="status">Loading {spec.type}…</div>}><Draw spec={spec} /></Suspense>
    </Boundary>
  );
}
