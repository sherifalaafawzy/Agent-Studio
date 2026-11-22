"use client";
import { useEffect, useState } from "react";
import InfoTip from "@/components/InfoTip";
import RedTeamPanel from "@/components/RedTeamPanel";
import Scorecard from "@/components/Scorecard";
import SecurityActivity from "@/components/SecurityActivity";

type Tab = "scorecard" | "redteam" | "activity";
const TABS: [Tab, string][] = [["scorecard", "Scorecard"], ["redteam", "Red team"], ["activity", "Activity"]];

export default function SecurityPage() {
  const [tab, setTab] = useState<Tab>("scorecard");
  const [agent, setAgent] = useState("");                         // /security/?agent=<id> opens that agent's own scorecard
  useEffect(() => {
    const h = window.location.hash.slice(1) as Tab; if (TABS.some(([k]) => k === h)) setTab(h);
    setAgent(new URLSearchParams(window.location.search).get("agent") ?? "");
  }, []);
  const go = (t: Tab) => { setTab(t); history.replaceState(null, "", `#${t}`); };
  return (
    <div>
      <div className="page-head"><h2>Security</h2><InfoTip id="security-score" /><span className="grow" /><a className="small" href="/settings/#security">Guardrails and identity settings →</a></div>
      <p className="page-sub">See how your setup lines up with the OWASP lists of AI risks, attack your own agents on purpose, and review what the safety checks caught.</p>
      <div className="tabs">{TABS.map(([k, l]) => <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => go(k)}>{l}</button>)}</div>
      {tab === "scorecard" && <Scorecard initialAgent={agent} />}
      {tab === "redteam" && <RedTeamPanel />}
      {tab === "activity" && <SecurityActivity />}
    </div>
  );
}
