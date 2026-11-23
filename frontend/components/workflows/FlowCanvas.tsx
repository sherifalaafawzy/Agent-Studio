"use client";
// The canvas of the workflow editor (React Flow). It only draws and edits the graph; the editor owns the data, the history and the run.
import { useCallback, useMemo, useRef, useState } from "react";
import { addEdge, applyEdgeChanges, applyNodeChanges, Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider, useReactFlow, type Connection, type Edge, type EdgeChange, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ENTRY, portsOf, ROOTS, summarise, type NodeSpec, type WEdge, type WNode } from "@/lib/workflows";

const STATUS_TEXT: Record<string, string> = { pending: "waiting", waiting_approval: "needs your OK", running: "running…", done: "done", error: "failed", skipped: "skipped", stopped: "stopped" };
const STATUS_ICON: Record<string, string> = { done: "✓", error: "✕", running: "…", waiting_approval: "?", skipped: "–", stopped: "■", pending: "·" };
const GROUP_VAR: Record<string, string> = { trigger: "var(--ok)", ai: "var(--acc)", flow: "var(--warn)", data: "var(--fg2)", integration: "var(--acc)", output: "var(--ok)", canvas: "var(--mut)" };

type Data = { label: string; kind: string; group: string; typeLabel: string; summary: string; ports: string[]; status?: string; problem?: string; pinned?: boolean; disabled?: boolean; note?: string; waiting?: boolean };

function WfNode({ data, selected }: NodeProps<Node<Data>>) {
  if (data.kind === "note") return <div className={`wf-note ${selected ? "sel" : ""}`} data-testid="wf-node-note">{data.note || "Note"}</div>;
  return (
    <div className={`wf-node wf-${data.kind} ${selected ? "sel" : ""} ${data.status ? `st-${data.status}` : ""} ${data.disabled ? "off" : ""}`} data-testid={`wf-node-${data.kind}`} style={{ borderLeft: `4px solid ${GROUP_VAR[data.group] ?? "var(--line)"}` }}>
      {!ROOTS.includes(data.kind) && <Handle type="target" position={Position.Left} id="in" />}
      <div className="wf-head"><span>{data.typeLabel}</span>
        {data.pinned && <span className="wf-tag" title="Output is pinned: this box is not run">pinned</span>}
        {data.status && <span className="wf-status" title={STATUS_TEXT[data.status]}><span aria-hidden="true">{STATUS_ICON[data.status]}</span> {STATUS_TEXT[data.status] ?? data.status}</span>}</div>
      <div className="wf-title">{data.label}</div>
      <div className="wf-sum">{data.summary}</div>
      {data.problem && <div className="wf-problem" title={data.problem}>⚠ {data.problem}</div>}
      {data.ports.length > 1 && <div className="wf-ports" aria-hidden="true">{data.ports.map((p) => <span key={p}>{p}</span>)}</div>}
      {data.ports.map((p, i) => <Handle key={p} type="source" position={Position.Right} id={p} style={{ top: data.ports.length === 1 ? "50%" : `${((i + 1) / (data.ports.length + 1)) * 100}%` }} />)}
    </div>
  );
}
const nodeTypes = { wf: WfNode };

type Props = {
  nodes: WNode[]; edges: WEdge[]; specs: Record<string, NodeSpec>; selected: string | null; statuses?: Record<string, string>; problems?: Record<string, string>;
  waiting?: Set<string>; multi?: string[]; readOnly?: boolean; onSelect: (id: string | null) => void; onSelectMany?: (ids: string[]) => void; onChange: (nodes: WNode[], edges: WEdge[], commit: boolean) => void; onDropNode?: (type: string, pos: { x: number; y: number }) => void;
};

