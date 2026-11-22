import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CostChart, RunThread, RunTimeline, buildLanes, clock, colorFor, fmtDur, runText, type Run } from "@/components/Runs";

const run = (over: Partial<Run> = {}): Run => ({
  run_id: "root", parent: null, agent: "engineer", spawned: false, task: "Build the thing", status: "done",
  started: 1000, ended: 1010, steps: [], texts: [], calls: [], result: null, cost_usd: 0, ...over,
});

describe("buildLanes", () => {
  it("puts every turn of the same root agent on one lane", () => {
    const lanes = buildLanes([run({ turn: 1, uid: "1:root" }), run({ turn: 2, uid: "2:root" })]);
    expect(lanes).toHaveLength(1);
    expect(lanes[0]).toMatchObject({ key: "root:engineer", label: "engineer", spawned: false });
    expect(lanes[0].runs).toHaveLength(2);
  });

  it("gives each sub-agent its own lane, numbering repeats and dropping the team prefix", () => {
    const lanes = buildLanes([
      run(),
      run({ run_id: "r1", parent: "root", agent: "team__research", uid: "u1" }),
      run({ run_id: "r2", parent: "root", agent: "team__research", spawned: true, uid: "u2" }),
      run({ run_id: "r3", parent: "root", agent: "writer", uid: "u3" }),
    ]);
    expect(lanes.map((l) => l.label)).toEqual(["engineer", "research 1", "research 2", "writer"]);
    expect(lanes[2].spawned).toBe(true);
    expect(lanes[1].key).toBe("u1");
  });

  it("marks external agent lanes and falls back to the turn-based uid", () => {
    const lanes = buildLanes([run({ run_id: "x1", parent: "root", agent: "claude_code", turn: 3, external: true })]);
    expect(lanes[0].external).toBe(true);
    expect(lanes[0].key).toBe("3:x1");
  });

  it("returns no lanes for no runs", () => {
    expect(buildLanes([])).toEqual([]);
  });
});

