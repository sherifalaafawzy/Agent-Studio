import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ModelSelect from "@/components/ModelSelect";
import { jsonResponse, routeFetch } from "../helpers/http";

describe("ModelSelect branches", () => {
  it("prints an unknown output price as $0 when only the input price is known", async () => {
    routeFetch({
      "GET /api/providers/ms-branch-1/models": () => jsonResponse({
        provider: "p", source: "live", error: null, cached: false,
        models: [{ id: "half", label: "Half", input_per_m: 2, output_per_m: null }],
      }),
    });
    render(<ModelSelect provider="ms-branch-1" value="" onChange={() => {}} />);
    expect(await screen.findByRole("option", { name: "Half (half) · $2/$0 per M" })).toBeInTheDocument();
  });

  it("keeps the typed-id input shrink-to-fit in compact mode", async () => {
    routeFetch({
      "GET /api/providers/ms-branch-2/models": () => jsonResponse({
        provider: "p", source: "live", error: null, cached: false,
        models: [{ id: "m", label: "m", input_per_m: null, output_per_m: null }],
      }),
    });
    render(<ModelSelect provider="ms-branch-2" value="" onChange={() => {}} compact />);
    await screen.findByRole("option", { name: "m" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "__custom");
    const wrap = screen.getByPlaceholderText("exact model id").parentElement!;
    expect(wrap.style.width).toBe("");
  });
});
