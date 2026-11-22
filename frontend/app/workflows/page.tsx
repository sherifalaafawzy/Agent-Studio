"use client";
// Workflows: two tabs. "Flows" lists your workflows (and starts a new one); "OpenAPI steps" holds the APIs you imported and their endpoints.
// Editing a workflow happens full screen in /workflows/edit/.
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import InfoTip from "@/components/InfoTip";
import FlowsTab from "@/components/workflows/FlowsTab";
import OpenApiTab from "@/components/workflows/OpenApiTab";

function Tabs() {
  const router = useRouter();
  const tab = useSearchParams().get("tab") === "openapi" ? "openapi" : "flows";
  return (
    <div data-testid="workflows-page">
      <div className="page-head"><h2>Workflows</h2><InfoTip id="workflows" /></div>
      <p className="page-sub">Draw a process of agents, tools and API calls. Build it box by box, or describe the goal and let an agent propose it for you to adjust. You choose how much it asks before acting.</p>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "flows"} className={`tab ${tab === "flows" ? "on" : ""}`} onClick={() => router.replace("/workflows/")}>Flows</button>
        <button role="tab" aria-selected={tab === "openapi"} className={`tab ${tab === "openapi" ? "on" : ""}`} onClick={() => router.replace("/workflows/?tab=openapi")}>OpenAPI steps</button>
      </div>
      {tab === "flows" ? <FlowsTab /> : <OpenApiTab />}
    </div>
  );
}

export default function WorkflowsPage() {
  return <Suspense fallback={null}><Tabs /></Suspense>;
}
