import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import OpenApiTab from "@/components/workflows/OpenApiTab";

const OP = (id: string, over: Record<string, unknown> = {}) => ({ id, method: "get", path: `/${id}`, summary: `Do ${id}`, description: "", parameters: [{ name: "limit", in: "query", type: "integer" }], body_fields: [], tags: ["t"], origin: "spec", enabled: true, hidden: false,
  risk: "read", confirm_required: false, defaults: {}, deprecated: false, used_by: [], ...over });
const SRC = (over: Record<string, unknown> = {}) => ({ id: "pets", name: "Pets", base: "https://api.pets.test", allow_write: false, has_token: true, origin: "url", auth: { type: "bearer", name: "" }, servers: [{ url: "https://api.pets.test", description: "" }],
  refreshed: 1_700_000_000, updated: 1_700_000_000, title: "Pet Store", version: "1", warnings: ["2 operation(s) change data"], counts: { total: 3, enabled: 2, write: 1, hidden: 0 }, used_by: [], operations: [OP("list")], ...over });
const PREVIEW = { title: "Pet Store", version: "1.0", spec_version: "3.0.3", base: "https://api.pets.test", servers: [{ url: "https://api.pets.test" }, { url: "https://eu.pets.test" }], warnings: ["w1"], suggested_id: "pet_store",
  suggested_auth: { type: "header", header: "X-Key" }, counts: { total: 3, read: 2, write: 1, tags: { pets: 2, store: 1 } }, operations: [] };

function routes(extra: Record<string, any> = {}, srcs: any[] = [SRC()]) {
  return routeFetch({
    "GET /api/workflows/apis": () => jsonResponse({ apis: srcs }), "GET /api/workflows/apis/pets": () => jsonResponse(srcs[0] ?? SRC()),
    "GET /api/workflows/apis/pets/operations": () => jsonResponse({ operations: [OP("list", { used_by: ["wf_a"] }), OP("add", { method: "post", risk: "write", path: "/add", body_fields: [{ name: "name", type: "string" }] }), OP("mine", { origin: "custom", deprecated: true }),
      OP("gone", { hidden: true })] }), ...extra });
}

