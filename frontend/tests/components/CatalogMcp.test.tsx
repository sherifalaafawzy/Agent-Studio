import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CatalogMcp, { MODE_LABEL, type CatalogServer } from "@/components/CatalogMcp";
import { jsonResponse, routeFetch } from "../helpers/http";

const field = (o: Partial<CatalogServer["fields"][number]> & { name: string; label: string }): CatalogServer["fields"][number] => ({ secret: false, required: true, where: "env", help: "", placeholder: "", default: "", ...o });
const server = (o: Partial<CatalogServer> & { id: string }): CatalogServer => ({
  label: o.id, domains: ["data"], publisher: "Acme", official: true, description: `${o.id} description`, kind: "pypi", package: "x==1", read_only: "always", writes: "Nothing.",
  data_leaves: "Nothing leaves the PC.", license: "MIT", docs: "https://example.com/docs", verified: "PyPI x 1.0", transport: "stdio", command_line: "uvx x==1", fields: [], setup: "",
  can_be_read_only: true, installed: false, allow_writes: false, ready: false, ...o,
});
const LIST = [
  server({ id: "tavily", label: "Tavily", domains: ["marketing"], fields: [field({ name: "TAVILY_API_KEY", label: "API key", secret: true, help: "Free tier." })], setup: "Create a key first." }),
  server({ id: "xero", label: "Xero", domains: ["accounting"], official: false, read_only: "none", writes: "Creates invoices.", can_be_read_only: false, fields: [field({ name: "XERO_CLIENT_ID", label: "Client id" })] }),
  server({ id: "clickhouse", label: "ClickHouse", read_only: "enforced", fields: [field({ name: "CLICKHOUSE_PORT", label: "Port", required: false, default: "8123" })] }),
  server({ id: "postgres", label: "PostgreSQL", read_only: "enforced", installed: true, ready: true, fields: [field({ name: "DATABASE_URI", label: "Connection string", secret: true })] }),
  server({ id: "mongodb", label: "MongoDB", read_only: "enforced", installed: true, ready: false, allow_writes: true, writes: "Writes documents." }),
  server({ id: "hubspot", label: "HubSpot", read_only: "credential", domains: ["sales"], installed: true, ready: true }),
  server({ id: "odd", label: "Odd", domains: ["quantum"] }),
];
const toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);
beforeEach(() => { toasts.length = 0; window.addEventListener("as:toast", onToast); });
afterEach(() => window.removeEventListener("as:toast", onToast));

function setup(routes: Record<string, any> = {}, props = { domain: "", q: "" }) {
  const r = routeFetch({ "GET /api/catalog/mcp": () => jsonResponse(LIST), ...routes });
  const ui = render(<CatalogMcp {...props} />);
  return { ...r, ...ui };
}
const card = (id: string) => document.querySelector(`[data-server="${id}"]`) as HTMLElement;

describe("CatalogMcp: browsing", () => {
  it("shows what each server can do to your data", async () => {
    setup();
    await screen.findByText("Tavily");
    expect(within(card("tavily")).getByText("Read-only")).toBeInTheDocument();
    expect(within(card("tavily")).getByText(/Acme · official/)).toBeInTheDocument();
    expect(within(card("xero")).getByText("Can change things")).toBeInTheDocument();
    expect(within(card("xero")).getByText(/Acme · community/)).toBeInTheDocument();
    expect(within(card("clickhouse")).getByText("Read-only switch")).toBeInTheDocument();
    expect(within(card("hubspot")).getByText("Limited by the key")).toBeInTheDocument();
    expect(MODE_LABEL.partial.text).toBe("Limited writes");
    expect(within(card("odd")).getByText("quantum")).toBeInTheDocument();                       // an unknown domain still shows its raw name
  });

  it("filters by domain and text; manufacturing explains why the list is short", async () => {
    const { rerender } = setup({}, { domain: "accounting", q: "" });
    await screen.findByText("Xero");
    expect(screen.queryByText("Tavily")).toBeNull();
    rerender(<CatalogMcp domain="" q="description" />);
    expect(await screen.findByText("Tavily")).toBeInTheDocument();
    rerender(<CatalogMcp domain="manufacturing" q="" />);
    expect(screen.getByText("No servers match.")).toBeInTheDocument();
    expect(screen.getByText(/Manufacturing has few public servers/)).toBeInTheDocument();
  });

  it("says when the backend is offline", async () => {
    routeFetch({ "GET /api/catalog/mcp": () => { throw new Error("refused"); } });
    render(<CatalogMcp domain="" q="" />);
    expect(await screen.findByText(/Backend offline: refused/)).toBeInTheDocument();
  });
});

