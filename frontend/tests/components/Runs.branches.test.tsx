import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunThread, RunTimeline, buildLanes, runText, type Run, type Step } from "@/components/Runs";

const run = (over: Partial<Run> = {}): Run => ({
  run_id: "root", parent: null, agent: "engineer", spawned: false, task: "Build the thing", status: "done",
  started: 1000, ended: 1010, steps: [], texts: [], calls: [], result: null, cost_usd: 0, ...over,
});
const step = (over: Partial<Step>): Step => ({ agent: "engineer", type: "result", tool: "grep", ...over });
const noop = () => {};

describe("runText fallbacks", () => {
  it("treats missing step args and results as empty text", () => {
    const t = runText(run({ steps: [step({ tool: "Grep" })] }));
    expect(t).toContain("grep  ");
    expect(t).not.toContain("undefined");
  });
});

describe("RunTimeline without timing data", () => {
  it("draws a lane but no run bar when the run never recorded a start", () => {
    const { container } = render(<RunTimeline runs={[run({ started: undefined, ended: undefined })]} onSelect={noop} />);
    expect(screen.getByText("engineer")).toBeInTheDocument();
    expect(container.querySelectorAll(".tl-run")).toHaveLength(0);
    expect(container.querySelectorAll(".tl-axis span")).toHaveLength(6);
  });

  it("extends a running run with no end to the supplied 'now'", () => {
    const { container } = render(<RunTimeline runs={[run({ status: "running", ended: null })]} onSelect={noop} now={1010} />);
    expect(container.querySelector(".tl-run")!.getAttribute("title")).toBe("engineer · running · 10.0s");
  });

  it("collapses a finished run with no end to zero length", () => {
    const { container } = render(<RunTimeline runs={[run({ ended: undefined })]} onSelect={noop} now={5000} />);
    expect(container.querySelector(".tl-run")!.getAttribute("title")).toBe("engineer · done · 0ms");
  });

  it("starts the axis from a tool step that has a timestamp but no duration, and labels tool segments by outcome", () => {
    const { container } = render(<RunTimeline onSelect={noop} runs={[run({
      steps: [
        step({ tool: "noduration", ts: 1004 }),
        step({ tool: "fine", ts: 1006, ms: 500, ok: true }),
        step({ tool: "broken", ts: 1008, ms: 250, ok: false }),
      ],
    })]} />);
    const titles = Array.from(container.querySelectorAll(".tl-seg.tool")).map((e) => e.getAttribute("title"));
    // the step without ms is not drawn as a tool segment
    expect(titles).toEqual(["fine · 500ms", "broken · 250ms · failed"]);
  });
});

describe("RunThread item ordering and labels", () => {
  const lane = (r: Run) => buildLanes([r])[0];

  it("shows a result that has no matching call, with and without timestamps or duration", () => {
    render(<RunThread lane={lane(run({
      started: undefined, ended: undefined,
      steps: [
        step({ tool: "bare" }),                      // no ts, no started: sequence fallback
        step({ tool: "stamped", ts: 1005 }),          // ts but no ms
        step({ tool: "timed", ts: 1006, ms: 12.4 }),  // ts and ms
      ],
    }))} />);
    expect(screen.getByText("bare")).toBeInTheDocument();
    expect(screen.getByText("stamped")).toBeInTheDocument();
    expect(screen.getByText("timed")).toBeInTheDocument();
    expect(screen.getByText("12ms")).toBeInTheDocument();
  });

  it("pairs a call with its result and uses the call timestamp, and marks calls with no result as pending", () => {
    const { container } = render(<RunThread lane={lane(run({
      steps: [
        step({ type: "call", tool: "read", args: "a.ts", ts: 1002 }),
        step({ type: "result", tool: "read", ts: 1003, ms: 800, result: "contents" }),
        step({ type: "call", tool: "slow", args: "x", ts: 1005 }),
      ],
    }))} />);
    const rows = Array.from(container.querySelectorAll(".tool-row"));
    expect(rows.map((r) => r.querySelector("b")!.textContent)).toEqual(["read", "slow"]);
    expect(rows[0].querySelector(".ms")!.textContent).toBe("800ms");
    expect(rows[1].querySelector(".ms")!.textContent).toBe("…");
  });

  it("derives a start time from the result when the matching call carried no timestamp", () => {
    const { container } = render(<RunThread lane={lane(run({
      steps: [
        step({ type: "call", tool: "late", args: "" }),
        step({ type: "call", tool: "early", args: "" }),
        step({ type: "result", tool: "late", ts: 1008, ms: 1000, result: "r1" }),   // started 1007
        step({ type: "result", tool: "early", ts: 1003 }),                          // no ms: started 1003
      ],
    }))} />);
    const names = Array.from(container.querySelectorAll(".tool-row b")).map((e) => e.textContent);
    expect(names).toEqual(["early", "late"]);
  });

  it("leaves the duration blank for a result that reported none", () => {
    const { container } = render(<RunThread lane={lane(run({ steps: [step({ tool: "quiet", ts: 1001 })] }))} />);
    expect(container.querySelector(".tool-row .ms")!.textContent).toBe("");
  });

  it("orders unstamped text after the run start and falls back to started/ended", () => {
    const { container } = render(<RunThread lane={lane(run({ started: undefined, ended: undefined, texts: ["first words", "second words"] }))} />);
    const said = Array.from(container.querySelectorAll(".said")).map((e) => e.textContent);
    expect(said).toEqual(["first words", "second words"]);
  });
});

describe("RunThread lane headers", () => {
  it("names an external lane by agent when its definition has no label, and omits a missing reported cost", () => {
    const r = run({ run_id: "r1", parent: "root", agent: "ext", external: true, external_cost: null, definition: { policy: "ask" }, uid: "u1" });
    render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("external · ext · policy ask")).toBeInTheDocument();
    expect(screen.queryByText(/reported/)).toBeNull();
  });

  it("shows 'none' for a created agent whose definition lists no tools or MCP servers", () => {
    const r = run({ run_id: "r1", parent: "root", agent: "maker", spawned: true, instruction: "Do it", definition: { tier: "small" }, uid: "u1" });
    render(<RunThread lane={buildLanes([r])[0]} />);
    expect(screen.getByText("tier small · tools [none] · MCP [none]")).toBeInTheDocument();
  });
});
