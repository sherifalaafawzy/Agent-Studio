import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import * as Icons from "@/components/Icons";

type IconComponent = (p: { size?: number; className?: string }) => React.ReactElement;

describe("Icons", () => {
  const names = Object.keys(Icons) as (keyof typeof Icons)[];

  it.each(names)("%s renders an aria-hidden svg at the default size", (name) => {
    const Icon = Icons[name] as unknown as IconComponent;
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg?.getAttribute("width")).toBe(name === "Spark" ? "18" : name === "IStop" ? "14" : "18");
  });

  it("honours an explicit size and className on stroke icons", () => {
    const { container } = render(<Icons.IPlus size={24} className="extra" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("width", "24");
    expect(svg).toHaveAttribute("height", "24");
    expect(svg).toHaveClass("extra");
  });

  it("the stop icon falls back to 14px when no size is given", () => {
    const { container } = render(<Icons.IStop />);
    expect(container.querySelector("svg")).toHaveAttribute("height", "14");
    const sized = render(<Icons.IStop size={20} />).container.querySelector("svg");
    expect(sized).toHaveAttribute("width", "20");
  });

  it("Spark is the filled brand mark and accepts a size", () => {
    const { container } = render(<Icons.Spark size={30} className="mark" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("fill", "currentColor");
    expect(svg).toHaveAttribute("width", "30");
    expect(svg).toHaveClass("mark");
  });
});
