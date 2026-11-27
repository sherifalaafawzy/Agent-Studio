"use client";
// The component gallery: every chart, map, card and layout an agent can show, drawn from its built-in example, with the spec that makes it. Paste your own spec at the
// top to see how it would look. Also the source of the documentation screenshots.
import { useMemo, useState } from "react";
import InfoTip from "@/components/InfoTip";
import UiBlock from "@/components/uikit/UiBlock";
import { KIND_LIST, SAMPLES } from "@/lib/uikit/samples.generated";

const GROUP_TITLE: Record<string, string> = { table: "Tables", chart: "Charts", timeseries: "Time series", map: "Maps", card: "Cards", layout: "Layouts", media: "Audio and video", data: "JSON and HTML" };
const ORDER = Object.keys(GROUP_TITLE);

export default function ComponentsPage() {
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("all");
  const [mine, setMine] = useState("");
  const [drawn, setDrawn] = useState<unknown>(null);
  const shown = useMemo(() => KIND_LIST.filter((k) => (group === "all" || k.group === group) && (!q.trim() || `${k.type} ${k.about}`.toLowerCase().includes(q.trim().toLowerCase()))), [q, group]);
  const draw = () => { try { setDrawn(JSON.parse(mine)); } catch { setDrawn(mine); } };
  return (
    <div data-testid="components-page">
      <div className="page-head"><h2>Components</h2><InfoTip id="ui-components" /></div>
      <p className="page-sub">Everything an agent can show in an answer: {KIND_LIST.length} kinds. Each is drawn from a typed spec; none loads anything from the internet. Ask an agent for one by name, or call <code>ui_catalog</code>.</p>
      <details className="card" style={{ marginBottom: 14 }}><summary><b>Try your own spec</b></summary>
        <textarea aria-label="Component spec (JSON)" rows={7} style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} value={mine} onChange={(e) => setMine(e.target.value)} placeholder={'{"type": "chart.bar", "x": ["A", "B"], "series": [{"name": "S", "values": [3, 5]}]}'} />
        <div className="row"><button className="acc" onClick={draw} disabled={!mine.trim()}>Draw it</button></div>
        {drawn !== null && <UiBlock spec={drawn} />}
      </details>
      <div className="gal">
        <nav className="gal-nav" aria-label="Component groups">
          <input type="search" aria-label="Search components" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className={`ghost small${group === "all" ? " on" : ""}`} aria-pressed={group === "all"} onClick={() => setGroup("all")}>All ({KIND_LIST.length})</button>
          {ORDER.map((g) => <button key={g} className={`ghost small${group === g ? " on" : ""}`} aria-pressed={group === g} onClick={() => setGroup(g)}>{GROUP_TITLE[g]} ({KIND_LIST.filter((k) => k.group === g).length})</button>)}
        </nav>
        <div>
          {shown.map((k) => (
            <section key={k.type} className="gal-item" id={k.type} aria-label={k.type}>
              <h3>{k.type}</h3>
              <div className="small mut" style={{ marginBottom: 6 }}>{k.about}</div>
              <UiBlock spec={SAMPLES[k.type]} />
              <details><summary className="small mut">Spec</summary><pre className="gal-spec">{JSON.stringify(SAMPLES[k.type], null, 2)}</pre></details>
            </section>))}
          {!shown.length && <div className="mut">No component matches that search.</div>}
        </div>
      </div>
    </div>
  );
}
