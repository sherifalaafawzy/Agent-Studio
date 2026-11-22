import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CloudAccounts from "@/components/CloudAccounts";
import { jsonResponse, routeFetch } from "../helpers/http";

const f = (o: Record<string, unknown>) => ({ label: o.name, secret: false, required: true, help: "", set: false, ...o });
const prov = (o: Record<string, unknown>) => ({ label: o.id, docs: "https://example.com/api", cost_note: `${o.id} bills hourly.`, deploy_help: "{name, image}", kinds: ["endpoints", "compute"], deletes: ["endpoints"], configured: false, fields: [], ...o });
const cloud = (over: Record<string, unknown> = {}) => ({
  providers: [
    prov({ id: "aws", label: "AWS SageMaker", configured: true, kinds: ["models", "endpoints"], fields: [f({ name: "AWS_REGION", label: "Region", set: true }), f({ name: "AWS_SECRET_ACCESS_KEY", label: "Secret", secret: true, required: false, set: true, help: "Read-only key." })] }),
    prov({ id: "runpod", label: "RunPod", fields: [f({ name: "RUNPOD_API_KEY", label: "API key", secret: true })] }),
  ], allow_writes: false, enabled: false, ...over,
});
const toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);
beforeEach(() => { toasts.length = 0; window.addEventListener("as:toast", onToast); });
afterEach(() => window.removeEventListener("as:toast", onToast));

function setup(routes: Record<string, any> = {}) {
  const r = routeFetch({ "GET /api/catalog/cloud": () => jsonResponse(cloud()), ...routes });
  render(<CloudAccounts />);
  return r;
}
const card = (id: string) => document.querySelector(`[data-provider="${id}"]`) as HTMLElement;

describe("CloudAccounts", () => {
  it("shows each provider's state, what it can look at and its cost note", async () => {
    setup();
    await screen.findByText("AWS SageMaker");
    expect(within(card("aws")).getByText("Connected")).toBeInTheDocument();
    expect(within(card("runpod")).getByText("Not connected")).toBeInTheDocument();
    expect(within(card("aws")).getByText("Can look at: models, endpoints")).toBeInTheDocument();
    expect(within(card("runpod")).getByText("Cost: runpod bills hourly.")).toBeInTheDocument();
    expect(within(card("aws")).getByRole("link", { name: "API reference" })).toHaveAttribute("href", "https://example.com/api");
  });

  it("connects an account with a write-only key", async () => {
    const { calls } = setup({ "PUT /api/catalog/cloud": () => jsonResponse(cloud()) });
    await screen.findByText("RunPod");
    await userEvent.click(within(card("runpod")).getByRole("button", { name: "Connect" }));
    const key = within(card("runpod")).getByLabelText(/API key/);
    expect(key).toHaveAttribute("type", "password");
    expect(within(card("runpod")).getByText("{name, image}")).toBeInTheDocument();
    await userEvent.type(key, " rp-secret ");
    await userEvent.click(within(card("runpod")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { RUNPOD_API_KEY: "rp-secret" } }));
    expect(toasts[0].text).toBe("RunPod saved");
  });

  it("keeps a saved value when left blank and removes it on request (and can undo that)", async () => {
    const { calls } = setup({ "PUT /api/catalog/cloud": () => jsonResponse(cloud()) });
    await screen.findByText("AWS SageMaker");
    await userEvent.click(within(card("aws")).getByRole("button", { name: "Change keys" }));
    expect(within(card("aws")).getAllByPlaceholderText("saved — leave blank to keep")).toHaveLength(2);
    expect(within(card("aws")).getByText("Read-only key.")).toBeInTheDocument();
    const secret = within(card("aws")).getByLabelText(/Secret/);
    await userEvent.click(within(card("aws")).getAllByRole("button", { name: "Remove saved value" })[1]);
    expect(secret).toBeDisabled();
    await userEvent.click(within(card("aws")).getByRole("button", { name: "Keep it" }));
    expect(secret).toBeEnabled();
    await userEvent.click(within(card("aws")).getAllByRole("button", { name: "Remove saved value" })[1]);
    await userEvent.click(within(card("aws")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { AWS_SECRET_ACCESS_KEY: "" } }));
  });

  it("shows a save error and can close the form", async () => {
    setup({ "PUT /api/catalog/cloud": () => jsonResponse({ detail: "values must be a single line" }, { status: 422 }) });
    await screen.findByText("RunPod");
    await userEvent.click(within(card("runpod")).getByRole("button", { name: "Connect" }));
    await userEvent.type(within(card("runpod")).getByLabelText(/API key/), "x");
    await userEvent.click(within(card("runpod")).getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("single line");
    await userEvent.click(within(card("runpod")).getByRole("button", { name: "Close" }));
    expect(within(card("runpod")).queryByLabelText(/API key/)).toBeNull();
  });

  it("allow create/delete is off, needs a confirmation to turn on, and turns off freely", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    let on = false;
    const { calls } = setup({ "PUT /api/catalog/cloud": (init: RequestInit | undefined) => { on = JSON.parse(String(init?.body)).allow_writes; return jsonResponse(cloud({ allow_writes: on })); } });
    const box = await screen.findByRole("checkbox", { name: /Allow create and delete/ });
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    await userEvent.click(box);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ allow_writes: true }));
    await waitFor(() => expect(toasts.at(-1)?.text).toBe("Create/delete allowed"));
    await userEvent.click(await screen.findByRole("checkbox", { name: /Allow create and delete/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ allow_writes: false }));
    expect(confirm).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(toasts.at(-1)?.text).toBe("Back to read-only"));
  });

  it("reports a refused write switch", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "PUT /api/catalog/cloud": () => jsonResponse({ detail: "nope" }, { status: 422 }) });
    await userEvent.click(await screen.findByRole("checkbox", { name: /Allow create and delete/ }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "nope", tone: "err" }));
  });

  it("lets agents use it with one switch", async () => {
    const { calls } = setup({ "PUT /api/mcp/cloudml": () => jsonResponse({}) });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Let agents use it" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname === "/api/mcp/cloudml")?.body).toEqual({ enabled: true }));
  });

  it("reports a refused enable and an offline backend", async () => {
    setup({ "PUT /api/mcp/cloudml": () => jsonResponse({ detail: "unknown" }, { status: 404 }) });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Let agents use it" }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "unknown", tone: "err" }));
  });

  it("shows an offline message", async () => {
    routeFetch({ "GET /api/catalog/cloud": () => { throw new Error("refused"); } });
    render(<CloudAccounts />);
    expect(await screen.findByText(/Backend offline: refused/)).toBeInTheDocument();
  });
});
