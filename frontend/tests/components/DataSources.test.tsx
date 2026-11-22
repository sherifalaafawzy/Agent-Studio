import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DataSources from "@/components/DataSources";
import { jsonResponse, routeFetch } from "../helpers/http";

const KINDS = [
  { kind: "sqlite", label: "SQLite file", description: "A SQLite file.", docs: "", note: "", managed_mcp: null, fields: [{ name: "path", label: "Database file", secret: false, required: true, help: "Full path", default: "" }] },
  { kind: "postgres", label: "PostgreSQL", description: "A Postgres database.", docs: "", note: "Use a SELECT-only user.", managed_mcp: null,
    fields: [{ name: "host", label: "Host", secret: false, required: true, help: "", default: "" }, { name: "password", label: "Password", secret: true, required: false, help: "", default: "" }] },
  { kind: "bigquery", label: "Google BigQuery", description: "BigQuery.", docs: "", note: "", fields: [{ name: "project", label: "Project id", secret: false, required: true, help: "", default: "" }],
    managed_mcp: { url: "https://bigquery.googleapis.com/mcp", tools: "execute_sql_readonly", status: "Google-hosted", docs: "https://docs.example/bq", advice: "Use execute_sql_readonly." } },
];
const LIM = { max_rows: 100, max_bytes: 200000, timeout_s: 30 };
const SRC = { id: "crm", kind: "postgres", label: "CRM", description: "", config: { host: "db.local" }, limits: LIM, redact: true, enabled: true, secrets_set: ["password"] };
const BQ = { id: "bq", kind: "bigquery", label: "Analytics", description: "", config: { project: "p" }, limits: LIM, redact: false, enabled: false, secrets_set: [] };

