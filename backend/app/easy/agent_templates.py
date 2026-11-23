"""What the agent creation wizard needs from the server: starter profiles, plain-language tool labels, tier explanations.

A starter profile is just the field values the advanced editor would produce (the wizard never invents a different shape), so
everything it creates goes through `AgentStore.validate` like any other agent.
"""
from __future__ import annotations

from ..harness.tools import ALL_TOOLS, READ_ONLY_TOOLS

# tool -> (plain label, what it means for a non-expert, risk: safe | changes | runs | app)
TOOL_PLAIN: dict[str, tuple[str, str, str]] = {
    "read_file": ("Read files", "Open and read files in the workspace folder.", "safe"),
    "list_dir": ("Browse folders", "See what is inside folders in the workspace.", "safe"),
    "glob_files": ("Find files by name", "Look for files such as all *.py files.", "safe"),
    "search_text": ("Search inside files", "Find a word or pattern across many files.", "safe"),
    "write_file": ("Create files", "Make new files in the workspace (checked and undone if broken).", "changes"),
    "edit_file": ("Change files", "Edit existing files in the workspace (checked and undone if broken).", "changes"),
    "run_shell": ("Run commands", "Run commands such as tests, inside a locked-down sandbox with no internet.", "runs"),
    "load_skill": ("Use skills", "Read a skill's step-by-step instructions when it needs them.", "safe"),
    "create_skill": ("Write new skills", "Save new reusable instructions into Agent Studio.", "app"),
    "propose_mcp_server": ("Suggest new connections", "Suggest a tool server. It stays off until you approve it.", "app"),
    "propose_openapi_mcp": ("Suggest API connections", "Suggest an online service as a tool. It stays off until you approve it.", "app"),
    "rag_search": ("Search knowledge bases", "Look things up in the knowledge bases you attach, and cite them.", "safe"),
    "rag_list": ("List knowledge bases", "See which knowledge bases it may use.", "safe"),
    "data_sources": ("List data sources", "See which databases and Salesforce orgs it may read (read-only).", "safe"),
    "data_schema": ("Browse data", "Look at the tables and columns of a data source it may read.", "safe"),
    "data_query": ("Query data", "Run a read-only query on a data source. It can never change your data.", "safe"),
    "memory_search": ("Remember (look up)", "Look up what it remembers about you and your projects.", "safe"),
    "memory_save": ("Remember (save)", "Save facts, people and how-tos for later chats. New ones can wait for your review.", "changes"),
    "memory_forget": ("Forget", "Delete a memory that is wrong or no longer wanted.", "changes"),
    "memory_notes": ("Project notes", "Read and add to the notes file (AGENTS.md) in your workspace folder.", "changes"),
    "save_artifact": ("Keep results", "Save reports, charts and files so you can find them again on the Artifacts page.", "saves"),
    "canvas_create": ("Canvas: open", "Open a document, code, table or page beside the chat that you and the agent edit together.", "safe"),
    "canvas_read": ("Canvas: read", "Read the canvas and the text you selected in it.", "safe"),
    "canvas_replace_selection": ("Canvas: rewrite your selection", "Suggest a replacement for the text you selected. You accept or reject it.", "safe"),
    "canvas_insert": ("Canvas: add text", "Suggest text to add to the canvas. You accept or reject it.", "safe"),
    "canvas_patch": ("Canvas: edit", "Suggest exact edits to the canvas. You accept or reject each one.", "safe"),
    "app_agents": ("Apps: your agents", "See which of your agents an app could call.", "safe"),
    "app_list": ("Apps: list", "See your apps.", "safe"),
    "app_create": ("Apps: start one", "Start a new app from a standard template. It changes nothing else.", "changes"),
    "app_tree": ("Apps: list files", "See the files of an app.", "safe"),
    "app_read_file": ("Apps: read a file", "Read one file of an app.", "safe"),
    "app_write_file": ("Apps: write a file", "Suggest a new or replaced file in an app. You accept or reject it (new apps apply at once).", "changes"),
    "app_patch_file": ("Apps: edit a file", "Suggest exact edits to a file of an app. You accept or reject each one.", "changes"),
    "app_delete_file": ("Apps: delete a file", "Suggest deleting a file of an app. You accept or reject it.", "changes"),
    "app_set_manifest": ("Apps: change settings", "Suggest changes to an app's agents, permissions and variables. You accept or reject them.", "changes"),
    "app_check": ("Apps: check", "Check an app for missing files, secrets, remote code and agents it cannot call.", "safe"),
    "app_plan": ("Apps: plan", "Write down what it will build, in steps, before it changes anything.", "saves"),
    "app_plan_done": ("Apps: tick off the plan", "Mark plan steps as done.", "saves"),
    "app_verify": ("Apps: check the preview", "Read the app's files and the errors its preview reported, so it can fix them.", "safe"),
    "app_components": ("Apps: ready-made parts", "List ready-made parts or suggest adding some to an app. You accept or reject it.", "changes"),
    "app_theme": ("Apps: theme", "Suggest a colour theme, font and corners for an app. You accept or reject it.", "changes"),
    "app_connect_agent": ("Apps: connect an agent", "Suggest letting an app call one of your agents. It never switches API access on.", "changes"),
    "app_data_sources": ("Apps: list data sources", "See which data sources an app could read.", "safe"),
    "app_connect_data": ("Apps: connect a data source", "Suggest letting an app read one of your data sources (read-only).", "changes"),
    "app_undo": ("Apps: undo", "Go back one version of a new app.", "changes"),
    "science_sources": ("Science: data sources", "See the open science data sources it can read and their limits.", "safe"),
    "science_start": ("Science: start an investigation", "Open an investigation that keeps its question, evidence and report together.", "saves"),
    "science_search": ("Science: search", "Search PubMed, PubChem, UniProt, arXiv, OEIS and other open sources (read-only).", "safe"),
    "science_calc": ("Science: calculate", "Exact local maths: expressions, units, constants, molar mass, sequence statistics, numeric calculus.", "safe"),
    "science_run_python": ("Science: run Python", "Run Python in the sandbox (no network by default) and record how to repeat it.", "changes"),
    "science_add_evidence": ("Science: add evidence", "Attach evidence from a recorded search or run to an investigation.", "saves"),
    "science_add_claim": ("Science: record a claim", "Record a claim; its label comes from its evidence, not from the model.", "saves"),
    "science_add_hypothesis": ("Science: add a hypothesis", "Add a candidate explanation to rank.", "saves"),
    "science_compare": ("Science: compare hypotheses", "Record which hypothesis the evidence supports better.", "saves"),
    "science_check_citations": ("Science: check citations", "Check DOIs and PMIDs against Crossref and PubMed.", "safe"),
    "science_report": ("Science: report", "Build the investigation report and optionally keep it as artifacts.", "saves"),
    "render_ui": ("Show charts, maps and cards", "Show data as a table, chart, time series, map or card (weather, quotes, people, findings and more) instead of plain text.", "safe"),
    "ui_catalog": ("Look up chart and card types", "See which charts, maps and cards it can show and how to ask for each.", "safe"),
    "todo_write": ("Keep a to-do list", "Write down the steps of a task and tick them off while you watch.", "safe"),
    "repo_map": ("Map the project", "See a compact overview of the project's files and what is in them.", "safe"),
    "web_fetch": ("Read web pages", "Read public web pages (only when you switch web tools on in Settings).", "safe"),
    "web_search": ("Search the web", "Search the internet (only when web tools and a search service are set up).", "safe"),
    "run_workflow": ("Run a workflow", "Run one of your saved workflows and report what it produced.", "runs"),
    "call_remote_agent": ("Ask another agent (A2A)", "Send a question to another agent you registered in Settings.", "app"),
}