describe("helpers", () => {
  it("runText lower-cases every part of a run so it can be searched", () => {
    const text = runText(run({ task: "Find BUGS", result: "Found One", texts: ["Hi There"], steps: [{ agent: "a", type: "call", tool: "Grep", args: "X=1", result: "Y" }] }));
    expect(text).toContain("find bugs");
    expect(text).toContain("found one");
    expect(text).toContain("hi there");
    expect(text).toContain("grep x=1 y");
  });

  it("fmtDur chooses a readable unit", () => {
    expect(fmtDur(0.5)).toBe("500ms");
    expect(fmtDur(2.345)).toBe("2.3s");
    expect(fmtDur(75)).toBe("1m 15s");
    expect(fmtDur(3725)).toBe("1h 2m");
  });

  it("colorFor ignores a numeric suffix so repeats share a colour", () => {
    expect(colorFor("researcher_2")).toBe(colorFor("researcher"));
    expect(colorFor("alpha")).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("clock renders a time of day", () => {
    expect(clock(1_700_000_000)).toMatch(/\d{1,2}:\d{2}:\d{2}/);
  });
});

describe("RunTimeline", () => {
  it("says so when there is no activity", () => {
    render(<RunTimeline runs={[]} onSelect={() => {}} />);
    expect(screen.getByText("No agent activity yet.")).toBeInTheDocument();
  });

  it("lists one lane per agent and selects a lane on click", async () => {
    const onSelect = vi.fn();
    const sub = run({ run_id: "r1", parent: "root", agent: "reviewer", started: 1002, ended: 1008, uid: "u1", spawned: true });
    render(<RunTimeline runs={[run(), sub]} selected="root:engineer" onSelect={onSelect} />);
    expect(screen.getByText("reviewer")).toBeInTheDocument();
    expect(screen.getByText("built")).toHaveClass("tl-tag");
    expect(screen.getByText("engineer").closest(".tl-label")).toHaveClass("on");
    await userEvent.click(screen.getByText("reviewer"));
    expect(onSelect).toHaveBeenCalledWith("u1");
  });

  it("tags external lanes and selects from the timeline body too", async () => {
    const onSelect = vi.fn();
    const ext = run({ run_id: "x1", parent: "root", agent: "cursor", started: 1001, ended: 1004, uid: "ux", external: true });
    const { container } = render(<RunTimeline runs={[run(), ext]} onSelect={onSelect} />);
    expect(screen.getByText("external")).toHaveClass("tl-tag", "ext");
    await userEvent.click(container.querySelector(".tl-lane")!);
    expect(onSelect).toHaveBeenCalled();
  });

  it("dims lanes that do not match the search", () => {
    const sub = run({ run_id: "r1", parent: "root", agent: "reviewer", started: 1002, ended: 1008, uid: "u1", task: "check the auth module" });
    render(<RunTimeline runs={[run({ task: "build login" }), sub]} query="AUTH" onSelect={() => {}} />);
    expect(screen.getByText("reviewer").closest(".tl-label")).not.toHaveClass("dim");
    expect(screen.getByText("engineer").closest(".tl-label")).toHaveClass("dim");
  });

  it("draws runs, model calls and tool calls as segments", () => {
    const r = run({
      started: 1000, ended: 1010,
      calls: [{ agent: "engineer", provider: "p", model: "gpt-x", cost_usd: 0.002, input_tokens: 1, output_tokens: 2, t0: 1001, t1: 1004 }],
      steps: [{ agent: "engineer", type: "result", tool: "read_file", ts: 1006, ms: 800, ok: false }],
    });
    const { container } = render(<RunTimeline runs={[r]} onSelect={() => {}} now={1011} />);
    expect(container.querySelector(".tl-run")).not.toBeNull();
    expect(container.querySelector(".tl-seg:not(.tool)")).toHaveAttribute("title", expect.stringContaining("gpt-x"));
    expect(container.querySelector(".tl-seg.tool")).toHaveAttribute("title", "read_file · 800ms · failed");
  });

  it("draws a still-running run up to now", () => {
    const r = run({ status: "running", started: 1000, ended: null });
    const { container } = render(<RunTimeline runs={[r]} onSelect={() => {}} now={1020} />);
    expect(container.querySelector(".tl-run.running")).toHaveAttribute("title", expect.stringContaining("running"));
  });

  it("starts the axis at the first real activity when agent setup came first", () => {
    const r = run({ started: 1000, ended: 1100, calls: [{ agent: "engineer", provider: "p", model: "m", cost_usd: 0, input_tokens: 0, output_tokens: 0, t0: 1090, t1: 1095 }] });
    render(<RunTimeline runs={[r]} onSelect={() => {}} />);
    expect(screen.getByText(/after 1m 30s setup\)/)).toBeInTheDocument();
  });

  it("draws the delegation link from parent to child lane", () => {
    const child = run({ run_id: "r1", parent: "root", agent: "reviewer", started: 1003, ended: 1006, uid: "u1" });
    const { container } = render(<RunTimeline runs={[run(), child]} onSelect={() => {}} />);
    expect(container.querySelector(".tl-link")).not.toBeNull();
  });

  it("skips a delegation whose parent is not in the list", () => {
    const orphan = run({ run_id: "r9", parent: "missing", agent: "reviewer", started: 1003, ended: 1006, uid: "u9" });
    const { container } = render(<RunTimeline runs={[run(), orphan]} onSelect={() => {}} />);
    expect(container.querySelector(".tl-link")).toBeNull();
  });
});

describe("RunThread", () => {
  it("shows the lane heading with the agent's role", () => {
    const lane = buildLanes([run({ instruction: "Be **precise** and cite sources." })])[0];
    const { container } = render(<RunThread lane={lane} />);
    expect(container.querySelector(".thread-head b")?.textContent).toBe("engineer");
    expect(screen.getByText("precise").tagName).toBe("STRONG");
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("collapses a long role until Read more is pressed", async () => {
    const long = "word ".repeat(120);
    const lane = buildLanes([run({ instruction: long })])[0];
    const { container } = render(<RunThread lane={lane} />);
    expect(container.querySelector(".role")).not.toHaveClass("open");
    await userEvent.click(screen.getByRole("button", { name: "Read more" }));
    expect(container.querySelector(".role")).toHaveClass("open");
    expect(screen.queryByRole("button", { name: "Read more" })).toBeNull();
  });

  it("shows the role from the agent definition for a built agent, and who built it", () => {
    const built = run({
      run_id: "r1", parent: "root", agent: "team__qa", spawned: true, uid: "u1", result: "Tests pass",
      definition: { name: "QA", parent: "engineer", instruction: "Test everything", tier: "flash", tools: ["read_file"], mcp: [] },
    });
    const lane = buildLanes([built])[0];
    const onSave = vi.fn();
    render(<RunThread lane={lane} onSave={onSave} />);
    expect(screen.getByText("created by engineer")).toBeInTheDocument();
    expect(screen.getByText("Test everything")).toBeInTheDocument();
    expect(screen.getByText(/tier flash · tools \[read_file\] · MCP \[none\]/)).toBeInTheDocument();
    expect(screen.getByText("Report to parent")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save as agent" }));
    expect(onSave).toHaveBeenCalledWith(built);
  });

  it("does not offer to save a built agent that is still running", () => {
    const lane = buildLanes([run({ run_id: "r1", parent: "root", agent: "a", spawned: true, status: "running", uid: "u1" })])[0];
    render(<RunThread lane={lane} onSave={() => {}} />);
    expect(screen.queryByRole("button", { name: "Save as agent" })).toBeNull();
    expect(screen.getByText("created by agent")).toBeInTheDocument();
  });

  it("labels the external agent with its policy and reported cost", () => {
    const ext = run({ run_id: "x1", parent: "root", agent: "claude_code", external: true, external_cost: 0.05, definition: { label: "Claude Code", policy: "ask" }, uid: "ux" });
    render(<RunThread lane={buildLanes([ext])[0]} />);
    expect(screen.getByText("external · Claude Code · policy ask")).toBeInTheDocument();
    expect(screen.getByText("reported $0.0500")).toBeInTheDocument();
  });

  it("counts turns when a lane has several", () => {
    const lane = buildLanes([run({ turn: 1, uid: "1" }), run({ turn: 2, uid: "2" })])[0];
    render(<RunThread lane={lane} />);
    expect(screen.getByText("2 turns")).toBeInTheDocument();
  });

  it("walks through a model call, a tool call with its output, and the reply", async () => {
    const r = run({
      calls: [{ agent: "engineer", provider: "p", model: "gpt-x", cost_usd: 0.001, input_tokens: 5, output_tokens: 6, t0: 10, t1: 12 }],
      steps: [
        { agent: "engineer", type: "call", tool: "read_file", args: "a.txt", ts: 11 },
        { agent: "engineer", type: "result", tool: "read_file", ts: 11.5, ms: 500, ok: true, result: "file contents" },
      ],
      texts: ["Here is **the** plan"],
      text_ts: [11.8],
      result: "All done",
    });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("Thought for 2.0s")).toBeInTheDocument();
    expect(screen.getByText(/gpt-x · 5→6 tok/)).toBeInTheDocument();
    expect(screen.getByText("500ms")).toBeInTheDocument();
    expect(container.querySelector(".said strong")?.textContent).toBe("the");
    expect(screen.getByText("Answer")).toBeInTheDocument();
    expect(screen.queryByText("file contents")).toBeNull();
    await userEvent.click(screen.getByText("read_file"));
    expect(screen.getByText("file contents")).toBeInTheDocument();
    await userEvent.click(screen.getByText("read_file"));
    expect(screen.queryByText("file contents")).toBeNull();
  });

  it("shows a tool that has not finished yet", async () => {
    const r = run({ status: "running", ended: null, steps: [{ agent: "engineer", type: "call", tool: "bash", args: "npm test", ts: 1002 }] });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("…")).toBeInTheDocument();
    expect(screen.getByText("working…")).toBeInTheDocument();
    await userEvent.click(container.querySelector(".tool-row")!);
    expect(screen.getByText("(no result yet)")).toBeInTheDocument();
  });

  it("shows a result that arrives without its call, and a failed tool", () => {
    const r = run({
      steps: [{ agent: "engineer", type: "result", tool: "fetch", ts: 1004, ms: 30, ok: false, result: "403" }],
    });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("fetch")).toBeInTheDocument();
    expect(container.querySelector(".tool-row")).toHaveClass("bad");
  });

  it("shows text that arrives before any model call", () => {
    const r = run({ texts: ["Starting up"], text_ts: [1000.5] });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(container.querySelector(".said")?.textContent).toBe("Starting up");
    expect(screen.queryByText(/Thought for/)).toBeNull();
  });

  it("uses the last ordering fallbacks when timestamps are missing", () => {
    const r = run({
      started: undefined, ended: undefined, status: "error",
      calls: [{ agent: "engineer", provider: "p", model: "m", cost_usd: 0, input_tokens: 0, output_tokens: 0 }],
      texts: ["no timestamps"],
      steps: [{ agent: "engineer", type: "call", tool: "t", args: "", ts: undefined }],
    });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("Thought for a moment")).toBeInTheDocument();
    expect(container.querySelector(".notice")?.textContent).toBe("error");
  });

  it("shows thinking when the model reported any", () => {
    const r = run({ thoughts: ["plan step one", ""] });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("Thinking (1)")).toBeInTheDocument();
    expect(container.querySelector(".think-box")).not.toBeNull();
  });

  it("highlights the search term in the task and in tool arguments", () => {
    const r = run({
      task: "Find the needle",
      steps: [
        { agent: "engineer", type: "call", tool: "grep", args: "needle.ts", ts: 1002 },
        { agent: "engineer", type: "result", tool: "grep", ts: 1003, ms: 5, ok: true, result: "" },
      ],
    });
    const { container } = render(<RunThread lane={buildLanes([r])[0]} query="needle" />);
    const marks = container.querySelectorAll("mark");
    expect(marks.length).toBeGreaterThanOrEqual(2);
    expect(marks[0].textContent).toBe("needle");
  });

  it("names the run's author correctly", () => {
    const lead = run({ run_id: "r1", parent: "root", agent: "a", uid: "u1" });
    const other = run({ run_id: "r2", parent: "r1", agent: "b", uid: "u2" });
    const { container } = render(<RunThread lane={buildLanes([lead])[0]} />);
    expect(container.querySelector(".msg-chip span")?.textContent).toBe("lead agent");
    const { container: c2 } = render(<RunThread lane={buildLanes([other])[0]} />);
    expect(c2.querySelector(".msg-chip span")?.textContent).toBe("parent agent");
  });

  it("falls back to a placeholder when a task is empty", () => {
    render(<RunThread lane={buildLanes([run({ task: "" })])[0]} />);
    expect(screen.getByText("(no task text)")).toBeInTheDocument();
  });

  it("shows the role from a built agent's definition when the run has none", () => {
    const built = run({ run_id: "r1", parent: "root", agent: "team__x", spawned: true, uid: "u1", definition: { instruction: "Use the definition", tools: [], mcp: ["fs"] } });
    render(<RunThread lane={buildLanes([built])[0]} />);
    expect(screen.getByText("Use the definition")).toBeInTheDocument();
    expect(screen.getByText(/tools \[none\] · MCP \[fs\]/)).toBeInTheDocument();
  });

  it("shows a report for a sub-agent and the cost of its calls", () => {
    const sub = run({
      run_id: "r1", parent: "root", agent: "writer", uid: "u1", result: "Drafted",
      calls: [{ agent: "writer", provider: "p", model: "m", cost_usd: 0.0007, input_tokens: 1, output_tokens: 1, t0: 1000, t1: 1001 }],
    });
    const { container } = render(<RunThread lane={buildLanes([sub])[0]} />);
    expect(screen.getByText("Report to parent")).toBeInTheDocument();
    expect(screen.getAllByText(/\$0\.00070/).length).toBeGreaterThan(0);
    expect(container.querySelector(".report")).not.toBeNull();
  });
});

