import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryNote, MemoryPicker, type MemCfg } from "@/components/MemoryUI";

describe("MemoryPicker", () => {
  it("starts with memory off and explains it", () => {
    render(<MemoryPicker value={undefined} onChange={() => {}} />);
    expect(screen.getByText(/starts every chat fresh/)).toBeInTheDocument();
    expect(screen.getByTestId("memory-picker").querySelectorAll("input[type=checkbox]")).toHaveLength(6);
    expect(screen.queryByText(/may read memories/)).toBeNull();
  });

  it("ticking a kind switches memory on with the safe defaults, and unticking the last one switches it off", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<MemoryPicker value={{}} onChange={onChange} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Facts" }));
    expect(onChange).toHaveBeenLastCalledWith({ types: ["semantic"], read_scopes: ["agent", "user", "workspace", "global"], write_scopes: ["agent", "user", "workspace"], auto_recall: true, auto_save: "review" });
    const on: MemCfg = { types: ["semantic", "notes"], read_scopes: ["agent", "user"], write_scopes: ["agent"], auto_recall: true, auto_save: "auto" };
    rerender(<MemoryPicker value={on} onChange={onChange} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Project notes" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...on, types: ["semantic"] });
    rerender(<MemoryPicker value={{ ...on, types: ["semantic"] }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Facts" }));
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("edits who it reads from, who it saves for, recall and how saving works", async () => {
    const onChange = vi.fn();
    const on: MemCfg = { types: ["semantic"], read_scopes: ["agent"], write_scopes: ["agent"], auto_recall: true, auto_save: "review" };
    render(<MemoryPicker value={on} onChange={onChange} />);
    const boxes = screen.getAllByRole("checkbox", { name: "all my agents" });                 // [read, write]
    await userEvent.click(boxes[0]);
    expect(onChange).toHaveBeenLastCalledWith({ ...on, read_scopes: ["agent", "user"] });
    await userEvent.click(boxes[1]);
    expect(onChange).toHaveBeenLastCalledWith({ ...on, write_scopes: ["agent", "user"] });
    await userEvent.click(screen.getByRole("checkbox", { name: /Bring up relevant memories/ }));
    expect(onChange).toHaveBeenLastCalledWith({ ...on, auto_recall: false });
    await userEvent.selectOptions(screen.getByRole("combobox"), "off");
    expect(onChange).toHaveBeenLastCalledWith({ ...on, auto_save: "off" });
  });

  it("fills in defaults for settings an older agent does not have", () => {
    render(<MemoryPicker value={{ types: ["episodic"] }} onChange={() => {}} />);
    expect(screen.getAllByRole("checkbox", { name: "everyone", checked: true })).toHaveLength(1);        // read: everyone is on by default, write: it is not
    expect(screen.getByRole("combobox")).toHaveValue("review");
  });
});

describe("MemoryNote", () => {
  it("shows nothing when nothing was remembered", () => {
    const { container, rerender } = render(<MemoryNote events={[]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<MemoryNote events={undefined as unknown as []} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("summarises what happened and lists each memory when opened", async () => {
    render(<MemoryNote events={[
      { kind: "recalled", type: "semantic", scope: "user", text: "The billing system is Xero" }, { kind: "recalled", type: "entity", scope: "weird", text: "Alice (person)" },
      { kind: "saved", type: "semantic", text: "Deploys are on Fridays" }, { kind: "pending", type: "semantic", text: "Timezone is Paris" },
      { kind: "notes", file: "AGENTS.md" }, { kind: "mystery" }, { kind: "compacted", text: "summarised" }]} />);
    const btn = screen.getByRole("button", { name: /Memory: recalled 2 · remembered 1 · waiting for your review 1 · read the project notes 1 · mystery 1 · summarised the chat 1/ });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/The billing system is Xero/)).toBeNull();
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/The billing system is Xero/)).toBeInTheDocument();
    expect(screen.getByText(/semantic, all my agents/)).toBeInTheDocument();
    expect(screen.getByText(/entity, weird/)).toBeInTheDocument();
    expect(screen.getByText(/AGENTS.md/)).toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.queryByText(/The billing system is Xero/)).toBeNull();
  });
});
