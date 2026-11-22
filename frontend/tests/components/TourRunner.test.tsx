import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TourRunner, { startTour } from "@/components/TourRunner";
import { DONE_KEY, STATE_KEY, TOURS } from "@/lib/tour";

const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { path: "/", push, router: { push } };
});

vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => nav.router,
}));

/** Elements the tour steps point at, so driver.js does not wait for them. */
function Targets() {
  return (
    <>
      <div data-tour="sidebar">sidebar</div>
      <div data-tour="composer">composer</div>
      <div data-tour="agent-select">agent select</div>
      <div data-tour="assistant">assistant</div>
      <div data-tour="agent-list">agent list</div>
      <div data-tour="context-meter">meter</div>
    </>
  );
}

function setState(state: unknown) {
  localStorage.setItem(STATE_KEY, JSON.stringify(state));
}

beforeEach(() => {
  nav.path = "/";
  nav.push.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("startTour", () => {
  it("saves the tour at its first step and announces it on the window", () => {
    const seen: unknown[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("as:tour-start", listener);
    try {
      startTour("chat");
    } finally {
      window.removeEventListener("as:tour-start", listener);
    }
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "chat", index: 0 });
    expect(seen).toEqual([{ tour: "chat" }]);
  });

  it("ignores an unknown tour name", () => {
    const listener = vi.fn();
    window.addEventListener("as:tour-start", listener);
    try {
      startTour("does-not-exist");
    } finally {
      window.removeEventListener("as:tour-start", listener);
    }
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("TourRunner", () => {
  it("renders nothing into the DOM itself", () => {
    const { container } = render(<TourRunner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("runs a saved tour on mount and shows its first step", async () => {
    setState({ tour: "chat", index: 0 });
    render(<><Targets /><TourRunner /></>);
    expect(await screen.findByText("Compose")).toBeInTheDocument();
    expect(screen.getByText(TOURS.chat.steps[0].body)).toBeInTheDocument();
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
  });

  it("advances to the next step on Next and shows progress", async () => {
    setState({ tour: "chat", index: 0 });
    const user = userEvent.setup();
    render(<><Targets /><TourRunner /></>);
    await screen.findByText("Compose");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Queue or steer")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
  });

  it("finishing the last step of the last page clears progress and marks the first run done", async () => {
    setState({ tour: "chat", index: 0 });
    const user = userEvent.setup();
    render(<><Targets /><TourRunner /></>);
    await screen.findByText("Compose");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Queue or steer");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Context window");
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("Context window")).not.toBeInTheDocument());
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
    expect(localStorage.getItem(DONE_KEY)).toBe("1");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("hands a cross-page tour to the next page when its page ends", async () => {
    setState({ tour: "welcome", index: 0 });
    const user = userEvent.setup();
    render(<><Targets /><TourRunner /></>);
    await screen.findByText("Your workbench");
    for (const title of ["Start a chat", "Pick who answers", "Ask me anything"]) {
      await user.click(screen.getByRole("button", { name: "Next" }));
      expect(await screen.findByText(title)).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/agents/"));
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "welcome", index: 4 });
    expect(localStorage.getItem(DONE_KEY)).toBeNull();
  });

  it("navigates to the page the tour expects when it is not the current page", () => {
    setState({ tour: "welcome", index: 4 });
    render(<TourRunner />);
    expect(nav.push).toHaveBeenCalledWith("/agents/");
    expect(screen.queryByText("Design your agents")).not.toBeInTheDocument();
  });

  it("does nothing when a saved index is already past the last step", () => {
    setState({ tour: "welcome", index: TOURS.welcome.steps.length });
    render(<TourRunner />);
    expect(nav.push).not.toHaveBeenCalled();
    expect(screen.queryByText("Your workbench")).not.toBeInTheDocument();
  });

  it("treats a missing pathname as the home page", async () => {
    nav.path = null as unknown as string;
    setState({ tour: "chat", index: 0 });
    render(<><Targets /><TourRunner /></>);
    expect(await screen.findByText("Compose")).toBeInTheDocument();
  });

  it("matches the current page ignoring trailing slashes", async () => {
    nav.path = "/agents/";
    setState({ tour: "agents", index: 0 });
    render(<><Targets /><TourRunner /></>);
    expect(await screen.findByText("Your agents")).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("closing with the X button stops the tour and marks the first run done", async () => {
    setState({ tour: "chat", index: 0 });
    const user = userEvent.setup();
    render(<><Targets /><TourRunner /></>);
    await screen.findByText("Compose");
    await user.click(screen.getByRole("button", { name: /close/i }));
    await waitFor(() => expect(screen.queryByText("Compose")).not.toBeInTheDocument());
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
    expect(localStorage.getItem(DONE_KEY)).toBe("1");
  });

  it("starts a tour when startTour fires while mounted", async () => {
    const user = userEvent.setup();
    render(<><Targets /><TourRunner /></>);
    expect(screen.queryByText("Compose")).not.toBeInTheDocument();
    act(() => startTour("chat"));
    expect(await screen.findByText("Compose")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /close/i }));
  });

  it("removes the popover when unmounted", async () => {
    setState({ tour: "chat", index: 0 });
    const { unmount } = render(<><Targets /><TourRunner /></>);
    await screen.findByText("Compose");
    unmount();
    expect(screen.queryByText("Compose")).not.toBeInTheDocument();
  });

  describe("first visit", () => {
    it("offers the welcome tour on the home page after a short delay", async () => {
      render(<><Targets /><TourRunner /></>);
      expect(screen.queryByText("Your workbench")).not.toBeInTheDocument();
      expect(await screen.findByText("Your workbench", {}, { timeout: 3000 })).toBeInTheDocument();
      expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "welcome", index: 0 });
    });

    it("does not offer it again once the first run is marked done", async () => {
      localStorage.setItem(DONE_KEY, "1");
      vi.useFakeTimers();
      render(<><Targets /><TourRunner /></>);
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(screen.queryByText("Your workbench")).not.toBeInTheDocument();
      expect(localStorage.getItem(STATE_KEY)).toBeNull();
    });

    it("does not offer it if the set-up guide claims the first run during the delay", () => {
      vi.useFakeTimers();
      render(<><Targets /><TourRunner /></>);
      localStorage.setItem(DONE_KEY, "1");                          // the guide marks the first run done while the timer is pending
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(screen.queryByText("Your workbench")).not.toBeInTheDocument();
      expect(localStorage.getItem(STATE_KEY)).toBeNull();
    });

    it("waits while the set-up guide gate is still asking the server, then offers it if the guide is not needed", () => {
      vi.useFakeTimers();
      sessionStorage.setItem("as-gate-pending", "1");
      render(<><Targets /><TourRunner /></>);
      act(() => { vi.advanceTimersByTime(900 + 1500); });
      expect(localStorage.getItem(STATE_KEY)).toBeNull();                          // still waiting
      sessionStorage.removeItem("as-gate-pending");                                // the gate answered: nothing to set up
      act(() => { vi.advanceTimersByTime(600); });
      expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "welcome", index: 0 });
    });

    it("stays away if the gate claims the first run while it waits", () => {
      vi.useFakeTimers();
      sessionStorage.setItem("as-gate-pending", "1");
      render(<><Targets /><TourRunner /></>);
      act(() => { vi.advanceTimersByTime(1000); });
      localStorage.setItem(DONE_KEY, "1");
      sessionStorage.removeItem("as-gate-pending");
      act(() => { vi.advanceTimersByTime(2000); });
      expect(localStorage.getItem(STATE_KEY)).toBeNull();
    });

    it("gives up waiting for the gate after about ten seconds", () => {
      vi.useFakeTimers();
      sessionStorage.setItem("as-gate-pending", "1");
      render(<><Targets /><TourRunner /></>);
      act(() => { vi.advanceTimersByTime(900 + 20 * 500 + 600); });
      expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "welcome", index: 0 });
    });

    it("does not wait when session storage cannot be read", () => {
      vi.useFakeTimers();
      const real = Storage.prototype.getItem;
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, k: string) { if (k === "as-gate-pending") throw new Error("blocked"); return real.call(this, k); });
      render(<><Targets /><TourRunner /></>);
      act(() => { vi.advanceTimersByTime(1000); });
      expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "welcome", index: 0 });
    });

    it("does not offer it on other pages", () => {
      nav.path = "/settings/";
      vi.useFakeTimers();
      render(<><Targets /><TourRunner /></>);
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(screen.queryByText("Your workbench")).not.toBeInTheDocument();
      expect(localStorage.getItem(STATE_KEY)).toBeNull();
    });

    it("cancels the pending offer when unmounted before the delay", () => {
      vi.useFakeTimers();
      const { unmount } = render(<><Targets /><TourRunner /></>);
      unmount();
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(localStorage.getItem(STATE_KEY)).toBeNull();
    });
  });
});
