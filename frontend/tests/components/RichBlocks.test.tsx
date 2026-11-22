import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Markdown from "@/components/Markdown";
import { ArtifactCard, ArtifactList, Chart, CsvTable, DiffBlock, FormBlock, isRichLang, parseChart, parseForm, ReplyContext, RichFence, SafeImage, splitLine, StatePanel } from "@/components/RichBlocks";
import { API_BASE } from "@/lib/api";
import type { Artifact } from "@/lib/artifacts";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

const art = (over: Partial<Artifact> = {}): Artifact => ({ id: "0123456789ab", name: "report.md", path: "", type: "document", mime: "text/markdown", preview: "markdown", size: 2048, sha256: "a".repeat(64), version: 1,
  created: Date.now() / 1000, agent: "engineer", session_id: "s1", trace_id: "t", workspace: "", source: "tool", tags: [], summary: "", secrets: false,
  copies: [{ storage: "local", status: "synced", detail: "", size: 2048, updated: 0 }], ...over });

describe("safe images", () => {
  it("draws embedded pictures and Agent Studio's own files, and only notes remote ones", () => {
    const { container, rerender } = render(<SafeImage alt="pic" url="data:image/png;base64,iVBORw0KGgo=" />);
    expect(container.querySelector("img")).toHaveAttribute("src", "data:image/png;base64,iVBORw0KGgo=");
    rerender(<SafeImage alt="mine" url={`${API_BASE}/api/artifacts/0123456789ab/content`} />);
    expect(screen.getByAltText("mine")).toBeInTheDocument();
    rerender(<SafeImage alt="tracker" url="https://evil.example/pixel.png?secret=abc" />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/remote image not loaded \(evil.example\)/)).toBeInTheDocument();
    rerender(<SafeImage alt="" url="javascript:alert(1)" />);
    expect(screen.getByText(/image · remote image not loaded$/)).toBeInTheDocument();
    rerender(<SafeImage alt="svg" url="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=" />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("works inside an answer written in Markdown", () => {
    const { container } = render(<Markdown text={"Look: ![chart](data:image/png;base64,AAAA) and ![spy](https://evil.example/x.png) done"} />);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(screen.getByText(/remote image not loaded \(evil.example\)/)).toBeInTheDocument();
    expect(container.textContent).toContain("Look:");
  });
});

describe("chart validation", () => {
  it("accepts the two shapes", () => {
    const a = parseChart(JSON.stringify({ title: "T", unit: "u", data: [{ label: "a", value: 1 }, { label: "b", value: 2 }] }));
    expect(a.spec).toMatchObject({ type: "bar", title: "T", unit: "u", labels: ["a", "b"], series: [{ name: "T", values: [1, 2] }] });
    expect(parseChart(JSON.stringify({ name: "N", data: [{ label: "a", value: 1 }] })).spec?.series[0].name).toBe("N");
    expect(parseChart(JSON.stringify({ data: [{ label: "a", value: 1 }] })).spec?.series[0].name).toBe("Value");
    const b = parseChart(JSON.stringify({ type: "line", x: [1, 2], series: [{ name: "s", values: [3, 4] }] }));
    expect(b.spec).toMatchObject({ type: "line", labels: ["1", "2"] });
  });

  it("explains what is wrong with a chart", () => {
    const e = (v: unknown) => parseChart(typeof v === "string" ? v : JSON.stringify(v)).error;
    expect(e("{nope")).toBe("the chart is not valid JSON");
    expect(e("[1]")).toBe("a chart is a JSON object");
    expect(e("null")).toBe("a chart is a JSON object");
    expect(e({ type: "pie", data: [] })).toBe("type must be bar or line");
    expect(e({ foo: 1 })).toMatch(/give either data/);
    expect(e({ data: [] })).toBe("a chart needs 1 to 200 points");
    expect(e({ data: Array.from({ length: 201 }, (_, i) => ({ label: String(i), value: i })) })).toBe("a chart needs 1 to 200 points");
    expect(e({ x: ["a"], series: [] })).toBe("a chart needs 1 to 6 series");
    expect(e({ x: ["a"], series: Array.from({ length: 7 }, (_, i) => ({ name: `s${i}`, values: [1] })) })).toBe("a chart needs 1 to 6 series");
    expect(e({ x: ["a", "b"], series: [{ name: "s", values: [1] }] })).toMatch(/series "s" must have one number for each of the 2 points/);
    expect(e({ x: ["a"], series: [{ name: "s", values: ["1"] }] })).toMatch(/must have one number/);
    expect(e({ x: ["a"], series: [{ name: "s" }] })).toMatch(/must have one number/);
    expect(e({ data: [{ label: "a", value: NaN }] })).toMatch(/must have one number/);
  });
});

describe("charts", () => {
  const bar = parseChart(JSON.stringify({ title: "Revenue", unit: "USD", data: [{ label: "EMEA", value: 420 }, { label: "A very long label here", value: -50 }, { label: "AMER", value: 505000 }] })).spec!;

  it("draws bars with a label for every mark, and no legend for one series", () => {
    render(<Chart spec={bar} />);
    expect(screen.getByRole("img", { name: /Revenue: Revenue across 3 points/ })).toBeInTheDocument();
    expect(screen.getByText("Revenue")).toBeInTheDocument();
    expect(document.querySelectorAll("rect")).toHaveLength(3);
    expect(document.querySelector("rect title")?.textContent).toBe("EMEA · Revenue: 420 USD");
    expect(screen.getByText("A very lo…")).toBeInTheDocument();
    expect(screen.getByText("500k")).toBeInTheDocument();
    expect(document.querySelector(".chart-legend")).toBeNull();
  });

  it("draws several series with a legend and fixed colours, as lines with markers", () => {
    const spec = parseChart(JSON.stringify({ type: "line", x: ["Jan", "Feb", "Mar"], series: [{ name: "North", values: [1, 3, 2] }, { name: "South", values: [2, 2, 5] }] })).spec!;
    render(<Chart spec={{ ...spec, unit: "ms" }} />);
    expect(document.querySelector("circle title")?.textContent).toBe("Jan · North: 1 ms");
    expect(document.querySelectorAll("polyline")).toHaveLength(2);
    expect(document.querySelectorAll("circle")).toHaveLength(6);
    expect(within(document.querySelector(".chart-legend") as HTMLElement).getByText("North")).toBeInTheDocument();
    expect((document.querySelectorAll("polyline")[1] as SVGElement).style.stroke).toBe("var(--viz-2)");
  });

  it("makes marks smaller and thins the labels when there are many points", () => {
    const labels = Array.from({ length: 60 }, (_, i) => `p${i}`);
    render(<Chart spec={{ type: "line", title: "", unit: "", labels, series: [{ name: "s", values: labels.map((_, i) => i) }] }} />);
    expect(document.querySelector("circle")).toHaveAttribute("r", "2.5");
    expect(document.querySelectorAll(".chart-tick").length).toBeLessThan(25);
    expect(screen.queryByRole("figure")?.querySelector("figcaption")).toBeNull();
  });

  it("can show the same numbers as a table", async () => {
    render(<Chart spec={bar} />);
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("A very long label here")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show chart" }));
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("copes with all-zero values", () => {
    render(<Chart spec={{ type: "bar", title: "", unit: "", labels: ["a"], series: [{ name: "s", values: [0] }] }} />);
    expect(document.querySelectorAll("rect")).toHaveLength(1);
  });
});

describe("diffs and tables", () => {
  it("colours added, removed and header lines", () => {
    const { container } = render(<DiffBlock text={"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same"} />);
    expect(container.querySelectorAll(".d-file")).toHaveLength(2);
    expect(container.querySelector(".d-hunk")).toHaveTextContent("@@ -1 +1 @@");
    expect(container.querySelector(".d-del")).toHaveTextContent("-old");
    expect(container.querySelector(".d-add")).toHaveTextContent("+new");
  });

  it("splits lines with quotes", () => {
    expect(splitLine('a,"b,c",d', ",")).toEqual(["a", "b,c", "d"]);
    expect(splitLine('"say ""hi""",x', ",")).toEqual(['say "hi"', "x"]);
    expect(splitLine("a\tb", "\t")).toEqual(["a", "b"]);
  });

  it("shows a CSV as a table you can work with, and notes an empty one", () => {
    const { rerender } = render(<CsvTable text={"name,plan\nada,pro\nlin,team"} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("columnheader", { name: /plan/ })).toBeInTheDocument();
    rerender(<CsvTable text={"   \n"} />);
    expect(screen.getByText("(empty table)")).toBeInTheDocument();
    rerender(<CsvTable text={["h", ...Array.from({ length: 250 }, (_, i) => String(i))].join("\n")} />);
    expect(screen.getAllByRole("row")).toHaveLength(26);                       // the header and the first page of 25
    expect(screen.getByText("Page 1 of 10")).toBeInTheDocument();
  });
});

describe("forms", () => {
  const spec = { title: "Pick a plan", submit: "Go", fields: [{ name: "plan", label: "Plan", type: "select", options: ["pro", "team"], required: true }, { name: "seats", label: "Seats", type: "number" },
    { name: "note", label: "Note" }, { name: "agree", label: "I agree", type: "checkbox", required: true }] };

  it("explains what is wrong with a form", () => {
    const e = (v: unknown) => parseForm(typeof v === "string" ? v : JSON.stringify(v)).error;
    expect(e("{x")).toBe("the form is not valid JSON");
    expect(e({})).toBe("a form needs 1 to 12 fields");
    expect(e({ fields: [] })).toBe("a form needs 1 to 12 fields");
    expect(e({ fields: Array.from({ length: 13 }, (_, i) => ({ name: `f${i}` })) })).toBe("a form needs 1 to 12 fields");
    expect(e({ fields: [{ name: "a", type: "radio" }] })).toBe("field type must be text, select, checkbox or number");
    expect(e({ fields: [{ name: "a", type: "select" }] })).toBe("a select field needs options");
    const ok = parseForm(JSON.stringify({ fields: [{ label: "Only label" }] })).form!;
    expect(ok).toMatchObject({ title: "", submit: "Send", fields: [{ name: "Only label", label: "Only label", type: "text", options: [], required: false }] });
  });

  it("sends the answers as the next message", async () => {
    const reply = vi.fn();
    const form = parseForm(JSON.stringify(spec)).form!;
    render(<ReplyContext.Provider value={reply}><FormBlock form={form} /></ReplyContext.Provider>);
    const go = screen.getByRole("button", { name: "Go" });
    expect(go).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText(/Plan/), "team");
    expect(go).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/I agree/));
    await userEvent.type(screen.getByLabelText("Seats"), "5");
    expect(go).toBeEnabled();
    await userEvent.click(go);
    expect(reply).toHaveBeenCalledWith("Pick a plan\n- Plan: team\n- Seats: 5\n- Note: (not answered)\n- I agree: yes");
    expect(screen.getByRole("button", { name: "Sent" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Note"), "x");
    expect(reply).toHaveBeenCalledTimes(1);
  });

  it("says no when a box is left unticked, and is read-only where there is no chat", async () => {
    const reply = vi.fn();
    const form = parseForm(JSON.stringify({ fields: [{ name: "a", label: "Agree", type: "checkbox" }, { name: "t", label: "Text" }] })).form!;
    const { unmount } = render(<ReplyContext.Provider value={reply}><FormBlock form={form} /></ReplyContext.Provider>);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(reply).toHaveBeenCalledWith("- Agree: no\n- Text: (not answered)");
    unmount();
    render(<FormBlock form={form} />);
    expect(screen.getByText("Answer in the chat box.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByLabelText("Text")).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Text"), "x");
    screen.getByRole("button", { name: "Send" }).closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
});

describe("the code-fence dispatcher", () => {
  it("knows its whitelist", () => {
    expect(["chart", "DIFF", "patch", "csv", "tsv", "form"].every(isRichLang)).toBe(true);
    expect(["js", "python", "html", "script", ""].some(isRichLang)).toBe(false);
  });

  it("draws each kind, and says what went wrong with a bad one", () => {
    const { container, rerender } = render(<RichFence lang="diff" code={"+a"} />);
    expect(container.querySelector(".diff")).toBeInTheDocument();
    rerender(<RichFence lang="patch" code={"-a"} />);
    expect(container.querySelector(".d-del")).toBeInTheDocument();
    rerender(<RichFence lang="csv" code={"a,b\n1,2"} />);
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
    rerender(<RichFence lang="tsv" code={"a\tb\n1\t2"} />);
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
    rerender(<RichFence lang="chart" code={'{"data":[{"label":"a","value":1}]}'} />);
    expect(screen.getByTestId("chart")).toBeInTheDocument();
    rerender(<RichFence lang="chart" code="{bad" />);
    expect(screen.getByRole("note")).toHaveTextContent("Could not draw this chart: the chart is not valid JSON.");
    rerender(<RichFence lang="form" code={'{"fields":[{"name":"a"}]}'} />);
    expect(screen.getByTestId("form")).toBeInTheDocument();
    rerender(<RichFence lang="form" code="{}" />);
    expect(screen.getByRole("note")).toHaveTextContent("Could not show this form: a form needs 1 to 12 fields.");
  });

  it("is used for fenced blocks in Markdown, while ordinary code stays code", () => {
    const text = "```chart\n{\"data\":[{\"label\":\"a\",\"value\":2}]}\n```\n\n```diff\n+x\n```\n\n```js\nalert(1)\n```\n\n```html\n<script>alert(1)</script>\n```";
    const { container } = render(<Markdown text={text} />);
    expect(screen.getByTestId("chart")).toBeInTheDocument();
    expect(screen.getByTestId("diff")).toBeInTheDocument();
    expect(container.querySelectorAll(".codeblock")).toHaveLength(3);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
  });
});

describe("file cards and run state", () => {
  it("shows a file with its state, a thumbnail for pictures and a download link", () => {
    const { rerender } = render(<ArtifactCard a={art()} />);
    expect(screen.getByRole("link", { name: "report.md" })).toHaveAttribute("href", "/artifacts/?id=0123456789ab");
    expect(screen.getByLabelText("Download report.md")).toHaveAttribute("href", `${API_BASE}/api/artifacts/0123456789ab/content?download=true`);
    expect(screen.getByText(/Documents · 2.0 KB · just now/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    rerender(<ArtifactCard a={art({ preview: "image", name: "plot.png", type: "image", copies: [{ storage: "local", status: "failed", detail: "x", size: 0, updated: 0 }] })} />);
    expect(screen.getByAltText("plot.png")).toHaveAttribute("src", `${API_BASE}/api/artifacts/0123456789ab/content`);
    expect(screen.getByText("1 copy failed")).toBeInTheDocument();
    rerender(<ArtifactCard a={art({ type: "weird" })} />);
    expect(screen.getByText(/weird ·/)).toBeInTheDocument();
  });

  it("lists a turn's files, four at first", async () => {
    const many = Array.from({ length: 6 }, (_, i) => art({ id: `00000000000${i}`, name: `f${i}.md` }));
    const { container, rerender } = render(<ArtifactList items={[]} />);
    expect(container.innerHTML).toBe("");
    rerender(<ArtifactList items={[many[0]]} />);
    expect(screen.getByText(/1 file made in this turn/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<ArtifactList items={many} />);
    expect(screen.getAllByTestId("artifact-card")).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    expect(screen.getAllByTestId("artifact-card")).toHaveLength(6);
    await userEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.getAllByTestId("artifact-card")).toHaveLength(4);
  });

  it("summarises a run's shared state", () => {
    render(<StatePanel state={{ agents: [{ id: "root", name: "engineer", status: "done", parent: null, cost: 0.01 }, { id: "r2", name: "helper", status: "running", parent: "root", cost: 0 }, { id: "r3", name: "bad", status: "error", parent: "root", cost: 0 }],
      totals: { model_calls: 3, tool_calls: 1, cost_usd: 0.0123 }, artifacts: 2 }} />);
    expect(screen.getByText(/Run state · 3 agents · 1 tool call$/)).toBeInTheDocument();
    expect(screen.getByText("$0.0123")).toBeInTheDocument();
    expect(screen.getByText(/lead · done/)).toBeInTheDocument();
    expect(screen.getByText(/helper · running/)).toBeInTheDocument();
    expect(document.querySelectorAll(".dot.ok, .dot.warn, .dot.bad")).toHaveLength(3);
  });

  it("uses singular words for a single agent and several tool calls", () => {
    render(<StatePanel state={{ agents: [{ id: "root", name: "a", status: "ok", parent: null, cost: 0 }], totals: { model_calls: 1, tool_calls: 2, cost_usd: 0 }, artifacts: 0 }} />);
    expect(screen.getByText(/Run state · 1 agent · 2 tool calls$/)).toBeInTheDocument();
  });
});