RISK_TEXT = {"safe": "Only reads. Cannot change anything.", "changes": "Can change files in the workspace folder.",
             "runs": "Runs code, but only inside the sandbox.", "saves": "Saves copies of its results in Agent Studio (and in cloud storage, if you set that up).", "app": "Changes Agent Studio itself (always needs your approval)."}

TIERS = [
    dict(id="flash", label="Fast and cheap", text="Quick answers at low cost. Best for most everyday work."),
    dict(id="pro", label="Careful and strong", text="Slower and costs more, but better at planning and hard problems."),
    dict(id="custom", label="A specific model", text="You choose the exact model (for example one running on this PC)."),
]

# Instruction text is deliberately plain and short; people can edit it in the review step.
_READ = ["read_file", "list_dir", "glob_files", "search_text"]

TEMPLATES: list[dict] = [
    dict(id="explorer", name="Explorer", tagline="Reads a project and answers questions. Cannot change anything.", color="#4f8cff", risk="safe",
         agent=dict(name="Explorer", description="Reads a project and answers questions about it with file references.", tier="flash",
                    instruction="You explore a project in read-only mode and answer precisely, naming the files and lines you used. Be short. Never dump whole files.",
                    tools=list(READ_ONLY_TOOLS), skills=["repo-orientation"])),
    dict(id="coder", name="Coder", tagline="Makes a change you describe, then runs the tests.", color="#2fb27a", risk="changes",
         agent=dict(name="Coder", description="Makes a well-described change to the project and checks it.", tier="flash",
                    instruction="You make exactly the change requested, as small as possible and in the project's own style. Read first, edit, then run the relevant tests. Report which files changed and the test result.",
                    tools=[*_READ, "write_file", "edit_file", "run_shell", "load_skill"], skills=["debugging", "test-writing"])),
    dict(id="reviewer", name="Reviewer", tagline="Checks changes for mistakes. Cannot edit files.", color="#d98324", risk="runs",
         agent=dict(name="Reviewer", description="Reviews a change for mistakes, edge cases and missing tests.", tier="flash",
                    instruction="You review changes critically: correctness, edge cases, security, missing tests. You cannot edit. List concrete problems with file and line, worst first. Say 'no issues' only if you really checked.",
                    tools=[*READ_ONLY_TOOLS, "run_shell", "load_skill"], skills=["code-review"])),
    dict(id="debugger", name="Debugger", tagline="Finds why something breaks, then fixes it.", color="#c94f4f", risk="changes",
         agent=dict(name="Debugger", description="Reproduces a problem, finds the cause and fixes it.", tier="pro",
                    instruction="First reproduce the problem and read the error. Form a hypothesis, test it, and find the root cause before changing anything. Make the smallest fix, then prove it works by running the failing case again. Explain the cause in plain words.",
                    tools=[*_READ, "write_file", "edit_file", "run_shell", "load_skill"], skills=["debugging", "test-writing"])),
    dict(id="architect", name="Architect", tagline="Plans the work and hands steps to helpers. Does not edit.", color="#8a5cf6", risk="safe",
         agent=dict(name="Architect", description="Plans a piece of work and delegates the steps to specialists.", tier="pro",
                    instruction="You plan. Understand the goal, ask `explorer` to look at the project, write a short numbered plan with risks, and hand focused steps to `coder` and `reviewer`. You never edit files yourself. Finish with what was done, how it was checked, and what was not.",
                    tools=[*READ_ONLY_TOOLS, "load_skill"], skills=["plan-and-delegate", "repo-orientation"], sub_agents=["explorer", "coder", "reviewer"])),
    dict(id="researcher", name="Researcher", tagline="Answers from your knowledge bases and cites its sources.", color="#1fa5b8", risk="safe",
         agent=dict(name="Researcher", description="Finds answers in your knowledge bases and cites where they came from.", tier="flash",
                    instruction="You answer only from the knowledge bases and files you can read. Always search first, quote the source for each claim, and say clearly when the sources do not contain the answer.",
                    tools=["rag_search", "rag_list", "read_file", "list_dir", "search_text", "load_skill"], skills=["research-and-summarize"], rag=["*"])),
    dict(id="minimal", name="Command-line only", tagline="One tool: runs commands in the sandbox. Small and predictable.", color="#6b7280", risk="runs",
         agent=dict(name="Command-line helper", description="Solves tasks using only commands in the sandbox.", tier="flash",
                    instruction="You work only by running commands in the sandbox. Think about what to run, run it, read the output, repeat. Keep each command small. Say what you ran and what it showed.",
                    tools=["run_shell"], skills=[])),
    dict(id="orchestrator", name="Orchestrator", tagline="Leads a team: explorer, coder and reviewer, and can ask outside coding agents.", color="#e0559c", risk="runs",
         agent=dict(name="Orchestrator", description="Leads a small team of helpers to finish bigger tasks.", tier="pro",
                    instruction="You lead. For anything bigger than a quick fix: have `explorer` look first, make a short plan, give `coder` one focused step at a time with complete instructions, then have `reviewer` check it. Run the tests yourself before you report. Never claim success without evidence.",
                    tools=[*_READ, "write_file", "edit_file", "run_shell", "load_skill"], skills=["plan-and-delegate", "code-review"],
                    sub_agents=["explorer", "coder", "reviewer"], can_spawn=False, acp_agents=[], acp_policy="ask")),
]


def wizard_data() -> dict:
    tools = [dict(name=n, label=TOOL_PLAIN[n][0], plain=TOOL_PLAIN[n][1], risk=TOOL_PLAIN[n][2], risk_text=RISK_TEXT[TOOL_PLAIN[n][2]]) for n in ALL_TOOLS if n in TOOL_PLAIN]
    return dict(templates=TEMPLATES, tools=tools, tiers=TIERS, risk_text=RISK_TEXT)


DRAFT_SYSTEM = ("You write the instruction text for an AI agent. Reply with ONLY the instruction text (no title, no quotes, no markdown fences). "
                "Write 3 to 8 short sentences in the second person ('You ...'). Say what the agent is for, how it should work step by step, "
                "what it must never do, and how it reports back. Only mention tools from the list you are given. Plain words.")


def draft_prompt(goal: str, name: str, tools: list[str]) -> str:
    labels = ", ".join(f"{TOOL_PLAIN[t][0]} ({t})" for t in tools if t in TOOL_PLAIN) or "none"
    return f"Agent name: {name or 'Agent'}\nWhat it should do: {goal.strip()}\nTools it has: {labels}\n\nWrite its instruction text."
