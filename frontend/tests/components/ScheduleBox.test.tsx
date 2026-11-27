import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import ScheduleBox from "@/components/workflows/ScheduleBox";

const OFF = { has_schedule_box: true, enabled: false, next_run: null, upcoming: [], last_run: null, last_run_id: null, last_status: null, runs: 0, fails: 0, note: "", describe: "every 10 minutes", schedules_enabled: true };
const ON = { ...OFF, enabled: true, next_run: 1_800_000_000, upcoming: [1_800_000_000, 1_800_000_600, 1_800_001_200], runs: 2, last_run: Date.now() / 1000 - 60, last_status: "done", fails: 1, note: "skipped a time: the previous run was still working" };

describe("Schedule box", () => {
  it("switches the schedule on and off and shows the next times and the last run", async () => {
    let cur: Record<string, unknown> = OFF;
    const { calls } = routeFetch({
      "GET /api/workflows/wf_s/schedule": () => jsonResponse(cur),
      "POST /api/workflows/wf_s/schedule": (init) => { cur = JSON.parse(String(init?.body)).enabled ? ON : OFF; return jsonResponse(cur); },
    });
    const user = userEvent.setup();
    render(<ScheduleBox workflowId="wf_s" saved />);
    expect(await screen.findByText(/When on, it runs every 10 minutes/)).toBeInTheDocument();
    await user.click(screen.getByText("Switch on"));
    expect(await screen.findByText(/On · every 10 minutes/)).toBeInTheDocument();
    expect(screen.getByText(/then /)).toBeInTheDocument();
    expect(screen.getByText(/Ran 2 time\(s\).*1 failure\(s\) in a row/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("still working");
    await user.click(screen.getByText("Switch off"));
    expect(await screen.findByText("Switch on")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([{ enabled: true }, { enabled: false }]);
  });

  it("asks for a saved version, warns when all schedules are paused, and shows errors", async () => {
    routeFetch({
      "GET /api/workflows/wf_s/schedule": () => jsonResponse({ ...OFF, describe: "", schedules_enabled: false, upcoming: [] }),
      "POST /api/workflows/wf_s/schedule": () => jsonResponse({ detail: "this workflow does not start with a Schedule box" }, { status: 422 }),
    });
    const user = userEvent.setup();
    const { unmount } = render(<ScheduleBox workflowId="wf_s" saved={false} />);
    expect(await screen.findByText(/Every schedule is paused/)).toBeInTheDocument();
    expect(screen.getByText(/Save a version first/)).toBeInTheDocument();
    expect(screen.getByText("Switch on")).toBeDisabled();
    unmount();
    render(<ScheduleBox workflowId="wf_s" saved />);
    await user.click(await screen.findByText("Switch on"));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not start with a Schedule box");
  });

  it("shows a load error", async () => {
    routeFetch({ "GET /api/workflows/wf_s/schedule": () => jsonResponse({ detail: "unknown workflow" }, { status: 404 }) });
    render(<ScheduleBox workflowId="wf_s" saved />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown workflow");
  });

  it("shows a dash when an enabled schedule has no next time", async () => {
    routeFetch({ "GET /api/workflows/wf_s/schedule": () => jsonResponse({ ...ON, next_run: null, upcoming: [], runs: 1, fails: 0, last_status: null, note: "" }) });
    render(<ScheduleBox workflowId="wf_s" saved />);
    expect(await screen.findByText("—", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText(/Ran 1 time\(s\) · last .*: —/)).toBeInTheDocument();
  });
});
