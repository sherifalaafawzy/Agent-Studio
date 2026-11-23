"use client";
// Layouts that put several components together: stack, grid, tabs, carousel and split. Each child is a full component spec, cleaned and drawn on its own.
import { useState } from "react";
import { arr, clamp, num, str } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import UiBlock from "./UiBlock";

type P = { spec: Spec };
const kids = (v: unknown) => arr(v, 24);

export const Stack = ({ spec }: P) => <div className="ui-stack" data-testid="layout">{kids(spec.children).map((c, i) => <UiBlock key={i} spec={c} />)}</div>;

export function Grid({ spec }: P) {
  const cols = clamp(Math.round(num(spec.columns, 2)), 1, 4);
  return <div className="ui-grid" data-testid="layout" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>{kids(spec.children).map((c, i) => <UiBlock key={i} spec={c} />)}</div>;
}

export function Tabs({ spec }: P) {
  const tabs = arr(spec.tabs, 12), [at, setAt] = useState(0), cur = Math.min(at, tabs.length - 1);
  return (
    <div className="ui-tabs" data-testid="layout">
      <div role="tablist" aria-label={str(spec.title, 60) || "Tabs"}>{tabs.map((t, i) => <button key={i} role="tab" id={`tab-${i}`} aria-selected={cur === i} aria-controls={`panel-${i}`} tabIndex={cur === i ? 0 : -1} className={cur === i ? "acc" : "ghost"}
        onKeyDown={(e) => { if (e.key === "ArrowRight") setAt((cur + 1) % tabs.length); else if (e.key === "ArrowLeft") setAt((cur + tabs.length - 1) % tabs.length); }} onClick={() => setAt(i)}>{str(t?.label, 40) || `Tab ${i + 1}`}</button>)}</div>
      <div role="tabpanel" id={`panel-${cur}`} aria-labelledby={`tab-${cur}`}><UiBlock spec={tabs[cur]?.child} /></div>
    </div>
  );
}

export function Carousel({ spec }: P) {
  const items = kids(spec.children), [at, setAt] = useState(0), cur = Math.min(at, items.length - 1);
  return (
    <div className="ui-carousel" data-testid="layout" role="group" aria-roledescription="carousel" aria-label={str(spec.title, 60) || "Carousel"}>
      <div className="row"><button className="ghost small" aria-label="Previous" onClick={() => setAt((cur + items.length - 1) % items.length)}>‹</button><span className="mut small" aria-live="polite">{cur + 1} of {items.length}</span><button className="ghost small" aria-label="Next" onClick={() => setAt((cur + 1) % items.length)}>›</button></div>
      <UiBlock spec={items[cur]} />
    </div>
  );
}

export const Split = ({ spec }: P) => <div className="ui-split" data-testid="layout"><UiBlock spec={spec.left} /><UiBlock spec={spec.right} /></div>;
