"use client";
// Workflows: three tabs. "Flows" lists your workflows (and starts a new one); "Runs" lists every run of every workflow; "OpenAPI steps" holds the APIs you imported and their endpoints.
// Editing a workflow happens full screen in /workflows/edit/.
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import InfoTip from "@/components/InfoTip";
import FlowsTab from "@/components/workflows/FlowsTab";
import OpenApiTab from "@/components/workflows/OpenApiTab";
import RunsTab from "@/components/workflows/RunsTab";

function Tabs() {
  const router = useRouter();
  const t = useSearchParams().get("tab");
  const tab = t === "openapi" ? "openapi" : t === "runs" ? "runs" : "flows";
  return (
    <div data-testid="workflows-page">
      <div className="page-head"><h2>Workflows</h2><InfoTip id="workflows" /></div>
      <p className="page-sub">Draw a process of agents, tools and API calls. Build it box by box, or describe the goal and let an agent propose it for you to adjust. You choose how much it asks before acting.</p>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "flows"} className={`tab ${tab === "flows" ? "on" : ""}`} onClick={() => router.replace("/workflows/")}>Flows</button>
        <button role="tab" aria-selected={tab === "runs"} className={`tab ${tab === "runs" ? "on" : ""}`} onClick={() => router.replace("/workflows/?tab=runs")}>Runs</button>
        <button role="tab" aria-selected={tab === "openapi"} className={`tab ${tab === "openapi" ? "on" : ""}`} onClick={() => router.replace("/workflows/?tab=openapi")}>OpenAPI steps</button>
      </div>
      {tab === "flows" ? <FlowsTab /> : tab === "runs" ? <RunsTab /> : <OpenApiTab />}
    </div>
  );
}

export default function WorkflowsPage() {
  return <Suspense fallback={null}><Tabs /></Suspense>;
}
