import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import FolderPicker from "@/components/FolderPicker";

describe("FolderPicker compact mode", () => {
  it("uses a narrower text field when compact", () => {
    render(<FolderPicker value="" onChange={() => {}} placeholder="p" compact />);
    expect(screen.getByPlaceholderText("p").style.minWidth).toBe("120px");
  });
});
