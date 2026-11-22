import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import KindFields, { type KField } from "@/components/KindFields";

const FIELDS: KField[] = [
  { name: "name", label: "Name", required: true, placeholder: "p", help: "Say it" },
  { name: "key", label: "Key", type: "secret", help: "write-only" },
  { name: "on", label: "Switch", type: "bool", default: true, help: "Because" },
  { name: "detect", label: "Look for", type: "choice", default: ["a", "b"], choices: ["a", "b", "c"], help: "Several" },
  { name: "mode", label: "Mode", type: "choice", default: "x", choices: ["x", "y"], help: "One of" },
  { name: "sev", label: "Severity", type: "choice", default: 4, choices: [2, 4, 6] },
  { name: "words", label: "Words", type: "textarea", help: "Lines" },
  { name: "n", label: "Count", type: "number", default: 5, help: "A number" },
  { name: "plain", label: "Plain" },
];

function Host({ secretsSet = [] as string[], init = {} as Record<string, any> }) {
  const [params, setParams] = useState<Record<string, any>>(init);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  return <div><KindFields fields={FIELDS} params={params} onParams={setParams} secrets={secrets} onSecrets={setSecrets} secretsSet={secretsSet} />
    <pre data-testid="out">{JSON.stringify({ params, secrets })}</pre></div>;
}
const out = () => JSON.parse(screen.getByTestId("out").textContent!);

describe("KindFields", () => {
  it("draws every kind of field with its default and its help text", () => {
    render(<Host />);
    expect(screen.getByLabelText(/^Name/)).toHaveValue("");
    expect(screen.getByLabelText(/Switch/)).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "a" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "c" })).not.toBeChecked();
    expect(screen.getByLabelText(/^Mode/)).toHaveValue("x");
    expect(screen.getByLabelText(/^Severity/)).toHaveValue("4");
    expect(screen.getByLabelText(/^Count/)).toHaveValue(5);
    for (const t of ["Say it", "write-only", "Several", "One of", "Lines", "A number"]) expect(screen.getByText(t)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Key/)).toHaveAttribute("placeholder", "paste value");
  });

  it("collects what is typed, without ever showing a stored secret", async () => {
    render(<Host secretsSet={["key"]} init={{ detect: ["a"] }} />);
    expect(screen.getByLabelText(/^Key/)).toHaveAttribute("placeholder", "(stored: leave empty to keep)");
    await userEvent.type(screen.getByLabelText(/^Name/), "hello");
    await userEvent.type(screen.getByLabelText(/^Key/), "S3");
    await userEvent.click(screen.getByLabelText(/Switch/));
    await userEvent.click(screen.getByRole("checkbox", { name: "c" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "a" }));
    await userEvent.selectOptions(screen.getByLabelText(/^Mode/), "y");
    await userEvent.selectOptions(screen.getByLabelText(/^Severity/), "6");
    await userEvent.type(screen.getByLabelText(/^Words/), "w1");
    const n = screen.getByLabelText(/^Count/);
    fireEvent.change(n, { target: { value: "9" } });
    await userEvent.type(screen.getByLabelText(/^Plain/), "z");
    expect(out()).toEqual({ params: { name: "hello", on: false, detect: ["c"], mode: "y", sev: 6, words: "w1", n: 9, plain: "z" }, secrets: { key: "S3" } });
    fireEvent.change(n, { target: { value: "" } });
    expect(out().params.n).toBe("");
  });
});
