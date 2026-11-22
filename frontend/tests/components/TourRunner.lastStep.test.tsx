import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TourRunner from "@/components/TourRunner";
import { DONE_KEY, STATE_KEY } from "@/lib/tour";

// driver.js itself only ever invokes the last step's onDoneClick (it prefers it over onNextClick on the final
// step), so the onNextClick hook TourRunner also installs can only be reached with the library stubbed.
const drv = vi.hoisted(() => ({
  config: null as null | { steps: { popover: Record<string, unknown> }[] },
  destroy: vi.fn(),
  drive: vi.fn(),
}));
vi.mock("driver.js", () => ({
  driver: (cfg: typeof drv.config) => {
    drv.config = cfg;
    return { destroy: drv.destroy, drive: drv.drive };
  },
}));
vi.mock("driver.js/dist/driver.css", () => ({}));

const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { path: "/", push, router: { push } };
});
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => nav.router,
}));

beforeEach(() => {
  nav.path = "/";
  nav.push.mockReset();
  drv.destroy.mockReset();
  drv.drive.mockReset();
  drv.config = null;
});

describe("TourRunner last-step hooks", () => {
  it("only the last step carries next/done hooks; non-last steps leave driver's defaults", () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ tour: "chat", index: 0 }));
    render(<TourRunner />);
    const steps = drv.config!.steps;
    expect(steps).toHaveLength(3);
    expect(steps[0].popover.onNextClick).toBeUndefined();
    expect(steps[1].popover.onDoneClick).toBeUndefined();
    expect(typeof steps[2].popover.onNextClick).toBe("function");
    expect(typeof steps[2].popover.onDoneClick).toBe("function");
    expect(drv.drive).toHaveBeenCalledTimes(1);
  });

  it("onNextClick on the final step ends the tour like Done does", () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ tour: "chat", index: 0 }));
    render(<TourRunner />);
    drv.destroy.mockClear();
    (drv.config!.steps[2].popover.onNextClick as () => void)();
    expect(drv.destroy).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
    expect(localStorage.getItem(DONE_KEY)).toBe("1");
    expect(nav.push).not.toHaveBeenCalled();
  });
});
