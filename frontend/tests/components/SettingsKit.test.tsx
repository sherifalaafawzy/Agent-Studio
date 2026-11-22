import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackupPanel, NumField, numberProblem, ResetLink, SettingsSearch, useRegistry, type Registry, type RegSetting } from "@/components/SettingsKit";
import { anyDirty, confirmDiscard, dirtyIds, setDirty, useDirty } from "@/lib/dirty";
import { jsonResponse, routeFetch } from "../helpers/http";

const S = (o: Partial<RegSetting>): RegSetting => ({ key: "k", label: "K", tab: "harness", kind: "int", effect: "does k", help: "", where: "", internal: false, secret: false,
  min: 1, max: 5, choices: [], max_len: null, default: 4, value: 4, changed: false, field: "", ...o });
const REG: Registry = { tabs: ["harness"], settings: [S({ key: "max_spawns_per_turn", label: "Max sub-agents", effect: "stops runaway agent creation", default: 4, value: 9, changed: true }),
  S({ key: "internal_thing", label: "Bookkeeping", internal: true }), S({ key: "no_tab", label: "No tab", tab: "" })] };
const TABS: [string, string][] = [["harness", "Agents & sandbox"]];

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); for (const id of dirtyIds()) setDirty(id, false); });

describe("numberProblem", () => {
  it("accepts good numbers and explains bad ones", () => {
    expect(numberProblem("3", 1, 5, true)).toBe("");
    expect(numberProblem("2.5", 1, 5, false)).toBe("");
    for (const bad of ["", "  ", "abc", "Infinity"]) expect(numberProblem(bad, 1, 5, true)).toBe("Enter a whole number between 1 and 5");
    expect(numberProblem("2.5", 1, 5, true)).toBe("Enter a whole number between 1 and 5");
    expect(numberProblem("0", 1, 5, true)).toMatch(/between 1 and 5/);
    expect(numberProblem("9", 1, 5, false)).toBe("Enter a number between 1 and 5");
  });
});

describe("NumField", () => {
  it("saves a valid number on blur and on Enter, and never saves an unchanged or invalid one", async () => {
    const save = vi.fn();
    render(<NumField label="Limit" value={3} min={1} max={5} onSave={save} sk="limit" info="spawn" />);
    const input = screen.getByLabelText("Limit") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "4" } }); fireEvent.blur(input);
    expect(save).toHaveBeenCalledWith(4);
    fireEvent.change(input, { target: { value: "5" } }); fireEvent.keyDown(input, { key: "Enter" });
    expect(save).toHaveBeenLastCalledWith(5);
    fireEvent.keyDown(input, { key: "a" });
    fireEvent.change(input, { target: { value: "3" } }); fireEvent.blur(input);              // same as the stored value: nothing to save
    expect(save).toHaveBeenCalledTimes(2);
    fireEvent.change(input, { target: { value: "99" } }); fireEvent.blur(input);
    expect(screen.getByRole("alert")).toHaveTextContent("between 1 and 5");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(save).toHaveBeenCalledTimes(2);
    fireEvent.change(input, { target: { value: "2" } });                                          // typing clears the message
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("accepts decimals when asked to and follows the stored value", () => {
    const save = vi.fn();
    const { rerender } = render(<NumField label="Minutes" value={1.5} min={0} max={10} integer={false} onSave={save} />);
    const input = screen.getByLabelText("Minutes") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2.5" } }); fireEvent.blur(input);
    expect(save).toHaveBeenCalledWith(2.5);
    rerender(<NumField label="Minutes" value={7} min={0} max={10} integer={false} onSave={save} />);
    expect(input.value).toBe("7");
  });
});

