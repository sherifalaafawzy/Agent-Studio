import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";
import WebhookBox from "@/components/workflows/WebhookBox";

describe("Webhook box", () => {
  it("makes the secret address once, shows it, copies it, and removes it", async () => {
    let exists = false;
    routeFetch({
      "GET /api/workflows/wf_hook/hook": () => jsonResponse({ exists, created: exists ? Date.now() / 1000 - 60 : null, last_used: exists ? Date.now() / 1000 - 30 : null, calls: exists ? 3 : 0, webhooks_enabled: false }),
      "POST /api/workflows/wf_hook/hook": () => { exists = true; return jsonResponse({ path: "/api/hooks/wf_hook/SECRET123" }); },
      "DELETE /api/workflows/wf_hook/hook": () => { exists = false; return jsonResponse({ ok: true }); },
    });
    const user = userEvent.setup();
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<WebhookBox workflowId="wf_hook" saved />);
    expect(await screen.findByText(/No address yet\. Make one/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Webhooks are switched off");
    await user.click(screen.getByText("Make the address"));
    const secret = await screen.findByTestId("webhook-secret");
    expect(secret).toHaveTextContent("shown only once");
    expect(screen.getByLabelText("Webhook address")).toHaveValue("http://127.0.0.1:8765/api/hooks/wf_hook/SECRET123");
    fireEvent.focus(screen.getByLabelText("Webhook address"));                                                // focusing the address selects it, ready to copy
    expect(await screen.findByText(/called 3 time\(s\)/)).toBeInTheDocument();
    await user.click(screen.getByText("Copy"));
    expect(writeText).toHaveBeenCalledWith("http://127.0.0.1:8765/api/hooks/wf_hook/SECRET123");
    expect(screen.getByText("Make a new address (replaces the old)")).toBeInTheDocument();
    await user.click(screen.getByText("Remove"));
    await waitFor(() => expect(screen.queryByTestId("webhook-secret")).not.toBeInTheDocument());
    expect(await screen.findByText(/No address yet/)).toBeInTheDocument();
  });

  it("asks for a saved version first, and shows errors", async () => {
    routeFetch({
      "GET /api/workflows/wf_hook/hook": () => jsonResponse({ exists: true, created: null, last_used: null, calls: 0, webhooks_enabled: true }),
      "POST /api/workflows/wf_hook/hook": () => jsonResponse({ detail: "this workflow does not start with a Webhook box: add one first" }, { status: 422 }),
      "DELETE /api/workflows/wf_hook/hook": () => jsonResponse({ detail: "nope" }, { status: 500 }),
    });
    const user = userEvent.setup();
    const { unmount } = render(<WebhookBox workflowId="wf_hook" saved={false} />);
    expect(await screen.findByText(/Made never/)).toBeInTheDocument();
    expect(screen.getByText("Make a new address (replaces the old)")).toBeDisabled();
    unmount();
    render(<WebhookBox workflowId="wf_hook" saved />);
    await user.click(await screen.findByText("Make a new address (replaces the old)"));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not start with a Webhook box");
    await user.click(screen.getByText("Remove"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("nope"));
  });

  it("says so when the status cannot be read, and prompts to save first when nothing exists", async () => {
    routeFetch({ "GET /api/workflows/wf_hook/hook": () => jsonResponse({ detail: "unknown workflow" }, { status: 404 }) });
    const { unmount } = render(<WebhookBox workflowId="wf_hook" saved={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown workflow");
    unmount();
    routeFetch({ "GET /api/workflows/wf_hook/hook": () => jsonResponse({ exists: false, created: null, last_used: null, calls: 0, webhooks_enabled: true }) });
    render(<WebhookBox workflowId="wf_hook" saved={false} />);
    expect(await screen.findByText(/Save a version with this Webhook box first/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