describe("CatalogMcp: adding a server", () => {
  it("a read-only server is added with just its key", async () => {
    const { calls } = setup({ "POST /api/catalog/mcp/tavily/install": () => jsonResponse({ id: "tavily" }) });
    await screen.findByText("Tavily");
    await userEvent.click(within(card("tavily")).getByRole("button", { name: "Add" }));
    expect(within(card("tavily")).getByText("Nothing.")).toBeInTheDocument();
    expect(within(card("tavily")).getByText("Create a key first.")).toBeInTheDocument();
    expect(within(card("tavily")).queryByRole("checkbox")).toBeNull();
    const key = within(card("tavily")).getByLabelText(/API key/);
    expect(key).toHaveAttribute("type", "password");
    await userEvent.type(key, "tvly-123");
    await userEvent.click(within(card("tavily")).getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ values: { TAVILY_API_KEY: "tvly-123" }, allow_writes: false }));
    expect(toasts[0].text).toBe("Tavily added");
  });

  it("a server that can change things needs the tick first", async () => {
    const { calls } = setup({ "POST /api/catalog/mcp/xero/install": () => jsonResponse({}) });
    await screen.findByText("Xero");
    await userEvent.click(within(card("xero")).getByRole("button", { name: "Add" }));
    expect(within(card("xero")).getByText("Creates invoices.")).toBeInTheDocument();
    const add = within(card("xero")).getByRole("button", { name: "Add server" });
    expect(add).toBeDisabled();
    await userEvent.type(within(card("xero")).getByLabelText(/Client id/), "abc");
    await userEvent.click(within(card("xero")).getByRole("checkbox"));
    expect(add).toBeEnabled();
    await userEvent.click(add);
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ values: { XERO_CLIENT_ID: "abc" }, allow_writes: true }));
  });

  it("an enforced read-only server starts read-only and prefills its defaults", async () => {
    const { calls } = setup({ "POST /api/catalog/mcp/clickhouse/install": () => jsonResponse({}) });
    await screen.findByText("ClickHouse");
    await userEvent.click(within(card("clickhouse")).getByRole("button", { name: "Add" }));
    expect(within(card("clickhouse")).getByLabelText(/Port/)).toHaveValue("8123");
    expect(within(card("clickhouse")).getByRole("button", { name: "Add server" })).toBeEnabled();
    expect(within(card("clickhouse")).getByText(/leave off to keep it read-only/)).toBeInTheDocument();
    await userEvent.click(within(card("clickhouse")).getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ values: { CLICKHOUSE_PORT: "8123" }, allow_writes: false }));
  });

  it("shows the server's refusal in the panel and can close it", async () => {
    setup({ "POST /api/catalog/mcp/tavily/install": () => jsonResponse({ detail: "fill in: API key" }, { status: 422 }) });
    await screen.findByText("Tavily");
    await userEvent.click(within(card("tavily")).getByRole("button", { name: "Add" }));
    await userEvent.click(within(card("tavily")).getByRole("button", { name: "Add server" }));
    expect(await within(card("tavily")).findByRole("alert")).toHaveTextContent("fill in: API key");
    expect(within(card("tavily")).getByText("How it is started and checked")).toBeInTheDocument();
    await userEvent.click(within(card("tavily")).getByRole("button", { name: "Close" }));
    expect(within(card("tavily")).queryByRole("alert")).toBeNull();
  });
});

