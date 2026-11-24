import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Markdown, { DocNavContext } from "@/components/Markdown";
import DocsApp from "@/components/docs/DocsApp";
import DocsPanel from "@/components/docs/DocsPanel";
import { Blocks, DocBody, DocSearch, DocTree, groupBySection, hrefOf, neighbours, Results } from "@/components/docs/DocContent";
import DocsIndexPage from "@/app/docs/page";
import DocsSlugPage, { generateStaticParams } from "@/app/docs/[slug]/page";
import { DOC_PAGES, DOC_SHOTS } from "@/lib/docs/generated";
import { searchDocs } from "@/lib/docs/search";

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push }), usePathname: () => "/docs/" }));

beforeEach(() => { nav.push.mockReset(); window.history.replaceState({}, "", "/docs/"); });

describe("Blocks and DocBody", () => {
  it("draws a screenshot line as a figure with its caption, and the rest as Markdown", () => {
    const shot = DOC_SHOTS[0];
    render(<Blocks md={`Hello **there**\n\n![A caption](shot:${shot.id})\n\nAfter`} />);
    expect(screen.getByText("there")).toBeInTheDocument();
    const fig = screen.getByTestId(`doc-shot-${shot.id}`);
    expect(within(fig).getByRole("img")).toHaveAttribute("src", `/docs/shots/${shot.file}`);
    expect(within(fig).getByText("A caption", { selector: "figcaption" })).toBeInTheDocument();
    expect(screen.getByText("After")).toBeInTheDocument();
  });

  it("uses the manifest caption when the line has none, marks phone shots, and tells you when a file is missing", () => {
    const phone = DOC_SHOTS.find((s) => s.mobile)!;
    render(<Blocks md={`![](shot:${phone.id})`} />);
    const fig = screen.getByTestId(`doc-shot-${phone.id}`);
    expect(fig).toHaveClass("phone");
    expect(within(fig).getByText(phone.alt, { selector: "figcaption" })).toBeInTheDocument();
    fireEvent.error(within(fig).getByRole("img"));
    expect(within(fig).getByText(/Screenshot not captured yet/)).toBeInTheDocument();
  });

  it("draws nothing for an unknown screenshot and does not treat an image line inside a code fence as a figure", () => {
    const { container } = render(<Blocks md={"![x](shot:does-not-exist)\n\n```\n![y](shot:chat-home)\n```"} />);
    expect(container.querySelector("figure")).toBeNull();
    expect(screen.getByText(/!\[y\]\(shot:chat-home\)/)).toBeInTheDocument();
  });

  it("shows a page with its title, summary, intro, headings with addresses and working links", async () => {
    const onNav = vi.fn();
    const p = DOC_PAGES.find((x) => x.slug === "agents")!;
    render(<DocBody page={p} onNav={onNav} />);
    expect(screen.getByRole("heading", { level: 1, name: p.title })).toBeInTheDocument();
    expect(screen.getByText(p.summary)).toBeInTheDocument();
    const h = screen.getByRole("heading", { level: 2, name: /Sub-agents/ });
    expect(h).toHaveAttribute("id", "sub-agents");
    await userEvent.click(within(h).getByRole("link", { name: /Link to Sub-agents/ }));
    expect(onNav).toHaveBeenCalledWith("doc:agents#sub-agents");
    await userEvent.click(screen.getAllByRole("link", { name: "Agent editor" })[0]);
    expect(onNav).toHaveBeenLastCalledWith("doc:agent-editor");
  });

  it("keeps a modified click for the browser, so a link can open in a new tab", async () => {
    const onNav = vi.fn();
    render(<DocNavContext.Provider value={onNav}><Markdown text="See [the editor](doc:agent-editor#live-profile) and [settings](app:/settings/#assistant) and [top](#x) and [web](https://example.com)." /></DocNavContext.Provider>);
    const a = screen.getByRole("link", { name: "the editor" });
    expect(a).toHaveAttribute("href", "/docs/agent-editor/#live-profile");
    fireEvent.click(a, { ctrlKey: true });
    expect(onNav).not.toHaveBeenCalled();
    await userEvent.click(a);
    expect(onNav).toHaveBeenCalledWith("doc:agent-editor#live-profile");
    expect(screen.getByRole("link", { name: "settings" })).toHaveAttribute("href", "/settings/#assistant");
    expect(screen.getByRole("link", { name: "web" })).toHaveAttribute("target", "_blank");
  });

  it("does not make internal links in ordinary chat answers", () => {
    render(<Markdown text="See [the editor](doc:agent-editor) and [a page](/agents/)." />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/the editor/)).toBeInTheDocument();
  });

  it("works out where a doc link leads", () => {
    expect(hrefOf("doc:agents#x")).toBe("/docs/agents/#x");
    expect(hrefOf("app:/mcp/")).toBe("/mcp/");
    expect(hrefOf("#top")).toBeNull();
  });

  it("lists the sections in order and the neighbours of a page", () => {
    const groups = groupBySection();
    expect(groups.map(([s]) => s)).toEqual(["Start here", "Work", "Build", "Observe and protect", "How-to", "Reference"]);
    expect(groups.flatMap(([, p]) => p)).toHaveLength(DOC_PAGES.length);
    expect(neighbours(DOC_PAGES[0].slug)).toEqual({ prev: undefined, next: DOC_PAGES[1] });
    expect(neighbours("nope")).toEqual({});
  });
});

