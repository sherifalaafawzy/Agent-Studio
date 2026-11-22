import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import RuntimeSettings from "@/components/RuntimeSettings";
import { jsonResponse, routeFetch } from "../helpers/http";

const LOCAL = { id: "local", label: "This PC", docs: "", remote: false, cost_note: "", platform: "", side_effects: [], fields: [], enabled: true, values: {}, secrets_set: [], configured: true, problem: "" };
const AC = { id: "agentcore", label: "Amazon Bedrock AgentCore Runtime", docs: "https://docs.example/agentcore", remote: true, cost_note: "Bills while sessions run.", platform: "linux/arm64",
  side_effects: ["Creates a runtime in your AWS account."], enabled: false, values: { region: "us-east-1" }, secrets_set: ["secret_access_key"], configured: false, problem: "AgentCore is not set up: missing Execution role ARN (Settings > Runtimes)",
  fields: [{ name: "region", label: "AWS region", secret: false, required: true, help: "e.g. us-east-1", default: "" }, { name: "network", label: "Network mode", secret: false, required: false, help: "", default: "PUBLIC" },
    { name: "secret_access_key", label: "Secret access key", secret: true, required: false, help: "", default: "" }, { name: "access_key_id", label: "Access key id", secret: true, required: false, help: "", default: "" }] };

function setup(over: Record<string, any> = {}) {
  let o: any = { targets: [LOCAL, { ...AC }], image_template: "", model_env_set: ["OPENAI_API_KEY"], ...over };
  return routeFetch({
    "GET /api/runtimes": () => jsonResponse(o),
    "PUT /api/runtimes/config": (init: any) => {
      const p = JSON.parse(init.body);
      if (p.image_template === "bad image") return jsonResponse({ detail: "the image template must look like registry.example.com/agents:{agent}-{tag}" }, { status: 422 });
      if (p.targets?.agentcore) o = { ...o, targets: o.targets.map((t: any) => t.id === "agentcore" ? { ...t, enabled: p.targets.agentcore.enabled, values: { ...t.values, ...p.targets.agentcore }, secrets_set: Object.keys(p.secrets?.agentcore ?? t.secrets_set) } : t) };
      if (p.image_template !== undefined) o = { ...o, image_template: p.image_template };
      if (p.secrets?.model_env) o = { ...o, model_env_set: [...o.model_env_set, ...Object.keys(p.secrets.model_env)] };
      if (p.clear_model_env) o = { ...o, model_env_set: o.model_env_set.filter((n: string) => !p.clear_model_env.includes(n)) };
      return jsonResponse(o);
    },
    "POST /api/runtimes/targets/agentcore/test": () => jsonResponse({ ok: true, detail: "signed in to AWS" }),
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("RuntimeSettings", () => {
  it("shows the targets with cost, side effects and what is missing, but never the local one", async () => {
    setup();
    render(<RuntimeSettings />);
    const card = await screen.findByTestId("runtime-agentcore");
    expect(within(card).getByText("Bills while sessions run.")).toBeInTheDocument();
    expect(within(card).getByText("Creates a runtime in your AWS account.")).toBeInTheDocument();
    expect(within(card).getByText("image: linux/arm64")).toBeInTheDocument();
    expect(within(card).getByText("off")).toBeInTheDocument();
    expect(screen.queryByTestId("runtime-local")).toBeNull();
    expect(within(card).getByPlaceholderText("(stored: leave empty to keep)")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Documentation" })).toHaveAttribute("href", "https://docs.example/agentcore");
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
  });

  it("switches a target on, saving its values, and sends new secrets only when typed", async () => {
    const net = setup();
    render(<RuntimeSettings />);
    const card = await screen.findByTestId("runtime-agentcore");
    await userEvent.clear(within(card).getByLabelText(/AWS region/));
    await userEvent.type(within(card).getByLabelText(/AWS region/), "eu-west-1");
    await userEvent.type(within(card).getByLabelText(/Access key id/), "AKIA1");
    await userEvent.click(within(card).getByRole("checkbox", { name: "agentcore on" }));
    const put = net.calls.filter((c) => c.method === "PUT").pop()!.body;
    expect(put.targets.agentcore).toMatchObject({ region: "eu-west-1", enabled: true });
    expect(put.secrets).toEqual({ agentcore: { access_key_id: "AKIA1" } });
    expect(await screen.findByText("on")).toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId("runtime-agentcore")).getByRole("button", { name: "Save" }));
    const again = net.calls.filter((c) => c.method === "PUT").pop()!.body;
    expect(again.secrets).toBeUndefined();                                   // typed secrets were cleared after the first save
  });

  it("warns when an enabled target is not set up, and tests the connection", async () => {
    setup({ targets: [LOCAL, { ...AC, enabled: true }] });
    render(<RuntimeSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("missing Execution role ARN");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByRole("status")).toHaveTextContent("signed in to AWS");
  });

  it("reports a failed test as text", async () => {
    routeFetch({ "GET /api/runtimes": () => jsonResponse({ targets: [LOCAL, AC], image_template: "", model_env_set: [] }), "POST /api/runtimes/targets/agentcore/test": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    render(<RuntimeSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Test" }));
    expect(await screen.findByRole("status")).toHaveTextContent("boom");
  });

  it("saves the image template and shows the server's reason when it is refused", async () => {
    setup();
    render(<RuntimeSettings />);
    const input = await screen.findByLabelText(/Image address template/);
    const btn = screen.getByRole("button", { name: "Save address" });
    expect(btn).toBeDisabled();
    await userEvent.type(input, "bad image");
    await userEvent.click(btn);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("the image template must look like");
    await userEvent.clear(input);
    await userEvent.type(input, "reg.io/a:{{agent}}-{{tag}}");
    await userEvent.click(btn);
    await waitFor(() => expect(screen.queryByText(/must look like registry/)).toBeNull());
  });

  it("adds and removes the model keys a deployed agent uses, without ever showing a value", async () => {
    const net = setup();
    render(<RuntimeSettings />);
    await screen.findByText("OPENAI_API_KEY");
    const add = screen.getByRole("button", { name: "Add key" });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Key name"), "anthropic_api_key");
    await userEvent.type(screen.getByLabelText("Key value"), "sk-secret");
    await userEvent.click(add);
    expect(net.calls.filter((c) => c.method === "PUT").pop()!.body).toEqual({ secrets: { model_env: { ANTHROPIC_API_KEY: "sk-secret" } } });
    expect(await screen.findByText("ANTHROPIC_API_KEY")).toBeInTheDocument();
    expect(screen.queryByText("sk-secret")).toBeNull();
    expect((screen.getByLabelText("Key name") as HTMLInputElement).value).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Remove OPENAI_API_KEY" }));
    await waitFor(() => expect(screen.queryByText("OPENAI_API_KEY")).toBeNull());
  });

  it("says when no model key is set and when loading fails", async () => {
    setup({ model_env_set: [] });
    const { unmount } = render(<RuntimeSettings />);
    expect(await screen.findByText("None set.")).toBeInTheDocument();
    unmount();
    routeFetch({ "GET /api/runtimes": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<RuntimeSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