describe("CatalogMcp: an added server", () => {
  it("can be tested: tools, failure and exception", async () => {
    let n = 0;
    setup({ "POST /api/mcp/postgres/test": () => {
      n += 1;
      if (n === 1) return jsonResponse({ ok: true, tools: Array.from({ length: 7 }, (_, i) => ({ name: `t${i}` })) });
      if (n === 2) return jsonResponse({ ok: false });
      if (n === 3) return jsonResponse({ ok: false, error: "needs uvx" });
      return jsonResponse({ detail: "boom" }, { status: 500 });
    } });
    await screen.findByText("PostgreSQL");
    const btn = () => within(card("postgres")).getByRole("button", { name: "Test" });
    await userEvent.click(btn());
    expect(await within(card("postgres")).findByText(/Works: 7 tools \(t0, t1, t2, t3, t4, …\)/)).toBeInTheDocument();
    await userEvent.click(btn());
    expect(await within(card("postgres")).findByText("did not start")).toBeInTheDocument();
    await userEvent.click(btn());
    expect(await within(card("postgres")).findByText("needs uvx")).toBeInTheDocument();
    await userEvent.click(btn());
    expect(await within(card("postgres")).findByText("boom")).toBeInTheDocument();
  });

  it("a short tool list has no ellipsis and a missing list counts zero", async () => {
    let n = 0;
    setup({ "POST /api/mcp/postgres/test": () => (++n === 1 ? jsonResponse({ ok: true, tools: [{ name: "a" }] }) : jsonResponse({ ok: true })) });
    await screen.findByText("PostgreSQL");
    await userEvent.click(within(card("postgres")).getByRole("button", { name: "Test" }));
    expect(await within(card("postgres")).findByText("Works: 1 tools (a)")).toBeInTheDocument();
    await userEvent.click(within(card("postgres")).getByRole("button", { name: "Test" }));
    expect(await within(card("postgres")).findByText("Works: 0 tools ()")).toBeInTheDocument();
  });

  it("switches a read-only server to writes only after a confirmation, and back without one", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    const { calls } = setup({ "PUT /api/catalog/mcp/postgres/mode": () => jsonResponse({}), "PUT /api/catalog/mcp/mongodb/mode": () => jsonResponse({}) });
    await screen.findByText("PostgreSQL");
    await userEvent.click(within(card("postgres")).getByRole("button", { name: "Allow writes…" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    await userEvent.click(within(card("postgres")).getByRole("button", { name: "Allow writes…" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ allow_writes: true }));
    await waitFor(() => expect(toasts.at(-1)?.text).toBe("Writes allowed"));
    await userEvent.click(within(card("mongodb")).getByRole("button", { name: "Make read-only" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ allow_writes: false }));
    expect(confirm).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(toasts.at(-1)?.text).toBe("Back to read-only"));
  });

  it("reports a refused mode change", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "PUT /api/catalog/mcp/postgres/mode": () => jsonResponse({ detail: "cannot" }, { status: 422 }) });
    await screen.findByText("PostgreSQL");
    await userEvent.click(within(card("postgres")).getByRole("button", { name: "Allow writes…" }));
    await waitFor(() => expect(toasts.at(-1)).toEqual({ text: "cannot", tone: "err" }));
  });

  it("shows needs-setup, offers no mode switch for key-limited servers and reopens settings with the saved choice", async () => {
    setup();
    await screen.findByText("MongoDB");
    expect(within(card("mongodb")).getByText("Added · needs setup")).toBeInTheDocument();
    expect(within(card("hubspot")).queryByRole("button", { name: /writes/i })).toBeNull();
    await userEvent.click(within(card("mongodb")).getByRole("button", { name: "Change settings" }));
    expect(within(card("mongodb")).getByText(/enter the values again/)).toBeInTheDocument();
    expect(within(card("mongodb")).getByRole("checkbox")).toBeChecked();
    expect(within(card("mongodb")).getByRole("button", { name: "Save" })).toBeEnabled();
    await userEvent.click(within(card("mongodb")).getByRole("button", { name: "Close" }));
  });
});