describe("DocTree, Results and DocSearch", () => {
  it("opens a page from the tree and leaves modified clicks to the browser", async () => {
    const onOpen = vi.fn();
    render(<DocTree current="chat" onOpen={onOpen} />);
    expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("link", { name: "Canvas" }));
    expect(onOpen).toHaveBeenCalledWith("canvas");
    fireEvent.click(screen.getByRole("link", { name: "Runs" }), { metaKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("shows results with the matched words marked, and says when there are none", async () => {
    const onOpen = vi.fn();
    const hits = searchDocs(DOC_PAGES, "avatar cost", 4);
    const { container, rerender } = render(<Results hits={hits} onOpen={onOpen} />);
    expect(container.querySelector("mark")).not.toBeNull();
    await userEvent.click(screen.getAllByRole("link")[0]);
    expect(onOpen).toHaveBeenCalledWith(hits[0].slug, hits[0].anchor);
    fireEvent.click(screen.getAllByRole("link")[0], { shiftKey: true });
    expect(onOpen).toHaveBeenCalledTimes(1);
    rerender(<Results hits={[]} onOpen={onOpen} />);
    expect(screen.getByTestId("doc-no-results")).toBeInTheDocument();
    rerender(<Results hits={[{ ...hits[0], terms: ["a", "(*"] }]} onOpen={onOpen} />);          // short words and regex characters are harmless
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("searches as you type and clears when the box is empty", async () => {
    const onQuery = vi.fn();
    const { rerender } = render(<DocSearch query="" onQuery={onQuery} onOpen={vi.fn()} />);
    expect(screen.queryByTestId("doc-results")).toBeNull();
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the documentation" }), "w");
    expect(onQuery).toHaveBeenCalledWith("w");
    rerender(<DocSearch query="webhook" onQuery={onQuery} onOpen={vi.fn()} autoFocus />);
    expect(screen.getByTestId("doc-results")).toBeInTheDocument();
    rerender(<DocSearch query="   " onQuery={onQuery} onOpen={vi.fn()} />);
    expect(screen.queryByTestId("doc-results")).toBeNull();
  });
});

describe("DocsApp (the full page)", () => {
  it("shows the index with every section and opens a page from a card", async () => {
    render(<DocsApp />);
    expect(screen.getByRole("heading", { level: 1, name: "Documentation" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Start here", "Work", "Build", "Observe and protect", "How-to", "Reference"]);
    await userEvent.click(screen.getAllByRole("link", { name: /Live mode/ }).find((a) => a.classList.contains("doc-card"))!);
    expect(nav.push).toHaveBeenCalledWith("/docs/live-mode/");
    fireEvent.click(screen.getAllByRole("link", { name: /Canvas/ }).find((a) => a.classList.contains("doc-card"))!, { ctrlKey: true });
    expect(nav.push).toHaveBeenCalledTimes(1);
  });

  it("shows a page with the tree, an on-this-page list, previous and next, and follows links inside it", async () => {
    render(<DocsApp slug="agents" />);
    expect(screen.getByTestId("doc-agents")).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "On this page" })).getAllByRole("link").length).toBeGreaterThan(3);
    expect(screen.getByRole("navigation", { name: "Documentation pages" })).toBeInTheDocument();
    const i = DOC_PAGES.findIndex((p) => p.slug === "agents");
    await userEvent.click(screen.getByRole("link", { name: new RegExp(`← ${DOC_PAGES[i - 1].title}`) }));
    expect(nav.push).toHaveBeenCalledWith(`/docs/${DOC_PAGES[i - 1].slug}/`);
    await userEvent.click(screen.getByRole("link", { name: new RegExp(`${DOC_PAGES[i + 1].title} →`) }));
    expect(nav.push).toHaveBeenCalledWith(`/docs/${DOC_PAGES[i + 1].slug}/`);
    await userEvent.click(screen.getAllByRole("link", { name: "Agent editor" })[0]);
    expect(nav.push).toHaveBeenLastCalledWith("/docs/agent-editor/");
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(nav.push).toHaveBeenLastCalledWith("/docs/chat/");
  });

  it("starts with the list of pages open on a wide screen and folded on a phone, and the person can toggle it", () => {
    const { unmount } = render(<DocsApp slug="chat" />);
    expect(document.querySelector("details.docs-tree-wrap")).toHaveAttribute("open");
    unmount();
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("820"), media: q, addEventListener() {}, removeEventListener() {} }));
    render(<DocsApp slug="chat" />);
    const d = document.querySelector("details.docs-tree-wrap") as HTMLDetailsElement;
    expect(d.open).toBe(false);
    d.open = true;
    fireEvent(d, new Event("toggle"));
    expect(d.open).toBe(true);
  });

  it("has no previous link on the first page and no next link on the last", () => {
    const { unmount } = render(<DocsApp slug={DOC_PAGES[0].slug} />);
    expect(screen.queryByText(/^←/)).toBeNull();
    unmount();
    render(<DocsApp slug={DOC_PAGES.at(-1)!.slug} />);
    expect(screen.queryByText(/→$/)).toBeNull();
  });

  it("says so when a page does not exist", () => {
    render(<DocsApp slug="nope" />);
    expect(screen.getByTestId("doc-missing")).toHaveTextContent("There is no documentation page called nope");
  });

  it("searches and opens a result at its heading", async () => {
    render(<DocsApp slug="chat" />);
    await userEvent.type(screen.getByRole("searchbox"), "webhook address");
    const first = within(screen.getByTestId("doc-results")).getAllByRole("link")[0];
    await userEvent.click(first);
    expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/docs\/[\w-]+\/(#[\w-]+)?$/));
  });

  it("scrolls to the heading named in the address, and when only the heading changes", async () => {
    const spy = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
    window.history.replaceState({}, "", "/docs/agents/#sub-agents");
    render(<DocsApp slug="agents" />);
    await act(async () => { await new Promise((r) => setTimeout(r, 60)); });
    expect(spy).toHaveBeenCalledTimes(1);
    expect((spy.mock.contexts[0] as HTMLElement).id).toBe("sub-agents");
    window.history.replaceState({}, "", "/docs/agents/#the-list");
    await act(async () => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect((spy.mock.contexts.at(-1) as HTMLElement).id).toBe("the-list");
    window.history.replaceState({}, "", "/docs/agents/#missing-heading");
    await act(async () => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
    window.history.replaceState({}, "", "/docs/agents/");
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    await act(async () => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(scrollTo).toHaveBeenCalled();
  });
});

describe("the page files", () => {
  it("lists every documentation page for the static export and renders the index and a page", async () => {
    expect(generateStaticParams()).toEqual(DOC_PAGES.map((p) => ({ slug: p.slug })));
    render(<DocsIndexPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Documentation" })).toBeInTheDocument();
    const el = await DocsSlugPage({ params: Promise.resolve({ slug: "canvas" }) });
    render(el);
    expect(screen.getByTestId("doc-canvas")).toBeInTheDocument();
  });
});

describe("DocsPanel (beside the screen)", () => {
  const props = () => ({ onApp: vi.fn(), onFull: vi.fn() });

  it("starts with help for the screen you are on and every page grouped by section", async () => {
    render(<DocsPanel route="/mcp/?server=x" request={null} {...props()} />);
    const here = screen.getByTestId("docs-here");
    expect(here).toHaveTextContent("Help for this screen");
    expect(here).toHaveTextContent("MCP servers");
    await userEvent.click(here);
    expect(screen.getByTestId("doc-mcp-servers")).toBeInTheDocument();
  });

  it("shows no 'for this screen' card on a screen without a page, and still lists pages", async () => {
    render(<DocsPanel route="/nowhere/" request={null} {...props()} />);
    expect(screen.queryByTestId("docs-here")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Glossary" }));
    expect(screen.getByTestId("doc-glossary")).toBeInTheDocument();
  });

  it("opens the page the assistant asks for, at the heading, and goes back to the list", async () => {
    const spy = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
    const { rerender } = render(<DocsPanel route="/" request={null} {...props()} />);
    rerender(<DocsPanel route="/" request={{ slug: "agent-editor", anchor: "live-profile", n: 1 }} {...props()} />);
    expect(screen.getByTestId("doc-agent-editor")).toBeInTheDocument();
    await act(async () => { await new Promise((r) => setTimeout(r, 60)); });
    expect((spy.mock.contexts.at(-1) as HTMLElement).id).toBe("live-profile");
    rerender(<DocsPanel route="/" request={{ slug: "nope", n: 2 }} {...props()} />);          // an unknown page changes nothing
    expect(screen.getByTestId("doc-agent-editor")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "← All pages" }));
    expect(screen.queryByTestId("doc-agent-editor")).toBeNull();
    expect(screen.getByRole("button", { name: "Billing and spend limits" })).toBeInTheDocument();
  });

  it("scrolls to the top when a page opens without a heading", async () => {
    const top = vi.fn();
    Element.prototype.scrollTo = top as never;
    render(<DocsPanel route="/" request={{ slug: "chat", n: 1 }} {...props()} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 60)); });
    expect(top).toHaveBeenCalled();
  });

  it("follows doc links inside the panel, sends app links to the router, and can open the full page", async () => {
    const p = props();
    render(<DocsPanel route="/" request={{ slug: "agents", n: 1 }} {...p} />);
    await userEvent.click(screen.getAllByRole("link", { name: "Agent editor" })[0]);
    expect(screen.getByTestId("doc-agent-editor")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("link", { name: "Security" })[0]);
    expect(screen.getByTestId("doc-security")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("link", { name: "Red team" })[0]);
    expect(screen.getByTestId("doc-red-team")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open full page" }));
    expect(p.onFull).toHaveBeenCalledWith("/docs/red-team/");
  });

  it("sends a link to an app screen to the router and keeps the panel", async () => {
    const p = props();
    render(<DocsPanel route="/" request={{ slug: "chat", n: 1 }} {...p} />);
    const doc = screen.getByTestId("doc-chat");
    fireEvent.click(within(doc).getAllByRole("link", { name: /Settings, Auto/ })[0]);
    expect(screen.getByTestId("doc-settings")).toBeInTheDocument();                     // a doc link switches pages
    render(<DocsPanel route="/" request={{ slug: "live-mode", n: 1 }} {...p} />);
    const a = screen.getAllByRole("link").find((l) => l.getAttribute("href")?.startsWith("/settings/"));
    if (a) { fireEvent.click(a); expect(p.onApp).toHaveBeenCalledWith(expect.stringMatching(/^\/settings\//)); }
  });

  it("opens the full page at the heading it is on, searches, and opens a hit", async () => {
    const p = props();
    render(<DocsPanel route="/" request={{ slug: "agents", anchor: "sub-agents", n: 1 }} {...p} />);
    await userEvent.click(screen.getByRole("button", { name: "Open full page" }));
    expect(p.onFull).toHaveBeenCalledWith("/docs/agents/#sub-agents");
    await userEvent.type(screen.getByRole("searchbox"), "latency");
    expect(screen.queryByTestId("doc-agents")).toBeNull();                              // results replace the page while you search
    await userEvent.click(within(screen.getByTestId("doc-results")).getAllByRole("link")[0]);
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(screen.getByTestId("doc-live-mode")).toBeInTheDocument();
  });
});
