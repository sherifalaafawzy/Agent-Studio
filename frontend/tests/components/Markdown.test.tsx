import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Markdown from "@/components/Markdown";

const md = (text: string) => render(<Markdown text={text} />).container;

describe("Markdown: blocks", () => {
  it("renders nothing for empty input", () => {
    const c = md("");
    expect(c.querySelector(".md")).toBeInTheDocument();
    expect(c.querySelector(".md")?.children).toHaveLength(0);
  });

  it("renders a paragraph with inline bold", () => {
    md("Hello **world**");
    expect(screen.getByText("world").tagName).toBe("STRONG");
    expect(screen.getByText(/Hello/).tagName).toBe("P");
  });

  it("keeps soft line breaks inside a paragraph as <br>", () => {
    const c = md("first line\nsecond line");
    expect(c.querySelector("p br")).not.toBeNull();
    expect(c.querySelector("p")?.textContent).toBe("first linesecond line");
  });

  it("normalises CRLF line endings", () => {
    const c = md("a\r\nb");
    expect(c.querySelectorAll("p")).toHaveLength(1);
    expect(c.querySelector("p br")).not.toBeNull();
  });

  it("renders headings levels 1 to 4, but treats five hashes as text", () => {
    const c = md("# H1\n\n## H2\n\n### H3\n\n#### H4\n\n##### H5");
    expect(c.querySelector("h1")?.textContent).toBe("H1");
    expect(c.querySelector("h2")?.textContent).toBe("H2");
    expect(c.querySelector("h3")?.textContent).toBe("H3");
    expect(c.querySelector("h4")?.textContent).toBe("H4");
    expect(c.querySelector("h5")).toBeNull();
    expect(c.querySelector("p")?.textContent).toBe("##### H5");
  });

  it("applies inline formatting inside headings", () => {
    const c = md("# Title *emphasis*");
    expect(c.querySelector("h1 em")?.textContent).toBe("emphasis");
  });

  it("renders horizontal rules for --- and ***", () => {
    expect(md("---").querySelector("hr")).not.toBeNull();
    expect(md("***").querySelector("hr")).not.toBeNull();
  });

  it("renders a blockquote whose content is itself Markdown", () => {
    const c = md("> quoted **bold**\n> more");
    const bq = c.querySelector("blockquote");
    expect(bq?.querySelector("strong")?.textContent).toBe("bold");
    expect(bq?.textContent).toContain("more");
  });

  it("renders nested blockquotes", () => {
    const c = md("> outer\n> > inner");
    expect(c.querySelector("blockquote blockquote")?.textContent).toBe("inner");
  });

  it("ends one blockquote at a plain line and starts another", () => {
    const c = md("> a\n\n> b");
    expect(c.querySelectorAll(":scope > .md > blockquote")).toHaveLength(2);
  });
});

describe("Markdown: fenced code", () => {
  it("shows the language and the code", () => {
    const c = md("```ts\nconst a = 1;\nlet b = 2;\n```");
    expect(screen.getByText("ts")).toBeInTheDocument();
    expect(c.querySelector("pre code")?.textContent).toBe("const a = 1;\nlet b = 2;");
  });

  it("labels code without a language as text, and accepts ~~~ fences", () => {
    md("~~~\nplain\n~~~");
    expect(screen.getByText("text")).toBeInTheDocument();
    expect(screen.getByText("plain")).toBeInTheDocument();
  });

  it("treats an unterminated fence as code running to the end", () => {
    const c = md("```\nline one\nline two");
    expect(c.querySelector("pre code")?.textContent).toBe("line one\nline two");
  });

  it("does not treat a fence line with trailing words as a code fence", () => {
    const c = md("```js extra\nx");
    expect(c.querySelector("pre")).toBeNull();
    expect(c.textContent).toContain("```js extra");
  });

  it("copies the code to the clipboard and swaps the icon briefly", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    md("```\nsecret()\n```");
    const btn = screen.getByTitle("Copy");
    const before = btn.innerHTML;
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(btn); });
      expect(writeText).toHaveBeenCalledWith("secret()");
      expect(screen.getByTitle("Copy").innerHTML).not.toBe(before);
      act(() => { vi.advanceTimersByTime(1200); });
      expect(screen.getByTitle("Copy").innerHTML).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does nothing when the clipboard API is unavailable", () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    md("```\nx\n```");
    expect(() => fireEvent.click(screen.getByTitle("Copy"))).not.toThrow();
    expect(screen.getByTitle("Copy").innerHTML).toContain("path");
  });
});

describe("Markdown: lists", () => {
  it("renders bulleted items for -, * and +", () => {
    const c = md("- one\n- two");
    expect(c.querySelectorAll("ul > li")).toHaveLength(2);
    expect(md("* a\n* b").querySelectorAll("ul > li")).toHaveLength(2);
    expect(md("+ a\n+ b").querySelectorAll("ul > li")).toHaveLength(2);
  });

  it("renders numbered items as an ordered list", () => {
    const c = md("1. first\n2) second");
    expect(c.querySelectorAll("ol > li")).toHaveLength(2);
    expect(c.querySelector("ol > li")?.textContent).toBe("first");
  });

  it("starts a new list when the list type changes", () => {
    const c = md("1. a\n- b");
    expect(c.querySelector("ol > li")?.textContent).toBe("a");
    expect(c.querySelector("ul > li")?.textContent).toBe("b");
  });

  it("folds an indented continuation line into the item", () => {
    const c = md("- item\n  more text");
    expect(c.querySelector("li br")).not.toBeNull();
    expect(c.querySelector("li")?.textContent).toBe("itemmore text");
  });

  it("keeps a deeper bullet as continuation text rather than a sibling item", () => {
    const c = md("- a\n  - b");
    expect(c.querySelectorAll("li")).toHaveLength(1);
    expect(c.querySelector("li")?.textContent).toBe("a- b");
  });

  it("ends a list when a paragraph starts", () => {
    const c = md("Intro\n- item");
    expect(c.querySelector("p")?.textContent).toBe("Intro");
    expect(c.querySelector("ul > li")?.textContent).toBe("item");
  });
});

