import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DataProducts from "@/components/DataProducts";
import { jsonResponse, routeFetch } from "../helpers/http";

const PRODUCT = { id: "orders", name: "Customer orders", description: "All orders", source: "crm", objects: ["orders", "items"], classification: "restricted", owner: "Sales Ops", contains_pii: true, tags: [] };
const PLAIN = { id: "catalog", name: "Product catalog", description: "", source: "crm", objects: [], classification: "public", owner: "", contains_pii: false, tags: [] };
const SOURCES = [{ id: "crm", label: "CRM", kind: "postgres", enabled: true }, { id: "dwh", label: "Warehouse", kind: "snowflake", enabled: true }];
const AGENTS = [
  { id: "analyst", name: "Analyst", kind: "agent", purpose: "Answers sales questions.", framework: "adk", runtime: "local", sandbox: "default", data_sources: ["crm"], data_products: ["orders", "catalog"] },
  { id: "writer", name: "Writer", kind: "agent", purpose: "", framework: "langgraph", runtime: "agentcore", sandbox: "docker-microvm", data_sources: [], data_products: [] },
  { id: "claude", name: "Claude Code", kind: "external", purpose: "coding agent", launcher: "acp", used_by: ["analyst"], data_sources: [], data_products: [] },
  { id: "cursor", name: "Cursor", kind: "external", purpose: "editor agent", data_sources: [], data_products: [] },
];

