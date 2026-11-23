import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentAgui, { aguiBadge, aguiOf } from "@/components/AgentAgui";
import { jsonResponse, routeFetch } from "../helpers/http";

describe("AG-UI flags", () => {
  it("treat anything missing as on and name what is off", () => {
    expect(aguiOf(undefined)).toEqual({ components: true, endpoint: true });
    expect(aguiOf({ agui: { components: false } })).toEqual({ components: false, endpoint: true });
    expect(aguiBadge({})).toBeNull();
    expect(aguiBadge({ agui: { components: false } })).toBe("no components");
    expect(aguiBadge({ agui: { endpoint: false } })).toBe("no AG-UI stream");
    expect(aguiBadge({ agui: { components: false, endpoint: false } })).toBe("AG-UI off");
  });
});

describe("AgentAgui section", () => {
  it("shows what each switch does and sends the full setting when one is toggled", async () => {
    const onChange = vi.fn();
    render(<AgentAgui agent={{ agui: { components: true, endpoint: true } }} onChange={onChange} />);
    expect(screen.getByText(/It can show charts, tables, maps and cards/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Rich components in answers"));
    expect(onChange).toHaveBeenLastCalledWith({ components: false, endpoint: true });
    await userEvent.click(screen.getByLabelText("AG-UI stream for other apps"));
    expect(onChange).toHaveBeenLastCalledWith({ components: true, endpoint: false });
  });

  it("explains an off switch", () => {
    render(<AgentAgui agent={{ agui: { components: false, endpoint: false } }} onChange={() => undefined} />);
    expect(screen.getByText(/plain Markdown/)).toBeInTheDocument();
    expect(screen.getByText(/refuses this agent/)).toBeInTheDocument();
    expect(screen.getByLabelText("Rich components in answers")).not.toBeChecked();
  });

  it("starts a new agent from the default chosen in Settings", async () => {
    routeFetch({ "GET /api/uikit/config": () => jsonResponse({ new_agents: { components: false, endpoint: true } }) });
    const onChange = vi.fn();
    render(<AgentAgui agent={{}} isNew compact onChange={onChange} />);
    await waitFor(() => expect(screen.getByLabelText("Rich components in answers")).not.toBeChecked());
    await userEvent.click(screen.getByLabelText("AG-UI stream for other apps"));
    expect(onChange).toHaveBeenLastCalledWith({ components: false, endpoint: false });       // the whole setting is sent, starting from the default
  });

  it("falls back to everything on when the default cannot be read", async () => {
    routeFetch({ "GET /api/uikit/config": () => jsonResponse({ detail: "no" }, { status: 500 }) });
    render(<AgentAgui agent={{}} isNew onChange={() => undefined} />);
    await waitFor(() => expect(screen.getByLabelText("Rich components in answers")).toBeChecked());
    expect(screen.getByLabelText("AG-UI stream for other apps")).toBeChecked();
  });

  it("does not look up the default for an existing agent", () => {
    const { calls } = routeFetch({});
    render(<AgentAgui agent={{ agui: { components: true, endpoint: true } }} onChange={() => undefined} />);
    expect(calls).toHaveLength(0);
  });
});
