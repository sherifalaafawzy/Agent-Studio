import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TourRunner, { startTour } from "@/components/TourRunner";

const nav = vi.hoisted(() => ({ path: "/" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => ({ push: () => {} }),
}));

// Replace the first step of the welcome tour with one that points at no element. TourRunner reads its
// steps through lib/tour's slice(), which closes over the internal tour table, so slice itself is replaced.
vi.mock("@/lib/tour", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/tour")>();
  return {
    ...actual,
    slice: (tour: string, index: number, path: string) =>
      tour === "welcome" && index === 0
        ? { steps: [{ page: "/", title: "Just text", body: "No element to point at." }], next: null, nextPage: null }
        : actual.slice(tour, index, path),
  };
});

describe("TourRunner: a step without a target element", () => {
  it("shows the popover for a step that has no element to highlight", async () => {
    nav.path = "/";
    render(<TourRunner />);
    act(() => startTour("welcome"));
    await waitFor(() => expect(screen.getByText("Just text")).toBeInTheDocument());
    expect(screen.getByText("No element to point at.")).toBeInTheDocument();
  });
});