function setup(over: Record<string, any> = {}) {
  let products: any[] = over.products ?? [PRODUCT, PLAIN];
  return routeFetch({
    "GET /api/data/descriptors": () => jsonResponse({ agents: AGENTS, products, sources: over.sources ?? SOURCES }),
    "PUT /api/data/products": (init: any) => {
      const b = JSON.parse(init.body);
      if (b.id === "bad") return jsonResponse({ detail: "classification must be one of: public" }, { status: 422 });
      products = [...products.filter((p) => p.id !== b.id), { ...b }];
      return jsonResponse(b);
    },
    "DELETE /api/data/products/orders": () => { products = products.filter((p) => p.id !== "orders"); return jsonResponse({ ok: true }); },
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const lastPut = (net: ReturnType<typeof setup>) => net.calls.filter((c) => c.method === "PUT").pop()!.body;

describe("DataProducts", () => {
  it("lists the products with their sensitivity, owner and objects", async () => {
    setup();
    render(<DataProducts />);
    const rows = await screen.findAllByTestId("product");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Customer orders");
    expect(within(rows[0]).getByText("restricted")).toBeInTheDocument();
    expect(within(rows[0]).getByText("personal data")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("crm · orders, items · owner: Sales Ops");
    expect(rows[1]).toHaveTextContent("public");
    expect(within(rows[1]).queryByText("personal data")).toBeNull();
  });

  it("shows which agent does what and which products each can read, including external agents", async () => {
    setup();
    render(<DataProducts />);
    const table = await screen.findByTestId("agent-matrix");
    const row = (name: string) => within(table).getByText(name).closest("tr")!;
    expect(row("Analyst")).toHaveTextContent("Answers sales questions.");
    expect(row("Analyst")).toHaveTextContent("this PC · adk · default sandbox");
    expect(within(row("Analyst")).getByLabelText("Analyst Customer orders")).toHaveTextContent("✓");
    expect(row("Writer")).toHaveTextContent("no description");
    expect(row("Writer")).toHaveTextContent("agentcore · langgraph · docker-microvm sandbox");
    expect(within(row("Writer")).getByLabelText("Writer Customer orders")).toHaveTextContent("–");
    expect(row("Claude Code")).toHaveTextContent("outside Agent Studio (acp)");
    expect(row("Claude Code")).toHaveTextContent("called by analyst");
    expect(within(row("Claude Code")).getByLabelText("Claude Code Customer orders")).toHaveTextContent("?");
    expect(row("Cursor")).toHaveTextContent("outside Agent Studio");
    expect(row("Cursor")).not.toHaveTextContent("(acp)");
    expect(row("Cursor")).not.toHaveTextContent("called by");
  });

  it("adds a product, and shows the server's reason when it is refused", async () => {
    const net = setup();
    render(<DataProducts />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a data product" }));
    const form = screen.getByTestId("product-form");
    const save = within(form).getByRole("button", { name: "Save data product" });
    expect(save).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Id"), "Bad");
    await userEvent.type(within(form).getByLabelText("Name"), "Bad one");
    await userEvent.click(save);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("classification must be one of");
    await userEvent.clear(within(form).getByLabelText("Id"));
    await userEvent.type(within(form).getByLabelText("Id"), "invoices");
    await userEvent.clear(within(form).getByLabelText("Name"));
    await userEvent.type(within(form).getByLabelText("Name"), "Invoices");
    await userEvent.selectOptions(within(form).getByLabelText("Data source"), "dwh");
    await userEvent.selectOptions(within(form).getByLabelText("How sensitive is it?"), "confidential");
    await userEvent.type(within(form).getByLabelText("Owner"), "Finance");
    await userEvent.type(within(form).getByLabelText(/Tables or objects/), "invoices, , lines");
    await userEvent.type(within(form).getByLabelText("What it is"), "Billing");
    await userEvent.click(within(form).getByLabelText("Contains personal data"));
    await userEvent.click(save);
    await waitFor(() => expect(screen.queryByTestId("product-form")).toBeNull());
    expect(lastPut(net)).toEqual({ id: "invoices", name: "Invoices", description: "Billing", source: "dwh", objects: ["invoices", "lines"], classification: "confidential", owner: "Finance", contains_pii: true, tags: [] });
    await waitFor(() => expect(screen.getAllByTestId("product")).toHaveLength(3));
  });

  it("closes the add form without saving", async () => {
    const net = setup();
    render(<DataProducts />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a data product" }));
    expect(screen.queryByRole("button", { name: "Add a data product" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("product-form")).toBeNull();
    expect(net.calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("edits a product with its id fixed, and cancels", async () => {
    const net = setup();
    render(<DataProducts />);
    const rows = await screen.findAllByTestId("product");
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Edit" }));
    const form = screen.getByTestId("product-form");
    expect(within(form).getByLabelText("Id")).toBeDisabled();
    expect((within(form).getByLabelText(/Tables or objects/) as HTMLInputElement).value).toBe("orders, items");
    await userEvent.clear(within(form).getByLabelText("Name"));
    await userEvent.type(within(form).getByLabelText("Name"), "Orders v2");
    await userEvent.click(within(form).getByRole("button", { name: "Save data product" }));
    await waitFor(() => expect(screen.queryByTestId("product-form")).toBeNull());
    expect(lastPut(net)).toMatchObject({ id: "orders", name: "Orders v2", objects: ["orders", "items"] });
    await userEvent.click(within((await screen.findAllByTestId("product"))[0]).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("product-form")).toBeNull();
    expect(screen.getByRole("button", { name: "Add a data product" })).toBeInTheDocument();
  });

  it("removes a product only after a confirmation, and shows a failure", async () => {
    const net = setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<DataProducts />);
    const first = async () => within((await screen.findAllByTestId("product"))[0]).getByRole("button", { name: "Remove" });
    await userEvent.click(await first());
    expect(net.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(await first());
    await waitFor(() => expect(screen.getAllByTestId("product")).toHaveLength(1));
    expect(confirm.mock.calls[0][0]).toMatch(/The data itself is not touched/);
    net.fn.mockImplementation(async (input: any, init: any) => init?.method === "DELETE" ? jsonResponse({ detail: "no data product called 'catalog'" }, { status: 404 })
      : jsonResponse({ agents: AGENTS, products: [PLAIN], sources: SOURCES }));
    await userEvent.click(await first());
    expect(await screen.findByRole("alert")).toHaveTextContent("no data product called 'catalog'");
  });

  it("asks you to add a data source first when there is none", async () => {
    setup({ sources: [], products: [] });
    render(<DataProducts />);
    expect(await screen.findByText(/Add a data source in/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a data product" })).toBeNull();
  });

  it("shows an error when the descriptors cannot be loaded", async () => {
    routeFetch({ "GET /api/data/descriptors": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<DataProducts />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