describe("OpenAPI steps tab", () => {
  it("lists APIs and shows an empty state, and reports a load error", async () => {
    routes({}, []);
    const { unmount } = render(<OpenApiTab />);
    expect(await screen.findByText(/No APIs yet/)).toBeInTheDocument();
    unmount();
    routeFetch({ "GET /api/workflows/apis": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    render(<OpenApiTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("adds an API from pasted text: preview, then import", async () => {
    const { calls } = routes({ "POST /api/workflows/apis/preview": () => jsonResponse(PREVIEW), "POST /api/workflows/apis": () => jsonResponse(SRC({ id: "pet_store" })), "GET /api/workflows/apis/pet_store": () => jsonResponse(SRC({ id: "pet_store" })),
      "GET /api/workflows/apis/pet_store/operations": () => jsonResponse({ operations: [] }) }, [SRC()]);
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Add an API" }));
    const read = screen.getByRole("button", { name: "Read it" });
    expect(read).toBeDisabled();
    await user.click(screen.getByRole("tab", { name: "Paste" }));
    await user.click(screen.getByLabelText("Pasted description"));
    await user.paste('{"openapi":"3.0.0"}');
    await user.click(read);
    const pv = await screen.findByTestId("preview");
    expect(pv).toHaveTextContent("3 operations"); expect(pv).toHaveTextContent("pets (2)"); expect(pv).toHaveTextContent("w1");
    expect(calls.find((c) => c.url.pathname.endsWith("/preview"))?.body).toMatchObject({ content: '{"openapi":"3.0.0"}', src: "" });
    expect(screen.getByLabelText("API id")).toHaveValue("pet_store");
    expect(screen.getByLabelText("Sign-in type")).toHaveValue("header");
    expect(screen.getByLabelText("Key name")).toHaveValue("X-Key");
    await user.type(screen.getByLabelText("API key"), "sekret");
    await user.click(screen.getByLabelText(/Allow endpoints that change data/));
    await user.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/workflows/apis")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/workflows/apis")?.body).toMatchObject({ id: "pet_store", token: "sekret", allow_write: true, auth: { type: "header", name: "X-Key" }, content: '{"openapi":"3.0.0"}' });
    expect(await screen.findByTestId("source-detail")).toBeInTheDocument();
  });

  it("adds an API from an uploaded file or an address, and shows errors", async () => {
    let n = 0;
    const { calls } = routes({ "POST /api/workflows/apis/preview": () => (++n === 1 ? jsonResponse({ detail: "could not read that" }, { status: 422 }) : jsonResponse({ ...PREVIEW, suggested_auth: { type: "none", header: "Authorization" }, warnings: [] })),
      "POST /api/workflows/apis": () => jsonResponse({ detail: "an API 'pet_store' is already imported" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Add an API" }));
    const big = new File(["x"], "big.json"); Object.defineProperty(big, "size", { value: 6_000_000 });
    await user.upload(screen.getByLabelText("OpenAPI file"), big);
    expect(screen.getByRole("alert")).toHaveTextContent("larger than 5 MB");
    await user.upload(screen.getByLabelText("OpenAPI file"), new File(['{"openapi":"3.0.0"}'], "pets.yaml", { type: "application/json" }));
    expect(screen.getByText(/pets.yaml ready/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Read it" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not read that");
    await user.click(screen.getByRole("tab", { name: "From address" }));
    await user.type(screen.getByLabelText("Address"), "https://x.test/openapi.json");
    await user.click(screen.getByRole("button", { name: "Read it" }));
    await screen.findByTestId("preview");
    expect(calls.filter((c) => c.url.pathname.endsWith("/preview")).at(-1)?.body).toMatchObject({ src: "https://x.test/openapi.json", content: "" });
    expect(screen.queryByLabelText("API key")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already imported");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("button", { name: "Read it" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("duplicates, refreshes and deletes from the table", async () => {
    const { calls } = routes({ "POST /api/workflows/apis/pets/duplicate": () => jsonResponse(SRC({ id: "pets_copy" })), "POST /api/workflows/apis/pets/refresh": (init: any) => jsonResponse({ diff: JSON.parse(String(init?.body)).dry_run ? { added: ["fresh"], removed: ["old"], changed: ["list"] } : {}, affected: ["wf_a"], applied: !JSON.parse(String(init?.body)).dry_run, warnings: [] }),
      "DELETE /api/workflows/apis/pets": () => jsonResponse({ ok: true }) }, [SRC({ used_by: ["wf_a", "wf_b"] })]);
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    expect(screen.getByText("2 workflows")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Duplicate pets" }));
    await user.clear(screen.getByLabelText("New id")); await user.type(screen.getByLabelText("New id"), "pets_copy");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/duplicate"))).toBe(true));
    expect(calls.find((c) => c.url.pathname.endsWith("/duplicate"))?.body).toEqual({ id: "pets_copy" });
    await user.click(screen.getByRole("button", { name: "Refresh pets" }));
    await user.click(screen.getByRole("button", { name: "Check for changes" }));
    const diff = await screen.findByTestId("diff");
    expect(diff).toHaveTextContent("fresh"); expect(diff).toHaveTextContent("Gone: old"); expect(diff).toHaveTextContent("wf_a");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname.endsWith("/refresh")).length).toBe(2));
    await user.click(screen.getByRole("button", { name: "Delete pets" }));
    expect(screen.getByRole("alert")).toHaveTextContent("wf_a, wf_b");
    await user.click(screen.getByRole("button", { name: "Delete anyway" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.search === "?force=true")).toBe(true));
  });

  it("refresh says nothing changed, or needs a new file for uploaded APIs", async () => {
    routes({ "POST /api/workflows/apis/pets/refresh": () => jsonResponse({ diff: { added: [], removed: [], changed: [] }, affected: [], applied: false, warnings: [] }) }, [SRC({ origin: "upload" })]);
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Refresh pets" }));
    const check = screen.getByRole("button", { name: "Check for changes" });
    expect(check).toBeDisabled();
    await user.upload(screen.getByLabelText("New file"), new File(["{}"], "new.json"));
    await user.click(check);
    expect(await screen.findByTestId("diff")).toHaveTextContent("Nothing changed.");
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("opens an API: saves its connection, switches endpoints, hides and restores them", async () => {
    const { calls } = routes({ "PUT /api/workflows/apis/pets": () => jsonResponse(SRC()), "PUT /api/workflows/apis/pets/operations/list": () => jsonResponse(OP("list")), "PUT /api/workflows/apis/pets/operations/gone": () => jsonResponse(OP("gone")),
      "DELETE /api/workflows/apis/pets/operations/mine": () => jsonResponse({ ok: true }), "DELETE /api/workflows/apis/pets/operations/list": () => jsonResponse({ ok: true, hidden: true }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    const detail = await screen.findByTestId("source-detail");
    expect(detail).toHaveTextContent("2 operation(s) change data");
    await user.clear(screen.getByLabelText("Name")); await user.type(screen.getByLabelText("Name"), "Pets 2");
    await user.selectOptions(screen.getByLabelText("Sign-in"), "query");
    await user.type(screen.getByLabelText("Key name"), "api_key");
    await user.type(screen.getByLabelText("Key"), "k1");
    await user.click(screen.getByLabelText(/Allow endpoints that change data/));
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.pathname === "/api/workflows/apis/pets")).toBe(true));
    expect(calls.find((c) => c.method === "PUT" && c.url.pathname === "/api/workflows/apis/pets")?.body).toMatchObject({ name: "Pets 2", token: "k1", allow_write: true, auth: { type: "query", name: "api_key" } });
    const table = await screen.findByTestId("ops-table");
    expect(within(table).getByText("custom")).toBeInTheDocument(); expect(within(table).getByText("deprecated")).toBeInTheDocument();
    await user.click(within(table).getByLabelText("Switch list on"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.pathname.endsWith("/operations/list"))).toBe(true));
    await user.click(within(table).getByRole("button", { name: "Delete mine" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.pathname.endsWith("/operations/mine"))).toBe(true));
    await user.click(within(table).getByRole("button", { name: "Delete list" }));
    expect(screen.getByRole("alert")).toHaveTextContent("wf_a");
    await user.click(screen.getByRole("button", { name: "Delete anyway" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.pathname.endsWith("/operations/list"))).toBe(true));
    await user.click(within(table).getAllByRole("button", { name: "Show" })[0]);
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT" && c.url.pathname.endsWith("/operations/gone")).length).toBe(1));
    await user.type(screen.getByLabelText("Search endpoints"), "add");
    await user.selectOptions(screen.getByLabelText("Method filter"), "post");
    await user.click(screen.getByLabelText("show hidden"));
    await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/operations") && c.url.search.includes("q=add") && c.url.search.includes("method=post") && c.url.search.includes("hidden=true"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "← All APIs" }));
    expect(await screen.findByTestId("api-table")).toBeInTheDocument();
  });

  it("detail: refresh, errors and loading state", async () => {
    routes({ "PUT /api/workflows/apis/pets": () => jsonResponse({ detail: "the API address must start with http:// or https://" }, { status: 422 }), "POST /api/workflows/apis/pets/refresh": () => jsonResponse({ detail: "could not read" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    await screen.findByTestId("source-detail");
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    expect(await screen.findByText(/must start with http/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh from source" }));
    await user.click(screen.getByRole("button", { name: "Check for changes" }));
    expect(await screen.findByText("could not read")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export description" })).toHaveAttribute("href", expect.stringContaining("/apis/pets/spec"));
  });

  it("creates and edits endpoints (custom and spec-derived)", async () => {
    const { calls } = routes({ "POST /api/workflows/apis/pets/operations": () => jsonResponse(OP("mine", { origin: "custom" })), "PUT /api/workflows/apis/pets/operations/mine": () => jsonResponse(OP("mine")), "PUT /api/workflows/apis/pets/operations/add": () => jsonResponse(OP("add")) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    await screen.findByTestId("ops-table");
    await user.click(screen.getByRole("button", { name: "Add an endpoint" }));
    await user.type(screen.getByLabelText("Endpoint id"), "orders");
    await user.type(screen.getByLabelText("Endpoint name"), "Orders");
    await user.selectOptions(screen.getByLabelText("Method"), "post");
    fireEvent.change(screen.getByLabelText("Path"), { target: { value: "/orders/{id}" } });
    await user.click(screen.getByRole("button", { name: "Add a parameter" }));
    await user.type(screen.getByLabelText("Parameter 1 name"), "verbose");
    await user.selectOptions(screen.getByLabelText("Parameter 1 type"), "boolean");
    await user.click(screen.getByLabelText("required"));
    await user.click(screen.getByRole("button", { name: "Add a body field" }));
    await user.type(screen.getByLabelText("Field 1 name"), "note");
    await user.selectOptions(screen.getByLabelText("Field 1 type"), "integer");
    await user.click(screen.getByRole("button", { name: "Add a parameter" }));
    await user.click(screen.getByRole("button", { name: "Remove parameter 2" }));
    await user.click(screen.getByRole("button", { name: "Add a body field" }));
    await user.click(screen.getByRole("button", { name: "Remove field 2" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname.endsWith("/operations"))).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname.endsWith("/operations"))?.body).toMatchObject({ id: "orders", label: "Orders", method: "post", path: "/orders/{id}", parameters: [{ name: "verbose", type: "boolean", required: true }], body_fields: [{ name: "note", type: "integer" }] });
    // edit a custom endpoint (method/path editable) and a spec endpoint (read-only method/path)
    await user.click(await screen.findByRole("button", { name: "Edit mine" }));
    expect(screen.getByLabelText("Path")).toBeEnabled();
    await user.clear(screen.getByLabelText("Default arguments")); await user.type(screen.getByLabelText("Default arguments"), "not json");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid JSON");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Edit add" }));
    expect(screen.getByLabelText("Path")).toBeDisabled(); expect(screen.getByText(/come from the API description/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Switched on")); await user.click(screen.getByLabelText(/Always ask/));
    fireEvent.change(screen.getByLabelText("Default arguments"), { target: { value: '{"limit": 2}' } });
    await user.type(screen.getByLabelText("Endpoint name"), "!");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.pathname.endsWith("/operations/add"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT" && c.url.pathname.endsWith("/operations/add"))?.body).toMatchObject({ enabled: false, confirm_required: true, defaults: { limit: 2 } });
  });

  it("shows an error when saving an endpoint fails", async () => {
    routes({ "POST /api/workflows/apis/pets/operations": () => jsonResponse({ detail: "the API already has an endpoint 'x'" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    await screen.findByTestId("ops-table");
    await user.click(screen.getByRole("button", { name: "Add an endpoint" }));
    await user.type(screen.getByLabelText("Endpoint id"), "x");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has an endpoint");
  });

  it("tests an endpoint: read, write with confirmation, errors and big bodies", async () => {
    let n = 0;
    const { calls } = routes({ "POST /api/workflows/apis/pets/operations/list/test": () => jsonResponse({ ok: true, status: 200, body: { a: 1 }, request: { method: "GET", url: "https://api.pets.test/list", query: { api_key: "•••" }, headers: {} } }),
      "POST /api/workflows/apis/pets/operations/add/test": () => (++n === 1 ? jsonResponse({ ok: false, error: "POST changes data" }) : jsonResponse({ ok: true, status: 201, body: "plain text", truncated: true, request: { method: "POST", url: "u", query: {}, headers: {} } })) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    const table = await screen.findByTestId("ops-table");
    await user.click(within(table).getByRole("button", { name: "Test list" }));
    await user.type(screen.getByLabelText("limit"), "3");
    await user.click(screen.getByRole("button", { name: "Send test call" }));
    const res = await screen.findByTestId("test-result");
    expect(res).toHaveTextContent("200"); expect(res).toHaveTextContent("api_key=%E2%80%A2%E2%80%A2%E2%80%A2"); expect(res).toHaveTextContent('"a": 1');
    expect(calls.find((c) => c.url.pathname.endsWith("/list/test"))?.body).toEqual({ args: { limit: "3" }, confirmed: false });
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(within(table).getByRole("button", { name: "Test add" }));
    expect(screen.getByText(/changes data/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Other arguments"), { target: { value: "oops" } });
    await user.click(screen.getByRole("button", { name: "Send test call" }));
    expect(await screen.findByText(/must be valid JSON/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Other arguments"), { target: { value: '{"name": "x"}' } });
    await user.click(screen.getByRole("button", { name: "Send test call" }));
    expect(await screen.findByText("POST changes data")).toBeInTheDocument();
    await user.click(screen.getByLabelText(/I confirm this call/));
    await user.click(screen.getByRole("button", { name: "Send test call" }));
    expect(await screen.findByText(/cut at 20 000/)).toBeInTheDocument(); expect(screen.getByText("plain text")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.pathname.endsWith("/add/test")).at(-1)?.body).toEqual({ args: { name: "x" }, confirmed: true });
  });

  it("a failing test call shows the error", async () => {
    routes({ "POST /api/workflows/apis/pets/operations/list/test": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    await user.click(within(await screen.findByTestId("ops-table")).getByRole("button", { name: "Test list" }));
    await user.click(screen.getByRole("button", { name: "Send test call" }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});
