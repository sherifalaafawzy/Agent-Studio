import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import OpenApiTab from "@/components/workflows/OpenApiTab";

const OP = (id: string, over: Record<string, unknown> = {}) => ({ id, method: "get", path: `/${id}`, summary: `Do ${id}`, description: "", parameters: [], body_fields: [], tags: [], origin: "spec", enabled: true, hidden: false,
  risk: "read", confirm_required: false, defaults: {}, deprecated: false, used_by: [], ...over });
const SRC = (over: Record<string, unknown> = {}) => ({ id: "pets", name: "Pets", base: "https://api.pets.test", allow_write: false, has_token: true, origin: "url", auth: { type: "bearer", name: "" }, servers: [],
  refreshed: 1_700_000_000, updated: 1_700_000_000, title: "Pet Store", version: "1", warnings: [], counts: { total: 2, enabled: 2, write: 0, hidden: 0 }, used_by: [], operations: [], ...over });
const PREVIEW = { title: "Pet Store", version: "1.0", spec_version: "3.0.3", base: "https://api.pets.test", servers: [], warnings: [], suggested_id: "pet_store", suggested_auth: { type: "none", header: "Authorization" },
  counts: { total: 1, read: 1, write: 0, tags: {} }, operations: [] };

function routes(extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/workflows/apis": () => jsonResponse({ apis: [SRC()] }), "GET /api/workflows/apis/pets": () => jsonResponse(SRC()),
    "GET /api/workflows/apis/pets/operations": () => jsonResponse({ operations: [OP("list"), OP("mine", { origin: "custom" })] }), ...extra });
}

describe("OpenAPI steps tab: the corners", () => {
  it("lets you correct everything about an API before it is imported, and ignores a cancelled file choice", async () => {
    const { calls } = routes({ "POST /api/workflows/apis/preview": () => jsonResponse(PREVIEW), "POST /api/workflows/apis": () => jsonResponse(SRC({ id: "mine" })), "GET /api/workflows/apis/mine": () => jsonResponse(SRC({ id: "mine" })),
      "GET /api/workflows/apis/mine/operations": () => jsonResponse({ operations: [] }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Add an API" }));
    fireEvent.change(screen.getByLabelText("OpenAPI file"), { target: { files: [] } });                     // the file chooser was cancelled
    expect(screen.queryByText(/ready/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Paste" }));
    await user.click(screen.getByLabelText("Pasted description"));
    await user.paste('{"openapi":"3.0.0"}');
    await user.click(screen.getByRole("button", { name: "Read it" }));
    await screen.findByTestId("preview");
    await user.clear(screen.getByLabelText("API id")); await user.type(screen.getByLabelText("API id"), "MINE");
    await user.clear(screen.getByLabelText("API name")); await user.type(screen.getByLabelText("API name"), "Mine");
    await user.clear(screen.getByLabelText("Base address")); await user.type(screen.getByLabelText("Base address"), "https://api.mine.test");
    await user.selectOptions(screen.getByLabelText("Sign-in type"), "header");
    await user.type(screen.getByLabelText("Key name"), "X-Mine");
    await user.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/workflows/apis")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/workflows/apis")!.body).toMatchObject({ id: "mine", name: "Mine", base: "https://api.mine.test", auth: { type: "header", name: "X-Mine" } });
  });

  it("closes the table dialogs without doing anything, and shows when a duplicate or delete fails", async () => {
    routes({ "POST /api/workflows/apis/pets/duplicate": () => jsonResponse({ detail: "that id is taken" }, { status: 422 }), "DELETE /api/workflows/apis/pets": () => jsonResponse({ detail: "locked" }, { status: 500 }),
      "POST /api/workflows/apis/pets/refresh": () => jsonResponse({ diff: { added: [], removed: [], changed: [] }, affected: [], applied: false, warnings: [] }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Duplicate pets" }));
    expect(screen.getByRole("dialog", { name: "Duplicate Pets" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Duplicate pets" }));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Duplicate pets" }));
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("that id is taken");
    await user.click(screen.getByRole("button", { name: "Refresh pets" }));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Delete pets" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete pets" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("locked"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByTestId("source-detail")).toBeInTheDocument();
  });

  it("inside an API: edits the address and an endpoint's description, and reports failures", async () => {
    routes({
      "PUT /api/workflows/apis/pets/operations/list": () => jsonResponse(OP("list")),
      "DELETE /api/workflows/apis/pets/operations/mine": () => jsonResponse({ detail: "still used" }, { status: 409 }),
      "POST /api/workflows/apis/pets/refresh": (init: any) => (JSON.parse(String(init?.body)).dry_run ? jsonResponse({ diff: { added: ["fresh"], removed: [], changed: [] }, affected: [], applied: false, warnings: [] }) : jsonResponse({ detail: "could not apply" }, { status: 500 })),
    });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    await screen.findByTestId("ops-table");
    await user.clear(screen.getByLabelText("Address")); await user.type(screen.getByLabelText("Address"), "https://other.test");
    expect(screen.getByLabelText("Address")).toHaveValue("https://other.test");
    await user.click(screen.getByRole("button", { name: "Edit list" }));
    await user.type(screen.getByLabelText("Description"), "Lists them");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByLabelText("Description")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Delete mine" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete mine" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("still used"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "Refresh from source" }));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Refresh from source" }));
    await user.click(screen.getByRole("button", { name: "Check for changes" }));
    await screen.findByTestId("diff");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await within(screen.getByRole("dialog")).findByText("could not apply")).toBeInTheDocument();
  });

  it("says so when an API cannot be opened", async () => {
    routeFetch({ "GET /api/workflows/apis": () => jsonResponse({ apis: [SRC()] }), "GET /api/workflows/apis/pets": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await user.click(await screen.findByRole("button", { name: "Pets" }));
    expect(await screen.findByText("gone")).toBeInTheDocument();
  });

  it("an applied refresh closes the dialog from the table and from inside an API", async () => {
    routes({ "POST /api/workflows/apis/pets/refresh": (init: any) => jsonResponse({ diff: { added: ["x"], removed: [], changed: [] }, affected: [], applied: !JSON.parse(String(init?.body)).dry_run, warnings: [] }) });
    const user = userEvent.setup();
    render(<OpenApiTab />);
    await screen.findByTestId("api-table");
    await user.click(screen.getByRole("button", { name: "Refresh pets" }));
    await user.click(screen.getByRole("button", { name: "Check for changes" }));
    await screen.findByTestId("diff");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Pets" }));
    await screen.findByTestId("ops-table");
    await user.click(screen.getByRole("button", { name: "Refresh from source" }));
    await user.click(screen.getByRole("button", { name: "Check for changes" }));
    await screen.findByTestId("diff");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
