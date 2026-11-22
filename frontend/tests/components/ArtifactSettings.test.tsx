import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ArtifactSettings from "@/components/ArtifactSettings";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const KINDS = [
  { id: "local", label: "A folder on this PC", note: "The default.", cloud: false, fields: [{ name: "folder", label: "Folder", secret: false, required: false, placeholder: "D:\\x" }] },
  { id: "gcs", label: "Google Cloud Storage", note: "Use a service account.", cloud: true, fields: [{ name: "bucket", label: "GCS bucket", secret: false, required: true, placeholder: "" }] },
  { id: "s3", label: "Amazon S3", note: "Files are uploaded to your bucket.", cloud: true, fields: [{ name: "bucket", label: "Bucket", secret: false, required: true, placeholder: "" }, { name: "secret_access_key", label: "Secret access key", secret: true, required: false, placeholder: "" }] },
];
const LOCAL = { id: "local", kind: "local", label: "This PC", enabled: true, config: {}, secrets_set: [], where: "C:\\data\\artifacts", cloud: false };
const CLOUD = { id: "team", kind: "s3", label: "Team bucket", enabled: true, config: { bucket: "b1" }, secrets_set: ["secret_access_key"], where: "s3://b1/agent-studio/artifacts/ in us-east-1", cloud: true };
const CFG = { enabled: true, capture: "new", max_file_mb: 25, max_per_turn: 50, secret_scan: "redact", retention_days: 0, ignore: ["node_modules/**", "*.pyc"], storages: [LOCAL] as any[], policy: [] as any[],
  default_storages: ["local"], capture_modes: { off: "Do not collect files automatically", new: "New files only (recommended)", new_and_changed: "New and changed files" },
  scan_modes: { redact: "Remove secrets from text", block: "Do not send files with a secret", off: "No secret scan" }, types: ["document", "image", "data"], default_ignore: [], kinds: KINDS };

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  let cfg: any = { ...CFG, ...over };
  return routeFetch({
    "GET /api/artifacts/config": () => jsonResponse(cfg),
    "PUT /api/artifacts/config": (init: any) => {
      const p = JSON.parse(init.body);
      if (p.capture === "bad") return jsonResponse({ detail: "capture must be one of: off, new" }, { status: 422 });
      cfg = { ...cfg, ...p, storages: p.storages ? p.storages.map((s: any) => ({ ...LOCAL, ...s, secrets_set: Object.keys(p.secrets?.[s.id] ?? {}), where: "somewhere", cloud: s.kind !== "local" })) : cfg.storages };
      return jsonResponse(cfg);
    },
    "POST /api/artifacts/storages/team/test": () => jsonResponse({ ok: true, detail: "bucket 'b1' is reachable" }),
    "POST /api/artifacts/storages/local/test": () => jsonResponse({ ok: false, detail: "cannot write" }),
    ...extra,
  });
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT").map((c) => c.body);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("ArtifactSettings: collecting", () => {
  it("shows the state and saves each choice", async () => {
    const net = setup();
    render(<ArtifactSettings />);
    expect(await screen.findByText(/A copy always stays on this PC/)).toBeInTheDocument();
    expect(screen.getByText("on")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Keep what agents produce" }));
    await userEvent.selectOptions(screen.getByLabelText(/Collect files automatically/), "new_and_changed");
    await userEvent.selectOptions(screen.getByLabelText(/Secret scan before a copy leaves this PC/), "block");
    const mb = screen.getByLabelText("Largest file (MB)");
    fireEvent.change(mb, { target: { value: "10" } }); fireEvent.blur(mb);
    const per = screen.getByLabelText("Files collected per turn");
    fireEvent.change(per, { target: { value: "7" } }); fireEvent.blur(per);
    const days = screen.getByLabelText("Delete after (days, 0 = keep)");
    fireEvent.change(days, { target: { value: "30" } }); fireEvent.blur(days);
    await waitFor(() => expect(puts(net.calls)).toEqual([{ enabled: false }, { capture: "new_and_changed" }, { secret_scan: "block" }, { max_file_mb: 10 }, { max_per_turn: 7 }, { retention_days: 30 }]));
  });

  it("edits the ignore list when you leave the box, and only if it changed", async () => {
    const net = setup();
    render(<ArtifactSettings />);
    const box = await screen.findByLabelText(/Never collect these/);
    expect(box).toHaveValue("node_modules/**\n*.pyc");
    fireEvent.blur(box);
    expect(puts(net.calls)).toEqual([]);
    await userEvent.clear(box);
    await userEvent.type(box, "*.log{enter}secrets/**{enter}{enter}");
    fireEvent.blur(box);
    await waitFor(() => expect(puts(net.calls)).toEqual([{ ignore: ["*.log", "secrets/**"] }]));
  });

  it("shows why a setting was refused", async () => {
    setup();
    render(<ArtifactSettings />);
    const sel = await screen.findByLabelText(/Collect files automatically/);
    fireEvent.change(sel, { target: { value: "new" } });
    // a refusal: the server says no
    const net = setup({}, { "PUT /api/artifacts/config": () => jsonResponse({ detail: "capture must be one of: off, new" }, { status: 422 }) });
    await userEvent.selectOptions(sel, "off");
    expect(await screen.findByRole("alert")).toHaveTextContent("capture must be one of: off, new");
    expect(net.calls.some((c) => c.method === "PUT")).toBe(true);
  });

  it("shows a load error, and nothing while loading", async () => {
    routeFetch({ "GET /api/artifacts/config": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    const { container } = render(<ArtifactSettings />);
    expect(container.innerHTML).toBe("");
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});

describe("ArtifactSettings: storages", () => {
  it("lists the folder on this PC, which cannot be switched off or removed, and tests it", async () => {
    setup();
    render(<ArtifactSettings />);
    const card = await screen.findByTestId("storage");
    expect(within(card).getByText("This PC")).toBeInTheDocument();
    expect(within(card).queryByRole("checkbox")).toBeNull();
    expect(within(card).queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("cannot write");
  });

  it("adds a cloud storage with a write-only secret, then tests, disables, edits and removes it", async () => {
    const net = setup();
    render(<ArtifactSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a storage" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Add a storage" }));
    expect(screen.getByRole("button", { name: "Add storage" })).toBeDisabled();
    expect(screen.getByText(/Files you send here leave this PC/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("Kind")).queryByRole("option", { name: "A folder on this PC" })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "s3");
    await userEvent.type(screen.getByLabelText("Name"), "TEAM");
    await userEvent.type(screen.getByLabelText("Label (optional)"), "Team bucket");
    await userEvent.type(screen.getByLabelText(/^Bucket/), "b1");
    await userEvent.type(screen.getByLabelText(/Secret access key/), "shh");
    await userEvent.click(screen.getByRole("button", { name: "Add storage" }));
    await waitFor(() => expect(puts(net.calls)[0]).toEqual({ storages: [{ id: "local", kind: "local", label: "This PC", enabled: true, config: {} }, { id: "team", kind: "s3", label: "Team bucket", enabled: true, config: { bucket: "b1" } }], secrets: { team: { secret_access_key: "shh" } } }));
    const cards = await screen.findAllByTestId("storage");
    expect(cards).toHaveLength(2);
    expect(within(cards[1]).getByText("leaves this PC")).toBeInTheDocument();
    await userEvent.click(within(cards[1]).getByRole("button", { name: "Test" }));
    expect(await within(cards[1]).findByRole("status")).toHaveTextContent("bucket 'b1' is reachable");
    await userEvent.click(within(cards[1]).getByRole("checkbox", { name: "team on" }));
    await waitFor(() => expect(puts(net.calls)[1].storages[1].enabled).toBe(false));
    await userEvent.click(within((await screen.findAllByTestId("storage"))[1]).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Kind")).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Secret access key/), "new");
    await userEvent.click(screen.getByRole("button", { name: "Save storage" }));
    await waitFor(() => expect(puts(net.calls)[2].secrets).toEqual({ team: { secret_access_key: "new" } }));
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await userEvent.click(within((await screen.findAllByTestId("storage"))[1]).getByRole("button", { name: "Remove" }));
    expect(puts(net.calls)).toHaveLength(3);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await userEvent.click(within((await screen.findAllByTestId("storage"))[1]).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(puts(net.calls)[3].storages).toHaveLength(1));
  });

  it("changes the fields when another kind is chosen", async () => {
    setup();
    render(<ArtifactSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a storage" }));
    expect(screen.getByLabelText("Kind")).toHaveValue("gcs");
    await userEvent.type(screen.getByLabelText(/GCS bucket/), "x");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "s3");
    expect(screen.getByText(/Files are uploaded to your bucket/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/GCS bucket/)).toBeNull();
    expect(screen.getByLabelText(/^Bucket/)).toHaveValue("");
  });

  it("shows a failed test and a stored-secret hint, and can cancel an edit", async () => {
    setup({ storages: [LOCAL, CLOUD] }, { "POST /api/artifacts/storages/team/test": () => jsonResponse({ detail: "no such storage" }, { status: 404 }) });
    render(<ArtifactSettings />);
    const cards = await screen.findAllByTestId("storage");
    await userEvent.click(within(cards[1]).getByRole("button", { name: "Test" }));
    expect(await within(cards[1]).findByRole("status")).toHaveTextContent("no such storage");
    await userEvent.click(within(cards[1]).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText(/Secret access key/)).toHaveAttribute("placeholder", "(stored: leave empty to keep)");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("storage-form")).toBeNull();
    await userEvent.click(within((await screen.findAllByTestId("storage"))[0]).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Kind")).toHaveValue("local");
  });
});

describe("ArtifactSettings: rules", () => {
  it("adds a rule, changes its kinds, storages and size limit, and removes it", async () => {
    const net = setup({ storages: [LOCAL, CLOUD] });
    render(<ArtifactSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a rule" }));
    const rule = await screen.findByTestId("rule");
    expect(puts(net.calls)[0]).toEqual({ policy: [{ types: ["document"], max_mb: 0, storages: ["local"] }] });
    expect(within(rule).getByRole("checkbox", { name: "Documents" })).toBeDisabled();             // a rule always keeps one kind...
    expect(within(rule).getByRole("checkbox", { name: "local" })).toBeDisabled();                 // ...and one storage
    await userEvent.click(within(rule).getByRole("checkbox", { name: "Images" }));
    await userEvent.click(within(rule).getByRole("checkbox", { name: "team" }));
    const mb = within(rule).getByLabelText("Only files up to (MB, 0 = any size)");
    fireEvent.change(mb, { target: { value: "5" } }); fireEvent.blur(mb);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ policy: [{ types: ["document", "image"], max_mb: 5, storages: ["local", "team"] }] }));
    await userEvent.click(within(await screen.findByTestId("rule")).getByRole("button", { name: "Remove rule" }));
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ policy: [] }));
  });

  it("chooses the default storages, never leaving none", async () => {
    const net = setup({ storages: [LOCAL, CLOUD] });
    render(<ArtifactSettings />);
    const dflt = (await screen.findByText("Everything else goes to")).nextElementSibling as HTMLElement;
    expect(within(dflt).getByRole("checkbox", { name: "local" })).toBeDisabled();
    await userEvent.click(within(dflt).getByRole("checkbox", { name: "team" }));
    await waitFor(() => expect(puts(net.calls)).toEqual([{ default_storages: ["local", "team"] }]));
    await userEvent.click(within(dflt).getByRole("checkbox", { name: "local" }));
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ default_storages: ["team"] }));
  });
});
