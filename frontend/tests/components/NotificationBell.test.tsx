import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

const toast = vi.fn();
vi.mock("@/components/Nav", () => ({ toast: (...a: unknown[]) => toast(...a) }));
import NotificationBell, { type Note } from "@/components/NotificationBell";

const now = () => Date.now() / 1000;
const note = (id: string, over: Partial<Note> = {}): Note => ({ id, created: now() - 30, title: `Title ${id}`, body: "", level: "info", source: "workflow", link: "", read: false, desktop: false, ...over });

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); toast.mockReset(); });

describe("Notification bell", () => {
  it("shows the unread count and the list, and marks, deletes and clears", async () => {
    let items: Note[] = [note("a", { created: now() - 300, body: "x".repeat(320), level: "error", link: "/workflows/?tab=runs&run=r1" }), note("b", { read: true, level: "success", created: now() - 7200 }),
      note("c", { level: "warning", created: now() - 3 * 86400 }), note("d", { created: now() - 5, title: "fresh" })];
    const { calls } = routeFetch({
      "GET /api/notifications": () => jsonResponse({ items, unread: items.filter((n) => !n.read).length }),
      "POST /api/notifications/a/read": () => { items = items.map((n) => (n.id === "a" ? { ...n, read: true } : n)); return jsonResponse({ ok: true }); },
      "POST /api/notifications/c/read": () => jsonResponse({ detail: "gone" }, { status: 404 }),
      "POST /api/notifications/read-all": () => { items = items.map((n) => ({ ...n, read: true })); return jsonResponse({ ok: true }); },
      "DELETE /api/notifications/b": () => { items = items.filter((n) => n.id !== "b"); return jsonResponse({ ok: true }); },
      "DELETE /api/notifications": () => { items = []; return jsonResponse({ ok: true }); },
    });
    const user = userEvent.setup();
    render(<NotificationBell />);
    expect(await screen.findByTestId("bell-count")).toHaveTextContent("3");
    await user.click(screen.getByTestId("bell"));
    expect(screen.getAllByTestId("note")).toHaveLength(4);
    expect(screen.getByText(/x+…/)).toBeInTheDocument();
    expect(screen.getByText("just now")).toBeInTheDocument();
    expect(screen.getByText("5 min ago")).toBeInTheDocument();
    expect(screen.getByText("2 h ago")).toBeInTheDocument();
    expect(screen.getByText("3 d ago")).toBeInTheDocument();
    await user.click(screen.getByText("Open"));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/notifications/a/read")).toBe(true));
    await user.click(screen.getByTestId("bell"));
    await user.click(screen.getAllByText("Mark read")[0]);                                          // c: the server says it is gone; the list just reloads
    await user.click(screen.getByLabelText("Delete Title b"));
    await waitFor(() => expect(screen.getAllByTestId("note")).toHaveLength(3));
    await user.click(screen.getByText("Mark all read"));
    await waitFor(() => expect(screen.queryByTestId("bell-count")).not.toBeInTheDocument());
    await user.click(screen.getByText("Clear"));
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("toasts and pops up new notifications, asks for desktop permission, and survives offline polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let items: Note[] = [note("old")];
    let fail = false;
    routeFetch({ "GET /api/notifications": () => (fail ? Promise.reject(new Error("offline")) : jsonResponse({ items, unread: items.length })) });
    const shown: unknown[] = [];
    class FakeNotification { static permission = "default"; static requestPermission = vi.fn(async () => { FakeNotification.permission = "granted"; return "granted"; }); constructor(t: string, o: unknown) { shown.push([t, o]); } }
    vi.stubGlobal("Notification", FakeNotification);
    render(<NotificationBell />);
    expect(await screen.findByTestId("bell-count")).toHaveTextContent("1");
    expect(toast).not.toHaveBeenCalled();                                                           // what was there at start is not announced
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByTestId("bell"));
    await user.click(screen.getByText("Allow desktop pop-ups"));
    expect(FakeNotification.requestPermission).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText("Allow desktop pop-ups")).not.toBeInTheDocument());
    items = [note("n2", { created: now() + 1, title: "Second", level: "warning", desktop: true, body: "b" }), note("n1", { created: now(), title: "First", level: "odd" }), ...items];
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(2));
    expect(toast.mock.calls).toEqual([["First", "ok"], ["Second", "warn"]]);
    expect(shown).toEqual([["Second", { body: "b", tag: "n2" }]]);
    fail = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(screen.getByTestId("bell-count")).toHaveTextContent("3");
  });

  it("handles a refused permission and a browser that throws on pop-ups", async () => {
    routeFetch({ "GET /api/notifications": () => jsonResponse({ items: [], unread: 0 }) });
    class Throws { static permission = "default"; static requestPermission = vi.fn(async () => { throw new Error("no"); }); constructor() { throw new Error("service worker only"); } }
    vi.stubGlobal("Notification", Throws);
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(await screen.findByTestId("bell"));
    await user.click(screen.getByText("Allow desktop pop-ups"));
    await waitFor(() => expect(screen.queryByText("Allow desktop pop-ups")).not.toBeInTheDocument());
    expect(screen.getByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("does not crash when a desktop pop-up throws, and shows 99+", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let items: Note[] = [];
    routeFetch({ "GET /api/notifications": () => jsonResponse({ items, unread: items.length ? 150 : 0 }) });
    class Granted { static permission = "granted"; constructor() { throw new Error("service worker only"); } }
    vi.stubGlobal("Notification", Granted);
    render(<NotificationBell />);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    items = [note("x", { created: now() + 5, desktop: true })];
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(await screen.findByTestId("bell-count")).toHaveTextContent("99+");
    expect(toast).toHaveBeenCalledWith("Title x", "ok");
  });
});
