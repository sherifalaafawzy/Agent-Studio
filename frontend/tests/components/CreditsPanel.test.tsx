import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CreditsPanel, { type CreditsInfo } from "@/components/CreditsPanel";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const NOW = new Date("2026-11-21T12:00:00Z");
const nowSec = NOW.getTime() / 1000;

const base: CreditsInfo = {
  mode: "auto", balance_usd: 10, as_of: nowSec - 300, source: "OpenRouter", strict: false, remaining_usd: 8, checked_at: null, error: null,
  auto_available: true, why_not: null, spent_since_usd: 0.5,
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("CreditsPanel: summary line", () => {
  it("shows the remaining balance and where it came from", () => {
    render(<CreditsPanel pid="p1" credits={base} onChange={() => {}} />);
    expect(screen.getByText("$8.0000 left")).toHaveClass("ok");
    expect(screen.getByText(/OpenRouter · 5 min ago · \$0.5000 used here since/)).toBeInTheDocument();
  });

  it("flags a balance under 10% of the original as low", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, remaining_usd: 0.5 }} onChange={() => {}} />);
    expect(screen.getByText("$0.5000 left")).toHaveClass("bad");
  });

  it.each([
    [nowSec - 30, "just now"],
    [nowSec - 600, "10 min ago"],
    [nowSec - 7200, "2 h ago"],
    [nowSec - 3 * 86400, "3 d ago"],
  ])("describes a check time %#", (asOf, text) => {
    render(<CreditsPanel pid="p1" credits={{ ...base, as_of: asOf, spent_since_usd: null }} onChange={() => {}} />);
    expect(screen.getByText(new RegExp(`OpenRouter · ${text}`))).toBeInTheDocument();
  });

  it("omits the age when there is no timestamp", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, as_of: null, spent_since_usd: null }} onChange={() => {}} />);
    expect(screen.getByText("OpenRouter")).toBeInTheDocument();
  });

  it("says when credits are not tracked", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, remaining_usd: null, mode: "off", source: null, as_of: null }} onChange={() => {}} />);
    expect(screen.getByText("not tracked")).toBeInTheDocument();
  });

  it("says unknown for manual tracking without a balance", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, remaining_usd: null, mode: "manual" }} onChange={() => {}} />);
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("shows the provider error", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, error: "401 from provider" }} onChange={() => {}} />);
    expect(screen.getByText("401 from provider")).toBeInTheDocument();
  });
});

describe("CreditsPanel: automatic refresh", () => {
  it("offers a refresh when the provider reports balances", () => {
    render(<CreditsPanel pid="p1" credits={base} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Refresh from provider" })).toBeInTheDocument();
    expect(screen.getByText(/Fetched automatically from the provider/)).toBeInTheDocument();
  });

  it("explains why the provider cannot report balances", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, auto_available: false, why_not: "This provider has no balance API." }} onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: "Refresh from provider" })).toBeNull();
    expect(screen.getByText(/This provider has no balance API\./)).toBeInTheDocument();
  });

  it("refreshes from the provider and reports back", async () => {
    const onChange = vi.fn();
    const { calls } = routeFetch({ "POST /api/providers/p1/credits/refresh": () => jsonResponse({ ok: true }) });
    render(<CreditsPanel pid="p1" credits={base} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Refresh from provider" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(calls[0].method).toBe("POST");
    expect(screen.getByRole("button", { name: "Refresh from provider" })).toBeEnabled();
  });

  it("shows the busy label while checking", async () => {
    let finish!: (r: Response) => void;
    routeFetch({ "POST /api/providers/p1/credits/refresh": () => new Promise<Response>((r) => { finish = r; }) });
    render(<CreditsPanel pid="p1" credits={base} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Refresh from provider" }));
    expect(await screen.findByRole("button", { name: "Checking…" })).toBeDisabled();
    finish(jsonResponse({}));
  });

  it("shows an error when the refresh fails", async () => {
    const onChange = vi.fn();
    routeFetch({ "POST /api/providers/p1/credits/refresh": () => jsonResponse({ detail: "key revoked" }, { status: 401 }) });
    render(<CreditsPanel pid="p1" credits={base} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Refresh from provider" }));
    expect(await screen.findByText("⚠ key revoked")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("CreditsPanel: manual controls", () => {
  it("sets a manual balance and clears the field on success", async () => {
    const onChange = vi.fn();
    const { calls } = routeFetch({ "PUT /api/providers/p1/credits": () => jsonResponse({ ok: true }) });
    render(<CreditsPanel pid="p1" credits={{ ...base, auto_available: false, why_not: "no api" }} onChange={onChange} />);
    const input = screen.getByPlaceholderText("your current balance ($)") as HTMLInputElement;
    const set = screen.getByRole("button", { name: "Set balance" });
    expect(set).toBeDisabled();
    await userEvent.type(input, "12.5");
    expect(set).toBeEnabled();
    await userEvent.click(set);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ balance_usd: 12.5 });
    expect(input.value).toBe("");
  });

  it("uses the auto-mode placeholder when the provider can report balances", () => {
    render(<CreditsPanel pid="p1" credits={base} onChange={() => {}} />);
    expect(screen.getByPlaceholderText("or enter a balance manually ($)")).toBeInTheDocument();
  });

  it("keeps the field and shows the error when saving fails", async () => {
    routeFetch({ "PUT /api/providers/p1/credits": () => jsonResponse({ detail: "negative balance" }, { status: 400 }) });
    render(<CreditsPanel pid="p1" credits={base} onChange={() => {}} />);
    await userEvent.type(screen.getByPlaceholderText("or enter a balance manually ($)"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Set balance" }));
    expect(await screen.findByText("⚠ negative balance")).toBeInTheDocument();
  });

  it("stops tracking when the provider is still tracked", async () => {
    const onChange = vi.fn();
    const { calls } = routeFetch({ "PUT /api/providers/p1/credits": () => jsonResponse({}) });
    render(<CreditsPanel pid="p1" credits={base} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Stop tracking" }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ mode: "off" });
  });

  it("hides stop tracking once tracking is off", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, mode: "off" }} onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: "Stop tracking" })).toBeNull();
  });

  it("toggles strict mode", async () => {
    const onChange = vi.fn();
    const { calls } = routeFetch({ "PUT /api/providers/p1/credits": () => jsonResponse({}) });
    render(<CreditsPanel pid="p1" credits={base} onChange={onChange} />);
    const strict = screen.getByRole("checkbox");
    expect(strict).not.toBeChecked();
    await userEvent.click(strict);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ strict: true });
  });

  it("shows the strict toggle as on when strict is set", () => {
    render(<CreditsPanel pid="p1" credits={{ ...base, strict: true }} onChange={() => {}} />);
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(within(screen.getByText(/Strict:/).closest("label")!).getByRole("checkbox")).toBeChecked();
  });
});