describe("ResetLink", () => {
  it("shows only for changed, non-secret settings and resets through the API", async () => {
    const net = routeFetch({ "POST /api/settings/reset": () => jsonResponse({ reset: ["k"] }) });
    const onReset = vi.fn();
    const { container, rerender } = render(<ResetLink sk="k" reg={undefined} onReset={onReset} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ResetLink sk="k" reg={S({ changed: false })} onReset={onReset} />); expect(container).toBeEmptyDOMElement();
    rerender(<ResetLink sk="k" reg={S({ changed: true, secret: true })} onReset={onReset} />); expect(container).toBeEmptyDOMElement();
    for (const [def, text] of [[true, "(on)"], [false, "(off)"], [[], "(none)"], ["", "(empty)"], ["x", "(x)"], [4, "(4)"]] as [unknown, string][]) {
      rerender(<ResetLink sk="k" reg={S({ changed: true, default: def })} onReset={onReset} />);
      expect(screen.getByRole("button")).toHaveTextContent(text);
    }
    await userEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(onReset).toHaveBeenCalled());
    expect(net.calls[0].body).toEqual({ keys: ["k"] });
  });
  it("reports a failed reset instead of pretending", async () => {
    routeFetch({ "POST /api/settings/reset": () => jsonResponse({ detail: "nope" }, { status: 422 }) });
    const onReset = vi.fn();
    render(<ResetLink sk="k" reg={S({ changed: true })} onReset={onReset} />);
    await userEvent.click(screen.getByRole("button"));
    await new Promise((r) => setTimeout(r, 20));
    expect(onReset).not.toHaveBeenCalled();
  });
});

