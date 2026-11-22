import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Scorecard from "@/components/Scorecard";
import { jsonResponse, routeFetch } from "../helpers/http";

const CHECKS = [
  { id: "guard-input", title: "Guardrails check what goes into models", maps: ["LLM01", "ASI01"], status: "fail", detail: "Guardrails are switched off.", fix: "Turn guardrails on.", link: "/settings/#security", kind: "setting" },
  { id: "sandbox-only", title: "Commands run only inside a Docker sandbox", maps: ["LLM01"], status: "pass", detail: "No fallback.", fix: "", link: "/settings/#security", kind: "built-in" },
  { id: "limits", title: "Runaway loops are capped", maps: ["LLM02"], status: "warn", detail: "High caps.", fix: "Lower them.", link: "/settings/#harness", kind: "setting" },
];
const card = (percent: number) => ({ percent, counts: { pass: 1, warn: 1, fail: 1 }, note: "This measures configuration, not attacks.", checks: CHECKS, frameworks: [
  { id: "llm2025", name: "OWASP Top 10 for LLM Applications 2025", url: "https://genai.owasp.org/llm-top-10/", covered: 2, risks: [
    { id: "LLM01", name: "Prompt Injection", status: "fail", checks: ["guard-input", "sandbox-only"] }, { id: "LLM02", name: "Sensitive Information Disclosure", status: "warn", checks: ["limits"] },
    { id: "LLM03", name: "Supply Chain", status: "none", checks: [] }] },
  { id: "asi2026", name: "OWASP Top 10 for Agentic Applications 2026", url: "https://genai.owasp.org/agentic", covered: 1, risks: [{ id: "ASI01", name: "Agent Goal Hijack", status: "pass", checks: ["sandbox-only"] }] }] });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Scorecard", () => {
  it("shows the score with status words, not only colours, and the honest note", async () => {
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(card(45)) });
    render(<Scorecard />);
    expect(await screen.findByLabelText("Score 45 percent")).toBeInTheDocument();
    expect(screen.getByText("1 in place · 1 partly · 1 missing")).toBeInTheDocument();
    expect(screen.getByText(/measures configuration, not attacks/)).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 risks have a control here/)).toBeInTheDocument();
    const risks = screen.getAllByRole("button", { pressed: false }).filter((b) => b.className.includes("risk"));
    expect(risks.map((b) => b.getAttribute("data-status"))).toEqual(["fail", "warn", "none"]);
    expect(within(risks[0]).getByText("Missing")).toBeInTheDocument();
    expect(within(risks[1]).getByText("Partly")).toBeInTheDocument();
    expect(within(risks[2]).getByText("Not covered")).toBeInTheDocument();
    expect(screen.getByText("Download report (Markdown)")).toHaveAttribute("href", "http://127.0.0.1:8765/api/security/scorecard?format=md");
  });

  it("opens a risk to show its checks with what to do about them", async () => {
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(card(45)) });
    render(<Scorecard />);
    await userEvent.click(await screen.findByRole("button", { name: /LLM01/ }));
    const d = screen.getByTestId("risk-detail");
    expect(within(d).getByText("Guardrails check what goes into models")).toBeInTheDocument();
    expect(within(d).getByText("built in")).toBeInTheDocument();
    expect(within(d).getByText(/What to do: Turn guardrails on\./)).toBeInTheDocument();
    expect(within(d).getByRole("link", { name: "Open →" })).toHaveAttribute("href", "/settings/#security");
    expect(within(d).queryByText(/What to do: .*No fallback/)).toBeNull();                       // a passing check has nothing to fix
    await userEvent.click(screen.getByRole("button", { name: /LLM01/ }));
    expect(screen.queryByTestId("risk-detail")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /LLM03/ }));
    expect(screen.getByText("No control in Agent Studio is mapped to this risk yet.")).toBeInTheDocument();
  });

  it("switches between the two OWASP lists and closes the open risk", async () => {
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(card(90)) });
    render(<Scorecard />);
    await userEvent.click(await screen.findByRole("button", { name: /LLM01/ }));
    await userEvent.click(screen.getByRole("tab", { name: /Agentic Applications/ }));
    expect(screen.queryByTestId("risk-detail")).toBeNull();
    expect(screen.getByRole("tab", { name: /Agentic Applications/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Agent Goal Hijack")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "source" })).toHaveAttribute("href", "https://genai.owasp.org/agentic");
  });

  it("uses a warning bar for middling scores and re-checks on request", async () => {
    let n = 0;
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(card([45, 65, 95][n++ % 3])) });
    render(<Scorecard />);
    const bar = async () => (await screen.findByRole("presentation")).querySelector("i")!;
    await screen.findByLabelText("Score 45 percent");
    expect((await bar()).className).toBe("b");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await screen.findByLabelText("Score 65 percent");
    expect((await bar()).className).toBe("w");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await screen.findByLabelText("Score 95 percent");
    expect((await bar()).className).toBe("");
  });

  it("reports when the scorecard cannot be loaded", async () => {
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse({ detail: "offline" }, { status: 500 }) });
    render(<Scorecard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("offline"));
  });
});
