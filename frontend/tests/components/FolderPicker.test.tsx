import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import FolderPicker from "@/components/FolderPicker";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const listing = (path: string | null, parent: string | null, dirs: { name: string; path: string }[]) => ({ path, parent, dirs, home: "C:/Users/me" });

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("FolderPicker: native dialog", () => {
  it("opens the desktop folder dialog starting from the current value", async () => {
    const onChange = vi.fn();
    const { calls } = routeFetch({ "POST /api/fs/pick-folder": () => jsonResponse({ path: "D:/work", cancelled: false }) });
    render(<FolderPicker value="C:/start" onChange={onChange} placeholder="Pick" />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("D:/work"));
    expect(calls[0].body).toEqual({ initial: "C:/start" });
  });

  it("sends a null start when the field is empty", async () => {
    const { calls } = routeFetch({ "POST /api/fs/pick-folder": () => jsonResponse({ path: null, cancelled: true }) });
    const onChange = vi.fn();
    render(<FolderPicker value="" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ initial: null });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows the busy state while the dialog is open", async () => {
    const d = deferred<Response>();
    routeFetch({ "POST /api/fs/pick-folder": () => d.promise });
    render(<FolderPicker value="" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(await screen.findByRole("button", { name: /Choose in the dialog/ })).toBeDisabled();
    expect(screen.getByText(/A folder dialog opened on your desktop/)).toBeInTheDocument();
    d.resolve(jsonResponse({ path: null, cancelled: true }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Browse/ })).toBeEnabled());
  });

  it("shows a server error from the dialog", async () => {
    routeFetch({ "POST /api/fs/pick-folder": () => jsonResponse({ detail: "dialog crashed" }, { status: 500 }) });
    render(<FolderPicker value="" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(await screen.findByText("dialog crashed")).toHaveClass("err");
  });

  it("shows a network failure from the dialog", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<FolderPicker value="" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
  });

  it("lets the user type a path directly", async () => {
    const onChange = vi.fn();
    render(<FolderPicker value="" onChange={onChange} placeholder="C:/projects" />);
    await userEvent.type(screen.getByPlaceholderText("C:/projects"), "x");
    expect(onChange).toHaveBeenCalledWith("x");
  });
});

describe("FolderPicker: in-app fallback browser", () => {
  async function openFallback(value = "C:/x") {
    // returns the render result too, so a test can unmount between openings
    routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": (_init, url) => {
        const p = url.searchParams.get("path");
        if (p === "C:/x") return jsonResponse(listing("C:/x", "C:/", [{ name: "sub", path: "C:/x/sub" }]));
        if (p === "C:/x/sub") return jsonResponse(listing("C:/x/sub", "C:/x", []));
        return jsonResponse(listing(null, null, [{ name: "C:", path: "C:/" }]));
      },
    });
    const onChange = vi.fn();
    const view = render(<FolderPicker value={value} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    await screen.findByText("Choose a folder");
    return { onChange, view };
  }

  it("opens when the native dialog is unavailable and lists the start folder", async () => {
    await openFallback();
    expect(await screen.findByText("sub")).toBeInTheDocument();
    expect(screen.getByText("C:/x")).toBeInTheDocument();
  });

  it("navigates into a folder by clicking, and up again", async () => {
    const { calls } = routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": (_init, url) => {
        const p = url.searchParams.get("path");
        if (p === "C:/x/sub") return jsonResponse(listing("C:/x/sub", "C:/x", []));
        return jsonResponse(listing("C:/x", "C:/", [{ name: "sub", path: "C:/x/sub" }]));
      },
    });
    render(<FolderPicker value="C:/x" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    await userEvent.click(await screen.findByText("sub"));
    expect(await screen.findByText("No sub-folders.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "↑ Up" }));
    await waitFor(() => expect(screen.getByText("sub")).toBeInTheDocument());
    const listed = calls.filter((c) => c.method === "GET").map((c) => c.url.searchParams.get("path"));
    expect(listed).toEqual(["C:/x", "C:/x/sub", "C:/x"]);
  });

  it("navigates into a folder by double-clicking it", async () => {
    const { calls } = routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": (_init, url) => {
        const p = url.searchParams.get("path");
        return jsonResponse(p === "C:/x/sub" ? listing("C:/x/sub", "C:/x", []) : listing("C:/x", "C:/", [{ name: "sub", path: "C:/x/sub" }]));
      },
    });
    render(<FolderPicker value="C:/x" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    fireEvent.doubleClick(await screen.findByText("sub"));
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("path") === "C:/x/sub")).toBe(true));
  });

  it("disables Up at the top level and Use when there is no folder", async () => {
    routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": () => jsonResponse(listing(null, null, [{ name: "C:", path: "C:/" }])),
    });
    render(<FolderPicker value="" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(await screen.findByText("C:")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "↑ Up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use this folder" })).toBeDisabled();
    expect(screen.getByText("This PC", { selector: "code" })).toBeInTheDocument();
  });

  it("returns to the drive list from the This PC button", async () => {
    const { calls } = routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": (_init, url) => jsonResponse(url.searchParams.get("path") ? listing("C:/x", "C:/", []) : listing(null, null, [])),
    });
    render(<FolderPicker value="C:/x" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    await screen.findByText("No sub-folders.");
    await userEvent.click(screen.getByRole("button", { name: "This PC" }));
    await waitFor(() => expect(calls[calls.length - 1].url.search).toBe(""));
  });

  it("chooses the folder it is showing and closes", async () => {
    const { onChange } = await openFallback();
    await screen.findByText("sub");
    await userEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    expect(onChange).toHaveBeenCalledWith("C:/x");
    expect(screen.queryByText("Choose a folder")).toBeNull();
  });

  it("closes without choosing via Cancel and via the backdrop", async () => {
    const first = await openFallback();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Choose a folder")).toBeNull();
    first.view.unmount();
    await openFallback();
    fireEvent.click(document.querySelector(".backdrop")!);
    expect(screen.queryByText("Choose a folder")).toBeNull();
  });

  it("does not close when clicking inside the dialog", async () => {
    await openFallback();
    fireEvent.click(screen.getByText("Choose a folder"));
    expect(screen.getByText("Choose a folder")).toBeInTheDocument();
  });

  it("shows an error when a folder cannot be listed", async () => {
    routeFetch({
      "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }),
      "GET /api/fs/dirs": () => textResponse("denied", 403),
    });
    render(<FolderPicker value="C:/locked" onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(await screen.findByText("HTTP 403")).toHaveClass("err");
  });
});
