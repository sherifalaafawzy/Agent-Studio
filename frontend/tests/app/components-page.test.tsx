import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ComponentsPage from "@/app/components/page";
import UiKitSettings from "@/components/UiKitSettings";
import { KIND_LIST } from "@/lib/uikit/samples.generated";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => ({ setOption: vi.fn(), dispatchAction: vi.fn(), resize: vi.fn(), dispose: vi.fn(), getDataURL: vi.fn() })) }));
vi.mock("echarts/charts", () => ({ BarChart: {}, LineChart: {}, ScatterChart: {} }));
vi.mock("echarts/components", () => ({ DataZoomComponent: {}, GridComponent: {}, LegendComponent: {}, MarkAreaComponent: {}, MarkLineComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("maplibre-gl", () => ({ Map: class { on() { /* nothing */ } addControl() { /* nothing */ } remove() { /* nothing */ } }, NavigationControl: class { /* nothing */ }, Popup: class { remove() { /* nothing */ } }, addProtocol: () => undefined, setWorkerUrl: () => undefined }));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
vi.mock("pmtiles", () => ({ Protocol: class { tile = vi.fn(); } }));
vi.mock("katex", () => ({ default: { renderToString: (s: string) => s } }));
vi.mock("katex/dist/katex.min.css", () => ({}));
vi.mock("qrcode-generator", () => ({ default: () => ({ addData: () => undefined, make: () => undefined, getModuleCount: () => 3, isDark: () => true }) }));

describe("the component gallery", () => {
  it("lists every kind, filters by group and search, and draws your own spec", async () => {
    routeFetch({ "GET /api/uikit/config": () => jsonResponse({ basemap: { kind: "none" } }), "GET /api/artifacts/0123456789ab": () => jsonResponse({ id: "0123456789ab", name: "a.mp3", type: "audio", mime: "audio/mpeg", size: 1 }), "GET /api/artifacts/ba9876543210": () => jsonResponse({ id: "ba9876543210", name: "a.mp4", type: "video", mime: "video/mp4", size: 1 }) });
    render(<ComponentsPage />);
    expect(screen.getByRole("button", { name: `All (${KIND_LIST.length})` })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Cards/ }));
    expect(document.querySelectorAll(".gal-item").length).toBe(KIND_LIST.filter((k) => k.group === "card").length);
    await userEvent.type(screen.getByLabelText("Search components"), "weather");
    expect(document.querySelectorAll(".gal-item").length).toBe(1);
    await userEvent.clear(screen.getByLabelText("Search components")); await userEvent.type(screen.getByLabelText("Search components"), "zzzzzz");
    expect(screen.getByText("No component matches that search.")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Search components"));
    await userEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(document.querySelectorAll(".gal-item").length).toBe(KIND_LIST.length);
    for (const g of ["Tables", "Charts", "Time series", "Maps", "Layouts", "Audio and video", "JSON and HTML"]) { await userEvent.click(screen.getByRole("button", { name: new RegExp(`^${g}`) })); expect(document.querySelectorAll(".gal-item").length).toBeGreaterThan(0); }
    await userEvent.click(screen.getByText("Try your own spec"));
    await userEvent.type(screen.getByLabelText("Component spec (JSON)"), '{{"type": "card.alert", "text": "mine"}');
    await userEvent.click(screen.getByRole("button", { name: "Draw it" })); expect((await screen.findAllByText("mine")).length).toBeGreaterThan(0);
    await userEvent.clear(screen.getByLabelText("Component spec (JSON)")); await userEvent.type(screen.getByLabelText("Component spec (JSON)"), "not json");
    await userEvent.click(screen.getByRole("button", { name: "Draw it" })); expect(await screen.findAllByText(/not valid JSON/)).not.toHaveLength(0);
  });
});

describe("the component settings card", () => {
  const cfg = { enabled: true, max_kb: 300, max_rows: 5000, basemap: { kind: "none", tile_url: "", attribution: "", pmtiles: "" } };
  it("saves the limits and a tile basemap, and explains the privacy trade-off", async () => {
    const { calls } = routeFetch({ "GET /api/uikit/config": () => jsonResponse(cfg), "PUT /api/uikit/config": (init) => jsonResponse({ ...cfg, ...JSON.parse(String(init?.body)) }) });
    render(<UiKitSettings />);
    await screen.findByText("Rich components");
    expect(screen.getByRole("note")).toHaveTextContent(/load nothing from the internet/);
    await userEvent.clear(screen.getByLabelText("Largest component")); await userEvent.type(screen.getByLabelText("Largest component"), "500");
    await userEvent.clear(screen.getByLabelText("Most table rows")); await userEvent.type(screen.getByLabelText("Most table rows"), "8000");
    await userEvent.click(screen.getByRole("checkbox", { name: /Agents can show/ }));
    await userEvent.selectOptions(screen.getByLabelText("Basemap"), "tiles");
    await userEvent.type(screen.getByLabelText("Tile address"), "https://t.example/{{z}/{{x}/{{y}.png"); await userEvent.type(screen.getByLabelText("Attribution"), "© T");
    await userEvent.click(screen.getByRole("button", { name: "Save component settings" }));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toMatchObject({ enabled: false, max_kb: 500, max_rows: 8000, basemap: { kind: "tiles", tile_url: "https://t.example/{z}/{x}/{y}.png", attribution: "© T" } });
    await userEvent.selectOptions(screen.getByLabelText("Basemap"), "pmtiles"); await userEvent.type(screen.getByLabelText("PMTiles artifact"), "0123456789ab");
    await userEvent.selectOptions(screen.getByLabelText("Basemap"), "none");
  });
  it("chooses the AG-UI switches new agents start with (and treats an old config as both on)", async () => {
    const { calls } = routeFetch({ "GET /api/uikit/config": () => jsonResponse(cfg), "PUT /api/uikit/config": (init) => jsonResponse({ ...cfg, ...JSON.parse(String(init?.body)) }) });
    render(<UiKitSettings />);
    await screen.findByText("Rich components");
    expect(screen.getByLabelText("New agents show rich components")).toBeChecked();
    expect(screen.getByLabelText("New agents are reachable over the AG-UI stream")).toBeChecked();
    await userEvent.click(screen.getByLabelText("New agents show rich components"));
    await userEvent.click(screen.getByLabelText("New agents are reachable over the AG-UI stream"));
    await userEvent.click(screen.getByRole("button", { name: "Save component settings" }));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ new_agents: { components: false, endpoint: false } });
  });
  it("shows why saving failed and why loading failed", async () => {
    routeFetch({ "GET /api/uikit/config": () => jsonResponse(cfg), "PUT /api/uikit/config": () => jsonResponse({ detail: "max_kb must be a whole number" }, { status: 400 }) });
    const { unmount } = render(<UiKitSettings />); await screen.findByText("Rich components");
    await userEvent.click(screen.getByRole("button", { name: "Save component settings" })); expect(await screen.findByRole("alert")).toHaveTextContent("max_kb must be");
    unmount();
    routeFetch({ "GET /api/uikit/config": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<UiKitSettings />); expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
    expect(within(document.body).getByTestId("uikit-settings")).toBeInTheDocument();
  });
});
