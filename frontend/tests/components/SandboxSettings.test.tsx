import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SandboxSettings from "@/components/SandboxSettings";
import { jsonResponse, routeFetch } from "../helpers/http";

const KINDS = [{ id: "docker-container", label: "Docker container", available: true, detail: "ready", experimental: false, notice: "" },
  { id: "docker-microvm", label: "Docker Sandbox (micro VM)", available: false, detail: "switched off (experimental): turn it on below to use it", experimental: true, notice: "n" }];
const POLICY = { kind: "docker-container", memory: "1g", cpus: "2", pids: 256, read_only_root: false, microvm_enabled: false };

function setup() {
  let policy: any = { ...POLICY };
  return routeFetch({
    "GET /api/sandboxes": () => jsonResponse({ policy, network: false, kinds: KINDS, default: policy.kind }),
    "PUT /api/sandboxes/config": (init: any) => {
      const p = JSON.parse(init.body);
      if (p.memory === "huge") return jsonResponse({ detail: "memory must look like 512m or 2g" }, { status: 422 });
      policy = { ...policy, ...p };
      return jsonResponse({ policy, network: false, kinds: KINDS, default: policy.kind });
    },
    "POST /api/sandboxes/docker-container/test": () => jsonResponse({ ok: true, detail: "the sandbox ran a command" }),
    "POST /api/sandboxes/docker-microvm/test": () => jsonResponse({ detail: "no sbx" }, { status: 500 }),
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const lastPut = (net: ReturnType<typeof setup>) => net.calls.filter((c) => c.method === "PUT").pop()!.body;

describe("SandboxSettings", () => {
  it("shows the status of both kinds and saves each choice", async () => {
    const net = setup();
    render(<SandboxSettings />);
    expect(await screen.findByTestId("sandbox-kind-docker-container")).toHaveTextContent("ready");
    expect(screen.getByTestId("sandbox-kind-docker-microvm")).toHaveTextContent("not ready");
    await userEvent.selectOptions(screen.getByLabelText("Default sandbox kind"), "docker-microvm");
    expect(lastPut(net)).toEqual({ kind: "docker-microvm" });
    await userEvent.click(screen.getByRole("checkbox", { name: "Read-only root file system" }));
    expect(lastPut(net)).toEqual({ read_only_root: true });
    await userEvent.click(screen.getByRole("checkbox", { name: "Allow the micro VM sandbox" }));
    expect(lastPut(net)).toEqual({ microvm_enabled: true });
    const pids = screen.getByLabelText("Most processes");
    fireEvent.change(pids, { target: { value: "128" } }); fireEvent.blur(pids);
    await waitFor(() => expect(lastPut(net)).toEqual({ pids: 128 }));
  });

  it("saves memory and CPU when you leave the field, only if they changed", async () => {
    const net = setup();
    render(<SandboxSettings />);
    const mem = await screen.findByLabelText("Memory");
    fireEvent.blur(mem);
    expect(net.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    await userEvent.clear(mem);
    await userEvent.type(mem, "2g");
    fireEvent.blur(mem);
    await waitFor(() => expect(lastPut(net)).toEqual({ memory: "2g" }));
    const cpus = screen.getByLabelText("CPUs");
    await userEvent.clear(cpus);
    await userEvent.type(cpus, "4");
    fireEvent.blur(cpus);
    await waitFor(() => expect(lastPut(net)).toEqual({ cpus: "4" }));
    fireEvent.blur(cpus);                                                                     // unchanged now
    expect(net.calls.filter((c) => c.method === "PUT")).toHaveLength(2);
  });

  it("shows the server's reason for a refused value and puts the field back", async () => {
    setup();
    render(<SandboxSettings />);
    const mem = await screen.findByLabelText("Memory");
    await userEvent.clear(mem);
    await userEvent.type(mem, "huge");
    fireEvent.blur(mem);
    expect(await screen.findByRole("alert")).toHaveTextContent("memory must look like 512m or 2g");
    await waitFor(() => expect((screen.getByLabelText("Memory") as HTMLInputElement).value).toBe("1g"));
  });

  it("tests each kind and shows a failure as text", async () => {
    setup();
    render(<SandboxSettings />);
    const a = await screen.findByTestId("sandbox-kind-docker-container");
    await userEvent.click(within(a).getByRole("button", { name: "Test" }));
    expect(await within(a).findByRole("status")).toHaveTextContent("the sandbox ran a command");
    const b = screen.getByTestId("sandbox-kind-docker-microvm");
    await userEvent.click(within(b).getByRole("button", { name: "Test" }));
    expect(await within(b).findByRole("status")).toHaveTextContent("no sbx");
  });

  it("shows an error when the status cannot be loaded", async () => {
    routeFetch({ "GET /api/sandboxes": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<SandboxSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