function setup(sources: any[] = [SRC, BQ]) {
  let list = sources;
  return routeFetch({
    "GET /api/data/sources": () => jsonResponse({ sources: list, kinds: KINDS }),
    "PUT /api/data/sources": (init: any) => {
      const b = JSON.parse(init.body);
      if (b.id === "bad") return jsonResponse({ detail: "PostgreSQL is not set up: missing Host" }, { status: 422 });
      list = [...list.filter((s) => s.id !== b.id), { ...(list.find((s) => s.id === b.id) ?? { description: "", limits: LIM, redact: true, enabled: true, label: b.label || b.id }), ...b, secrets_set: Object.keys(b.secrets ?? {}), limits: { ...LIM, ...(b.limits ?? {}) } }];
      return jsonResponse(list[list.length - 1]);
    },
    "DELETE /api/data/sources/crm": () => { list = list.filter((s) => s.id !== "crm"); return jsonResponse({ ok: true }); },
    "POST /api/data/sources/crm/test": () => jsonResponse({ ok: true, detail: "connected to PostgreSQL" }),
    "POST /api/data/sources/bq/test": () => jsonResponse({ detail: "BigQuery refused the credentials" }, { status: 500 }),
    "GET /api/data/sources/crm/schema": () => jsonResponse({ tables: [{ name: "public.orders", kind: "base table" }] }),
    "GET /api/data/sources/crm/schema?table=public.orders": () => jsonResponse({ columns: [{ name: "id", type: "integer" }, { name: "note", type: "" }] }),
    "POST /api/data/sources/crm/query": (init: any) => JSON.parse(init.body).sql.includes("DELETE")
      ? jsonResponse({ detail: "only reads are allowed" }, { status: 422 })
      : jsonResponse({ columns: ["id", "email"], rows: [[1, "[email removed]"], [2, null]], row_count: 2, truncated: true, redacted: true }),
    "GET /api/data/audit?source=crm&limit=10": () => jsonResponse({ audit: [{ id: 2, ts: 1700000000, agent: "bot", kind: "query", statement: "SELECT 1", rows: 1, ms: 5, ok: 1, error: "" },
      { id: 1, ts: 1700000001, agent: "", kind: "test", statement: "", rows: 0, ms: 9, ok: 0, error: "cannot connect" }] }),
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const lastPut = (net: ReturnType<typeof setup>) => net.calls.filter((c) => c.method === "PUT").pop()!.body;

describe("DataSources", () => {
  it("lists the sources with their kind and the vendor's own MCP server when there is one", async () => {
    setup();
    render(<DataSources />);
    const cards = await screen.findAllByTestId("source");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(cards[0]).queryByText(/official MCP server/)).toBeNull();
    expect(within(cards[1]).getByText(/official MCP server \(Google-hosted\)/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("https://bigquery.googleapis.com/mcp")).toBeInTheDocument();
    expect(screen.getByText(/Read-only: anything that is not a plain query is refused/)).toBeInTheDocument();
  });

  it("switches a source on or off and its masking, and tests it", async () => {
    const net = setup();
    render(<DataSources />);
    await screen.findAllByTestId("source");
    await userEvent.click(screen.getByRole("checkbox", { name: "crm on" }));
    expect(lastPut(net)).toEqual({ id: "crm", kind: "postgres", enabled: false });
    await userEvent.click(screen.getByRole("checkbox", { name: "bq mask personal data" }));
    expect(lastPut(net)).toEqual({ id: "bq", kind: "bigquery", redact: true });
    const crm = (await screen.findAllByTestId("source")).find((c) => within(c).queryByText("CRM"))!;
    await userEvent.click(within(crm).getByRole("button", { name: "Test" }));
    expect(await within(crm).findByRole("status")).toHaveTextContent("connected to PostgreSQL");
    const bq = screen.getAllByTestId("source").find((c) => within(c).queryByText("Analytics"))!;
    await userEvent.click(within(bq).getByRole("button", { name: "Test" }));
    expect(await within(bq).findByRole("status")).toHaveTextContent("BigQuery refused the credentials");
  });

  it("adds a source with write-only secrets, and shows the server's reason when it is refused", async () => {
    const net = setup([]);
    render(<DataSources />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a data source" }));
    const form = screen.getByTestId("source-form");
    expect(within(form).getByRole("button", { name: "Add source" })).toBeDisabled();
    await userEvent.selectOptions(within(form).getByLabelText("Kind"), "postgres");
    expect(within(form).getByText(/Use a SELECT-only user/)).toBeInTheDocument();
    await userEvent.type(within(form).getByLabelText("Name"), "Bad");
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("missing Host");
    await userEvent.clear(within(form).getByLabelText("Name"));
    await userEvent.type(within(form).getByLabelText("Name"), "CRM-Prod");
    await userEvent.type(within(form).getByLabelText("Host"), "db.example.com");
    await userEvent.type(within(form).getByLabelText(/Password/), "hunter2");
    await userEvent.clear(within(form).getByLabelText("Most rows per query"));
    await userEvent.type(within(form).getByLabelText("Most rows per query"), "25");
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    await waitFor(() => expect(screen.queryByTestId("source-form")).toBeNull());
    expect(lastPut(net)).toEqual({ id: "crm-prod", kind: "postgres", label: "", config: { host: "db.example.com" }, secrets: { password: "hunter2" }, limits: { max_rows: 25 } });
    expect(await screen.findByText("crm-prod")).toBeInTheDocument();
    expect(screen.queryByText("hunter2")).toBeNull();
  });

  it("edits a source: the name and kind are fixed and a stored secret is only replaced when typed", async () => {
    const net = setup();
    render(<DataSources />);
    const crm = (await screen.findAllByTestId("source")).find((c) => within(c).queryByText("CRM"))!;
    await userEvent.click(within(crm).getByRole("button", { name: "Edit" }));
    const form = screen.getByTestId("source-form");
    expect(within(form).getByLabelText("Name")).toBeDisabled();
    expect(within(form).getByLabelText("Kind")).toBeDisabled();
    expect(within(form).getByPlaceholderText("(stored: leave empty to keep)")).toBeInTheDocument();
    await userEvent.clear(within(form).getByLabelText("Label (optional)"));
    await userEvent.type(within(form).getByLabelText("Label (optional)"), "CRM prod");
    await userEvent.click(within(form).getByRole("button", { name: "Save source" }));
    await waitFor(() => expect(screen.queryByTestId("source-form")).toBeNull());
    expect(lastPut(net)).toMatchObject({ id: "crm", kind: "postgres", label: "CRM prod", config: { host: "db.local" }, secrets: {} });
    await userEvent.click(within((await screen.findAllByTestId("source"))[0]).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("source-form")).toBeNull();
  });

  it("closes the add form without saving", async () => {
    setup([]);
    render(<DataSources />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a data source" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("source-form")).toBeNull();
  });

  it("removes a source only after a confirmation", async () => {
    const net = setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<DataSources />);
    const crm = () => screen.getAllByTestId("source").find((c) => within(c).queryByText("CRM"))!;
    await screen.findAllByTestId("source");
    await userEvent.click(within(crm()).getByRole("button", { name: "Remove" }));
    expect(net.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(crm()).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.getAllByTestId("source")).toHaveLength(1));
    expect(confirm.mock.calls[0][0]).toMatch(/credentials and the data products/);
  });

  it("shows a removal error as text", async () => {
    const net = setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    net.fn.mockImplementation(async (input: any, init: any) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      if (init?.method === "DELETE") return jsonResponse({ detail: "no data source called 'crm'" }, { status: 404 });
      return jsonResponse({ sources: [SRC], kinds: KINDS });
    });
    render(<DataSources />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no data source called 'crm'");
  });

  it("explores a source: browse tables, see columns, run a read-only query and read the activity log", async () => {
    setup();
    render(<DataSources />);
    const crm = (await screen.findAllByTestId("source")).find((c) => within(c).queryByText("CRM"))!;
    await userEvent.click(within(crm).getByRole("button", { name: "Explore" }));
    const ex = within(crm).getByTestId("explorer");
    expect(within(ex).getByRole("button", { name: "Run" })).toBeDisabled();
    await userEvent.click(within(ex).getByRole("button", { name: "Browse tables" }));
    await userEvent.click(await within(ex).findByRole("button", { name: "public.orders" }));
    expect(await within(ex).findByTestId("columns")).toHaveTextContent("id (integer), note (?)");
    expect((within(ex).getByLabelText("Query") as HTMLTextAreaElement).value).toBe("SELECT * FROM public.orders LIMIT 10");
    await userEvent.click(within(ex).getByRole("button", { name: "Run" }));
    const res = await within(ex).findByTestId("result");
    expect(res).toHaveTextContent("[email removed]");
    expect(res).toHaveTextContent("null");
    expect(res).toHaveTextContent("2 rows (more were cut off by the limits) · some values were masked");
    await userEvent.click(within(ex).getByRole("button", { name: "Recent activity" }));
    const audit = await within(ex).findByTestId("audit");
    expect(audit).toHaveTextContent("bot · query · SELECT 1 · 1 rows, 5 ms");
    expect(audit).toHaveTextContent("system · test · (no statement) · cannot connect");
    await userEvent.click(within(crm).getByRole("button", { name: "Close" }));
    expect(within(crm).queryByTestId("explorer")).toBeNull();
  });

  it("shows why a query is refused and keeps an existing query when another table is chosen", async () => {
    setup();
    render(<DataSources />);
    const crm = (await screen.findAllByTestId("source")).find((c) => within(c).queryByText("CRM"))!;
    await userEvent.click(within(crm).getByRole("button", { name: "Explore" }));
    const ex = within(crm).getByTestId("explorer");
    await userEvent.type(within(ex).getByLabelText("Query"), "DELETE FROM x");
    await userEvent.click(within(ex).getByRole("button", { name: "Run" }));
    expect(await within(ex).findByRole("alert")).toHaveTextContent("only reads are allowed");
    await userEvent.click(within(ex).getByRole("button", { name: "Browse tables" }));
    await userEvent.click(await within(ex).findByRole("button", { name: "public.orders" }));
    await within(ex).findByTestId("columns");
    expect((within(ex).getByLabelText("Query") as HTMLTextAreaElement).value).toBe("DELETE FROM x");
  });

  it("explains a failed schema lookup and an empty log, and uses SOQL hints for Salesforce", async () => {
    const net = setup([{ ...SRC, id: "empty", kind: "salesforce", label: "Org" }]);
    net.fn.mockImplementation(async (input: any) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      if (url.pathname.endsWith("/schema")) return jsonResponse({ detail: "cannot connect" }, { status: 422 });
      if (url.pathname.endsWith("/audit")) return jsonResponse({ audit: [] });
      return jsonResponse({ sources: [{ ...SRC, id: "empty", kind: "salesforce", label: "Org" }], kinds: [...KINDS, { ...KINDS[0], kind: "salesforce", label: "Salesforce" }] });
    });
    render(<DataSources />);
    await userEvent.click(await screen.findByRole("button", { name: "Explore" }));
    expect(screen.getByPlaceholderText("SELECT Id, Name FROM Account LIMIT 10")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Browse tables" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot connect");
    await userEvent.click(screen.getByRole("button", { name: "Recent activity" }));
    expect(await screen.findByText("Nothing yet.")).toBeInTheDocument();
  });

  it("says when a source has no tables", async () => {
    const net = setup();
    net.fn.mockImplementation(async (input: any) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      if (url.pathname.endsWith("/schema")) return jsonResponse({ tables: [] });
      return jsonResponse({ sources: [SRC], kinds: KINDS });
    });
    render(<DataSources />);
    await userEvent.click(await screen.findByRole("button", { name: "Explore" }));
    await userEvent.click(screen.getByRole("button", { name: "Browse tables" }));
    expect(await screen.findByText("No tables found.")).toBeInTheDocument();
  });

  it("shows an error when the sources cannot be loaded", async () => {
    routeFetch({ "GET /api/data/sources": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<DataSources />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
