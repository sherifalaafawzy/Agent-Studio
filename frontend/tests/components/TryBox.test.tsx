import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import TryBox from "@/components/workflows/TryBox";
import type { Doc, WNode } from "@/lib/workflows";

const NODE: WNode = { id: "f1", type: "filter", label: "Filter", position: { x: 0, y: 0 }, config: {} };
const DOC: Doc = { id: "wf_a", name: "A", autonomy: "auto", nodes: [NODE], edges: [] };

describe("Try this box", () => {
  it("tries one box with the text you type and shows the answer", async () => {
    const { calls } = routeFetch({ "POST /api/workflows/wf_a/nodes/f1/test": () => jsonResponse({ status: "done", output: "[1, 2]", error: null, ports: ["true"], side_effects: false }) });
    const user = userEvent.setup();
    const runFrom = vi.fn();
    render(<TryBox doc={DOC} node={NODE} lastInput="from the last run" canRunFrom runFromWhy="" onRunFrom={runFrom} />);
    expect(screen.getByLabelText("Test input")).toHaveValue("from the last run");
    await user.clear(screen.getByLabelText("Test input"));
    await user.click(screen.getByLabelText("Test input"));
    await user.paste("[1, 2, 3]");
    await user.click(screen.getByText("Test this box"));
    const res = await screen.findByTestId("try-result");
    expect(res).toHaveTextContent("done"); expect(res).toHaveTextContent("went true"); expect(res).toHaveTextContent("[1, 2]");
    expect(calls[0].body).toMatchObject({ input: "[1, 2, 3]", allow_side_effects: false, doc: { id: "wf_a" } });
    await user.click(screen.getByText("Run from here"));
    expect(runFrom).toHaveBeenCalled();
  });

  it("says when a box was not run because it could change something, and runs it when allowed", async () => {
    let n = 0;
    const { calls } = routeFetch({ "POST /api/workflows/wf_a/nodes/f1/test": () => jsonResponse(n++ === 0 ? { status: "not_run", message: "This box could change something", side_effects: true } : { status: "done", output: "", ports: ["out"] }) });
    const user = userEvent.setup();
    render(<TryBox doc={DOC} node={NODE} canRunFrom={false} runFromWhy="Run the whole workflow once first" onRunFrom={() => undefined} />);
    expect(screen.getByText("Run from here")).toBeDisabled();
    expect(screen.getByText("Run the whole workflow once first")).toBeInTheDocument();
    await user.click(screen.getByText("Test this box"));
    expect(await screen.findByTestId("try-result")).toHaveTextContent("not run");
    expect(screen.getByTestId("try-result")).toHaveTextContent("could change something");
    await user.click(screen.getByLabelText(/Allow side effects/));
    await user.click(screen.getByText("Test this box"));
    await waitFor(() => expect(screen.getByTestId("try-result")).toHaveTextContent("(empty)"));
    expect(calls[1].body.allow_side_effects).toBe(true);
    expect(screen.getByTestId("try-result")).not.toHaveTextContent("went");
  });

  it("shows an error from the box and a request that failed", async () => {
    let n = 0;
    routeFetch({ "POST /api/workflows/wf_a/nodes/f1/test": () => (n++ === 0 ? jsonResponse({ status: "error", error: "WorkflowError: bad pattern", ports: [] }) : jsonResponse({ detail: "that box does not exist" }, { status: 404 })) });
    const user = userEvent.setup();
    render(<TryBox doc={DOC} node={NODE} canRunFrom runFromWhy="" onRunFrom={() => undefined} />);
    await user.click(screen.getByText("Test this box"));
    expect(await screen.findByTestId("try-result")).toHaveTextContent("bad pattern");
    await user.click(screen.getByText("Test this box"));
    expect(await screen.findByText("that box does not exist")).toBeInTheDocument();
    expect(screen.queryByTestId("try-result")).not.toBeInTheDocument();
  });
});