function Inner({ nodes, edges, specs, selected, multi, statuses, problems, readOnly, onSelect, onSelectMany, onChange, onDropNode }: Props) {
  const rf = useReactFlow();
  const picked = useRef<Set<string>>(new Set());                                // every box that is selected right now (shift-click or a box dragged around several)
  const [dims, setDims] = useState<Record<string, { width: number; height: number }>>({});     // measured box sizes: the minimap needs them to draw anything
  const rfNodes: Node<Data>[] = useMemo(() => nodes.map((n) => {
    const spec = specs[n.type];
    return { id: n.id, type: "wf", position: n.position, initialWidth: 220, initialHeight: 72, ...(dims[n.id] ? { measured: dims[n.id] } : {}), selected: multi && multi.length > 1 ? multi.includes(n.id) : n.id === selected, draggable: !readOnly,
      data: { label: n.label, kind: n.type, group: spec?.group ?? "flow", typeLabel: spec?.label ?? n.type, summary: summarise(n), ports: portsOf(n, spec), status: statuses?.[n.id], problem: problems?.[n.id],
        pinned: n.settings?.pin != null, disabled: !!n.settings?.disabled, note: n.type === "note" ? n.config?.text : undefined } };
  }), [nodes, specs, selected, multi, statuses, problems, dims]);
  const rfEdges: Edge[] = useMemo(() => edges.map((e) => ({ id: e.id ?? `${e.source}-${e.target}${e.source_port ? `-${e.source_port}` : ""}`, source: e.source, target: e.target, sourceHandle: e.source_port ?? "out",
    targetHandle: "in", label: e.source_port && e.source_port !== "out" ? e.source_port : undefined, animated: statuses?.[e.source] === "running" })), [edges, statuses]);
  const toWEdges = (es: Edge[]): WEdge[] => es.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle && e.sourceHandle !== "out" ? { source_port: e.sourceHandle } : {}) }));
  const onNodesChange = useCallback((ch: NodeChange[]) => {
    const moved = applyNodeChanges(ch, rfNodes);
    const sized = ch.filter((c) => c.type === "dimensions" && (c as { dimensions?: unknown }).dimensions) as unknown as { id: string; dimensions: { width: number; height: number } }[];
    if (sized.length) setDims((d) => { const nx = { ...d }; let changed = false; for (const c of sized) { const o = d[c.id]; if (!o || o.width !== c.dimensions.width || o.height !== c.dimensions.height) { nx[c.id] = c.dimensions; changed = true; } } return changed ? nx : d; });
    const removed = new Set(ch.filter((c) => c.type === "remove").map((c) => (c as { id: string }).id).filter((id) => !ENTRY.includes(nodes.find((n) => n.id === id)?.type ?? "")));          // the starting box cannot be deleted
    const sels = ch.filter((c) => c.type === "select") as { id: string; selected?: boolean }[];
    if (sels.length) {
      for (const c of sels) { if (c.selected) picked.current.add(c.id); else picked.current.delete(c.id); }
      onSelectMany?.(Array.from(picked.current));
    }
    const sel = sels.find((c) => c.selected);
    if (sel) onSelect(sel.id);
    const moving = ch.filter((c) => c.type === "position") as { dragging?: boolean }[];
    if (removed.size || moving.length) {
      const next = nodes.filter((n) => !removed.has(n.id)).map((n) => ({ ...n, position: moved.find((m) => m.id === n.id)?.position ?? n.position }));
      onChange(next, edges.filter((e) => !removed.has(e.source) && !removed.has(e.target)), removed.size > 0 || moving.some((m) => m.dragging === false));
    }
  }, [nodes, edges, rfNodes, onChange, onSelect]);
  const onEdgesChange = useCallback((ch: EdgeChange[]) => {
    if (ch.some((c) => c.type === "remove")) onChange(nodes, toWEdges(applyEdgeChanges(ch, rfEdges)), true);
  }, [nodes, rfEdges, onChange]);
  const onConnect = useCallback((c: Connection) => {
    if (!c.source || !c.target || c.source === c.target || ROOTS.includes(nodes.find((n) => n.id === c.target)?.type ?? "")) return;
    onChange(nodes, toWEdges(addEdge({ ...c, id: `${c.source}-${c.target}-${c.sourceHandle ?? "out"}` }, rfEdges)), true);
  }, [nodes, rfEdges, onChange]);
  return (
    <div className="wf-canvas-full" data-testid="wf-canvas"
      onDragOver={(e) => { if (e.dataTransfer?.types?.includes("application/x-wf-node")) e.preventDefault(); }}
      onDrop={(e) => { const t = e.dataTransfer?.getData("application/x-wf-node"); if (t && onDropNode) { e.preventDefault(); onDropNode(t, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY })); } }}>
      <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onPaneClick={() => { picked.current.clear(); onSelectMany?.([]); onSelect(null); }}
        fitView snapToGrid snapGrid={[10, 10]} deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]} nodesConnectable={!readOnly} multiSelectionKeyCode="Shift" minZoom={0.2} proOptions={{ hideAttribution: false }}>
        <Background gap={20} /><Controls showInteractive={false} />{!readOnly && <MiniMap pannable zoomable ariaLabel="Overview of the workflow" className="wf-minimap" />}
      </ReactFlow>
    </div>
  );
}

export default function FlowCanvas(props: Props) {
  return <ReactFlowProvider><Inner {...props} /></ReactFlowProvider>;
}
