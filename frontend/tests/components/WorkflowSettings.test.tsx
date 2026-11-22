import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import WorkflowSettings from "@/components/workflows/WorkflowSettings";

const SETTINGS = { webhooks_enabled: false, hook_rate_per_min: 30, hook_max_body_kb: 256, hook_wait_max_s: 30, max_nodes: 60, run_retention: 100, http_timeout_s: 30, http_max_kb: 512, http_allow_hosts: ["api.internal.example"] };
const CRED = { id: "crm", label: "CRM", kind: "header", header: "X-Key", set: true };

describe("Workflow settings", () => {
  it("shows what is set, saves changes, and keeps credentials write-only", async () => {
    let saved: any = null;
    const { calls } = routeFetch({
      "GET /api/workflows/settings": () => jsonResponse({ settings: SETTINGS, credentials: [CRED] }),
      "PUT /api/workflows/settings": (init) => { saved = JSON.parse(String(init?.body)); return jsonResponse({ settings: { ...saved, http_allow_hosts: ["a.test", "b.test"] } }); },
      "POST /api/workflows/credentials": (init) => { const b = JSON.parse(String(init?.body)); return jsonResponse({ id: b.id, label: b.label || b.id, kind: b.kind, header: b.header, set: true }); },
      "DELETE /api/workflows/credentials/crm": () => jsonResponse({ ok: true }),
    });
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<WorkflowSettings onClose={onClose} />);
    expect(await screen.findByTestId("wf-settings")).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed hosts")).toHaveValue("api.internal.example");
    expect(screen.getByText("CRM")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("s3cret")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText(/Let Webhook boxes be called/));
    await user.clear(screen.getByLabelText("Webhook calls per minute"));
    await user.type(screen.getByLabelText("Webhook calls per minute"), "12");
    await user.clear(screen.getByLabelText("Allowed hosts"));
    await user.type(screen.getByLabelText("Allowed hosts"), "a.test{Enter}b.test");
    await user.click(screen.getByText("Save settings"));
    await waitFor(() => expect(saved).toMatchObject({ webhooks_enabled: true, hook_rate_per_min: 12, http_allow_hosts: ["a.test", "b.test"] }));
    // a credential: the secret goes up once and never comes back
    expect(screen.getByText("Save credential")).toBeDisabled();
    await user.type(screen.getByLabelText("Credential id"), "tok");
    await user.type(screen.getByLabelText("Credential name"), "Token");
    await user.type(screen.getByLabelText("Credential secret"), "s3cret");
    await user.selectOptions(screen.getByLabelText("Credential kind"), "header");
    await user.type(screen.getByLabelText("Header name"), "X-Api-Key");
    await user.click(screen.getByText("Save credential"));
    await waitFor(() => expect(screen.getAllByTestId("cred")).toHaveLength(2));
    expect(calls.find((c) => c.url.pathname === "/api/workflows/credentials")!.body).toMatchObject({ id: "tok", kind: "header", header: "X-Api-Key", value: "s3cret" });
    expect(screen.getByLabelText("Credential secret")).toHaveValue("");
    await user.click(screen.getByLabelText("Delete credential crm"));
    await waitFor(() => expect(screen.getAllByTestId("cred")).toHaveLength(1));
    await user.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("explains failures and an empty list", async () => {
    routeFetch({
      "GET /api/workflows/settings": () => jsonResponse({ settings: SETTINGS, credentials: [] }),
      "PUT /api/workflows/settings": () => jsonResponse({ detail: "max_nodes must be a whole number from 10 to 200" }, { status: 422 }),
      "POST /api/workflows/credentials": () => jsonResponse({ detail: "a credential id is lowercase" }, { status: 422 }),
    });
    const user = userEvent.setup();
    render(<WorkflowSettings onClose={() => undefined} />);
    expect(await screen.findByTestId("no-creds")).toBeInTheDocument();
    await user.click(screen.getByText("Save settings"));
    expect(await screen.findByRole("alert")).toHaveTextContent("max_nodes must be");
    await user.type(screen.getByLabelText("Credential id"), "x");
    await user.type(screen.getByLabelText("Credential secret"), "v");
    await user.click(screen.getByText("Save credential"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("a credential id is lowercase"));
  });

  it("reports a settings page that cannot be loaded, and a credential that is still in use", async () => {
    routeFetch({ "GET /api/workflows/settings": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    const { unmount } = render(<WorkflowSettings onClose={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    unmount();
    routeFetch({
      "GET /api/workflows/settings": () => jsonResponse({ settings: SETTINGS, credentials: [CRED] }),
      "DELETE /api/workflows/credentials/crm": () => jsonResponse({ detail: "'crm' is used by wf_a" }, { status: 409 }),
    });
    const user = userEvent.setup();
    render(<WorkflowSettings onClose={() => undefined} />);
    await user.click(await screen.findByLabelText("Delete credential crm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("used by wf_a");
    expect(within(screen.getByTestId("wf-settings")).getAllByTestId("cred")).toHaveLength(1);
  });

  it("does nothing when saving before the settings have loaded", async () => {
    routeFetch({ "GET /api/workflows/settings": () => new Promise<Response>(() => undefined) });
    render(<WorkflowSettings onClose={() => undefined} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
