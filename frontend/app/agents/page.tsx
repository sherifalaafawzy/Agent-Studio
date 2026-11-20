export default function AgentsPage() {
  return (
    <div>
      <h2>Agent Studio</h2>
      <div className="card">
        <p>Create and edit agents, define sub-agents (ADK <code>AgentTool</code>, isolated context, Pro/Flash tiering) and attach tools, MCP servers and skills.</p>
        <p className="mut">Arrives in <b>Phase 2</b> (ADK agents + super-harness + Docker sandbox). Phase 1 chat already runs through an ADK <code>LlmAgent</code> with the budget guard attached.</p>
      </div>
    </div>
  );
}
