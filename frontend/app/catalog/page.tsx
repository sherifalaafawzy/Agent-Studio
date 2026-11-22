"use client";
import { useEffect, useState } from "react";
import InfoTip from "@/components/InfoTip";
import { ISearch } from "@/components/Icons";
import CatalogSkills from "@/components/CatalogSkills";
import CatalogMcp from "@/components/CatalogMcp";
import TrainerPanel from "@/components/TrainerPanel";
import CloudAccounts from "@/components/CloudAccounts";

export const DOMAINS: { id: string; label: string }[] = [
  { id: "accounting", label: "Accounting" }, { id: "sales", label: "Sales" }, { id: "marketing", label: "Marketing" }, { id: "manufacturing", label: "Manufacturing" },
  { id: "engineering", label: "Engineering" }, { id: "data", label: "Data" }, { id: "machine-learning", label: "Machine learning" }, { id: "deep-learning", label: "Deep learning" },
];
const TABS = [["skills", "Skills"], ["mcp", "MCP servers"], ["ml", "ML tools"]] as const;
type Tab = (typeof TABS)[number][0];

// Catalog: ready-made skills and vetted public MCP servers by domain, plus the local model trainer and cloud ML accounts.
export default function CatalogPage() {
  const [tab, setTab] = useState<Tab>("skills");
  const [domain, setDomain] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const fromHash = () => { const h = window.location.hash.replace("#", "") as Tab; if (TABS.some(([id]) => id === h)) setTab(h); };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);
  const pick = (t: Tab) => { setTab(t); window.history.replaceState(null, "", `#${t}`); };

  return (
    <div>
      <div className="page-head"><h2>Catalog</h2><InfoTip id="catalog" /></div>
      <p className="page-sub">Ready-made know-how and add-ons for common jobs. Nothing is added until you click, and anything that can change data starts read-only or asks you first.</p>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
        <div className="seg" role="tablist" aria-label="Catalog sections">
          {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => pick(id)}>{label}</button>)}
        </div>
        {tab !== "ml" && (
          <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 360 }}>
            <input className="search" aria-label="Search the catalog" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 32, width: "100%" }} />
            <span style={{ position: "absolute", left: 10, top: 9, color: "var(--mut)" }}><ISearch size={15} /></span></div>)}
      </div>
      {tab !== "ml" && (
        <div className="cat-chips" role="group" aria-label="Domain">
          <button className={`chip ${domain === "" ? "on" : ""}`} aria-pressed={domain === ""} onClick={() => setDomain("")}>All</button>
          {DOMAINS.map((d) => <button key={d.id} className={`chip ${domain === d.id ? "on" : ""}`} aria-pressed={domain === d.id} onClick={() => setDomain(domain === d.id ? "" : d.id)}>{d.label}</button>)}
        </div>)}
      {tab === "skills" && <CatalogSkills domain={domain} q={q} />}
      {tab === "mcp" && <CatalogMcp domain={domain} q={q} />}
      {tab === "ml" && <div style={{ display: "grid", gap: 18 }}><TrainerPanel /><CloudAccounts /></div>}
    </div>
  );
}