describe("CostChart", () => {
  it("says so when no model call has been timed", () => {
    render(<CostChart calls={[]} />);
    expect(screen.getByText("No timed model calls yet.")).toBeInTheDocument();
  });

  it("plots the running total with its time span", () => {
    const { container } = render(<CostChart calls={[
      { agent: "a", provider: "p", model: "m", cost_usd: 0.002, input_tokens: 0, output_tokens: 0, t0: 1003, t1: 1005 },
      { agent: "a", provider: "p", model: "m", cost_usd: 0.001, input_tokens: 0, output_tokens: 0, t0: 1000, t1: 1002 },
    ]} height={120} />);
    expect(screen.getByText("$0.00300")).toBeInTheDocument();
    expect(container.querySelector("svg.cost-chart")).toHaveAttribute("height", "120");
    expect(container.querySelector("path.line")).not.toBeNull();
    expect(container.querySelector("circle.dotc")).not.toBeNull();
  });

  it("starts the span at the first call's start when there is one", () => {
    const { container } = render(<CostChart calls={[
      { agent: "a", provider: "p", model: "m", cost_usd: 0.5, input_tokens: 0, output_tokens: 0, t1: 1002 },
    ]} />);
    expect(container.querySelector("path.area")).not.toBeNull();
    expect(screen.getByText("$0.5000")).toBeInTheDocument();
  });

  it("ignores calls that never finished", () => {
    render(<CostChart calls={[{ agent: "a", provider: "p", model: "m", cost_usd: 1, input_tokens: 0, output_tokens: 0, t0: 1000 }]} />);
    expect(screen.getByText("No timed model calls yet.")).toBeInTheDocument();
  });
});