describe("Markdown: tables", () => {
  it("renders header and body cells with inline formatting", () => {
    const c = md("| Name | Score |\n| :--- | ---: |\n| **Ann** | `9` |\n| Bo | 7 |");
    expect(c.querySelectorAll("th")).toHaveLength(2);
    expect(c.querySelector("tbody td strong")?.textContent).toBe("Ann");
    expect(c.querySelector("tbody td code")?.textContent).toBe("9");
    expect(c.querySelectorAll("tbody tr")).toHaveLength(2);
  });

  it("renders a header-only table", () => {
    const c = md("| A | B |\n|---|---|");
    expect(c.querySelectorAll("th")).toHaveLength(2);
    expect(c.querySelectorAll("tbody tr")).toHaveLength(0);
  });

  it("does not treat a pipe line without a separator row as a table", () => {
    const c = md("a | b");
    expect(c.querySelector("table")).toBeNull();
    expect(c.querySelector("p")?.textContent).toBe("a | b");
  });
});

describe("Markdown: inline", () => {
  it("renders inline code without interpreting its contents", () => {
    const c = md("use `**not bold**` here");
    expect(c.querySelector("code")?.textContent).toBe("**not bold**");
    expect(c.querySelector("strong")).toBeNull();
  });

  it("renders _underscore_ and __double__ emphasis at word boundaries", () => {
    const c = md("an _em_ and a __strong__ word");
    expect(c.querySelector("em")?.textContent).toBe("em");
    expect(c.querySelector("strong")?.textContent).toBe("strong");
  });

  it("renders *single star* italics", () => {
    expect(md("*lean*").querySelector("em")?.textContent).toBe("lean");
  });

  it("keeps snake_case identifiers intact", () => {
    const c = md("call my_var_name now");
    expect(c.querySelector("em")).toBeNull();
    expect(c.textContent).toBe("call my_var_name now");
  });

  it("links http(s) and mailto URLs in a new tab", () => {
    const c = md("[site](https://example.com) and [mail](mailto:a@b.co)");
    const links = c.querySelectorAll("a");
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("https://example.com");
    expect(links[0].getAttribute("target")).toBe("_blank");
    expect(links[0].getAttribute("rel")).toBe("noreferrer noopener");
    expect(links[1].getAttribute("href")).toBe("mailto:a@b.co");
  });

  it("renders a link with formatted link text", () => {
    const c = md("[**bold link**](https://x.test)");
    expect(c.querySelector("a strong")?.textContent).toBe("bold link");
  });

  it("refuses unsafe link schemes and keeps only the text", () => {
    const c = md("[click](javascript:alert(1))");
    expect(c.querySelector("a")).toBeNull();
    expect(c.textContent).toContain("click");
  });

  it("never injects raw HTML from the model output", () => {
    const c = md("<img src=x onerror=alert(1)> <script>alert(1)</script>");
    expect(c.querySelector("script")).toBeNull();
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toContain("<script>alert(1)</script>");
  });

  it("converts common TeX commands to symbols inside $...$", () => {
    const c = md("$17 \\times 23 = 391$ and $\\alpha + \\beta \\cdot \\pi$");
    expect(c.querySelectorAll("span.math")).toHaveLength(2);
    expect(c.querySelectorAll("span.math")[0].textContent).toBe("17 × 23 = 391");
    expect(c.querySelectorAll("span.math")[1].textContent).toBe("α + β · π");
  });

  it("converts the wider set of TeX commands", () => {
    const c = md("$\\div \\pm \\leq \\geq \\neq \\approx \\infty \\rightarrow \\Rightarrow \\sum$");
    expect(c.querySelector("span.math")?.textContent).toBe("÷ ± ≤ ≥ ≠ ≈ ∞ → ⇒ Σ");
  });

  it("converts fractions, roots, powers, text and spacing", () => {
    const c = md("$\\frac{1}{2} \\sqrt{x} x^2 y^{3} \\text{hi}\\,z$");
    expect(c.querySelector("span.math")?.textContent).toBe("(1)/(2) √(x) x² y³ hi z");
  });

  it("does not treat a dollar amount as math", () => {
    const c = md("It costs $5 and $10 total");
    expect(c.querySelector("span.math")).toBeNull();
    expect(c.textContent).toBe("It costs $5 and $10 total");
  });

  it("renders a table cell's inline content", () => {
    const c = md("| a |\n|---|\n| *x* |");
    expect(c.querySelector("td em")?.textContent).toBe("x");
  });
});

describe("Markdown: mixed block layout", () => {
  it("renders headings, paragraphs and code in sequence", () => {
    const c = md("## Plan\nFirst step.\n\n```\nrun()\n```\n\n- done");
    const tags = Array.from(c.querySelector(".md")!.children).map((el) => el.tagName.toLowerCase());
    expect(tags).toEqual(["h2", "p", "div", "ul"]);
  });

  it("keeps a line that starts a fence with extra words as paragraph text after a paragraph", () => {
    const c = md("text\n```js extra");
    expect(c.querySelectorAll("p")).toHaveLength(2);
  });
});