describe("SettingsSearch", () => {
  it("finds settings by name or description, skipping internal ones, and jumps to the match", async () => {
    const go = vi.fn();
    render(<SettingsSearch reg={REG} tabs={TABS} go={go} />);
    const box = screen.getByLabelText("Search settings");
    expect(screen.queryByRole("listbox")).toBeNull();
    await userEvent.type(box, "runaway");
    expect(screen.getByRole("option")).toHaveTextContent("Max sub-agents");
    expect(screen.getByRole("option")).toHaveTextContent("Agents & sandbox");
    await userEvent.click(screen.getByRole("option"));
    expect(go).toHaveBeenCalledWith("harness", "max_spawns_per_turn");
    expect((box as HTMLInputElement).value).toBe("");
    await userEvent.type(box, "bookkeeping");
    expect(screen.getByText(/No setting matches/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect((box as HTMLInputElement).value).toBe("");
  });
  it("shows nothing while the registry is still loading", async () => {
    render(<SettingsSearch reg={null} tabs={TABS} go={() => {}} />);
    await userEvent.type(screen.getByLabelText("Search settings"), "x");
    expect(screen.getByText(/No setting matches/)).toBeInTheDocument();
  });
  it("labels a match with its tab id when the tab is not in the list", async () => {
    render(<SettingsSearch reg={{ tabs: [], settings: [S({ key: "z", label: "Zed", tab: "memory" })] }} tabs={TABS} go={() => {}} />);
    await userEvent.type(screen.getByLabelText("Search settings"), "zed");
    expect(screen.getByRole("option")).toHaveTextContent("memory");
  });
});

describe("useRegistry", () => {
  function Probe() { const { reg, byField } = useRegistry(0); return <div>{reg ? `${reg.settings.length}:${byField.sandbox_network?.label ?? "-"}:${byField.enabled?.label ?? "-"}` : "none"}</div>; }
  it("loads the registry and indexes it by field name (falling back to the key)", async () => {
    routeFetch({ "GET /api/settings/registry": () => jsonResponse({ tabs: [], settings: [S({ key: "sandbox_network", label: "Net" }), S({ key: "otel_enabled", field: "enabled", label: "Telemetry" })] }) });
    render(<Probe />);
    expect(await screen.findByText("2:Net:Telemetry")).toBeInTheDocument();
  });
  it("copes with the backend being offline", async () => {
    routeFetch({ "GET /api/settings/registry": () => jsonResponse({}, { status: 500 }) });
    render(<Probe />);
    await waitFor(() => expect(screen.getByText("none")).toBeInTheDocument());
  });
});

describe("BackupPanel", () => {
  const routes = (over: Record<string, any> = {}) => routeFetch({
    "GET /api/settings/history": () => jsonResponse([{ ts: 1, key: "a", label: "Alpha", from: null, to: "1" }, { ts: 2, key: "b", label: "Beta", from: "1", to: "2" }]),
    "GET /api/settings/export": () => jsonResponse({ format: "agent-studio-settings", settings: {} }),
    "POST /api/settings/import": (init: any) => {
      const b = JSON.parse(init.body);
      return jsonResponse(b.dry_run ? { applied: false, changes: [{ key: "k", label: "K", from: 4, to: "x" }, { key: "j", label: "J", from: [], to: { a: 1 } }], errors: [{ key: "bad", error: "unknown setting (ignored)" }, { key: "", error: "file problem" }] }
        : { applied: true, changes: [{ key: "k" }], errors: [] });
    },
    "POST /api/settings/reset": () => jsonResponse({ reset: [] }),
    ...over,
  });
  it("lists recent changes and exports a file with no keys in it", async () => {
    routes();
    URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<BackupPanel onChanged={() => {}} />);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText(/not set/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Download settings file" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
  });
  it("shows a problem when the export fails", async () => {
    routes({ "GET /api/settings/export": () => jsonResponse({ detail: "broken" }, { status: 500 }) });
    render(<BackupPanel onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Download settings file" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
  it("previews an import, shows what would change and what was refused, then applies it", async () => {
    const net = routes();
    const onChanged = vi.fn();
    render(<BackupPanel onChanged={onChanged} />);
    const box = screen.getByLabelText("Settings file contents");
    expect(screen.getByRole("button", { name: "Preview changes" })).toBeDisabled();
    fireEvent.change(box, { target: { value: "not json" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(screen.getByRole("alert")).toHaveTextContent("not valid JSON");
    fireEvent.change(box, { target: { value: '{"format":"agent-studio-settings","settings":{}}' } });
    await userEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(await screen.findByText(/4 → x/)).toBeInTheDocument();
    expect(screen.getByText(/bad: unknown setting/)).toBeInTheDocument();
    expect(screen.getByText(/file: file problem/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Apply 2 changes" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(net.calls.filter((c) => c.url.pathname === "/api/settings/import").map((c) => c.body.dry_run)).toEqual([true, false]);
  });
  it("says when nothing would change", async () => {
    routes({ "POST /api/settings/import": () => jsonResponse({ applied: false, changes: [], errors: [] }) });
    render(<BackupPanel onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText("Settings file contents"), { target: { value: "{}" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(await screen.findByText("Nothing would change.")).toBeInTheDocument();
  });
  it("reports an import that the server refuses on preview", async () => {
    routes({ "POST /api/settings/import": () => jsonResponse({ detail: "no" }, { status: 500 }) });
    render(<BackupPanel onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText("Settings file contents"), { target: { value: "{}" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
  it("applies a one-change import with the singular label, and shows a server error on apply", async () => {
    routes({ "POST /api/settings/import": (init: any) => {
      const b = JSON.parse(init.body);
      return b.dry_run ? jsonResponse({ applied: false, changes: [{ key: "k", label: "K", from: 1, to: 2 }], errors: [] }) : jsonResponse({ detail: "late failure" }, { status: 500 });
    } });
    render(<BackupPanel onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText("Settings file contents"), { target: { value: "{}" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply 1 change" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
  it("resets everything only after confirmation", async () => {
    const net = routes();
    const onChanged = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<BackupPanel onChanged={onChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Reset all settings to defaults" }));
    expect(net.calls.some((c) => c.url.pathname === "/api/settings/reset")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Reset all settings to defaults" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(net.calls.find((c) => c.url.pathname === "/api/settings/reset")!.body).toEqual({ all: true });
  });
  it("shows a failed reset and an empty history", async () => {
    routes({ "GET /api/settings/history": () => jsonResponse({}, { status: 500 }), "POST /api/settings/reset": () => jsonResponse({ detail: "x" }, { status: 500 }) });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<BackupPanel onChanged={() => {}} />);
    expect(await screen.findByText("Nothing changed yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reset all settings to defaults" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("dirty guard", () => {
  function Form({ dirty }: { dirty: boolean }) { useDirty("form-a", dirty); return null; }
  it("warns on unload and when leaving while a form has unsaved edits, and stops when it is clean or gone", () => {
    expect(anyDirty()).toBe(false);
    expect(confirmDiscard()).toBe(true);
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const { rerender, unmount } = render(<Form dirty />);
    expect(anyDirty()).toBe(true);
    expect(add).toHaveBeenCalledWith("beforeunload", expect.any(Function));
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    const ask = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    expect(confirmDiscard()).toBe(false);
    expect(confirmDiscard()).toBe(true);
    setDirty("form-b", true);                                              // a second form: still listening once
    expect(dirtyIds().sort()).toEqual(["form-a", "form-b"]);
    setDirty("form-b", false);
    rerender(<Form dirty={false} />);
    expect(anyDirty()).toBe(false);
    expect(remove).toHaveBeenCalledWith("beforeunload", expect.any(Function));
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    rerender(<Form dirty />); unmount();                                   // a form that unmounts is clean again
    expect(anyDirty()).toBe(false);
    expect(ask).toHaveBeenCalledTimes(2);
  });
});
