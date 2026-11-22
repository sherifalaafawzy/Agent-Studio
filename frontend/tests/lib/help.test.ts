import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { HELP, helpIds } from "@/lib/help";

const root = join(__dirname, "..", "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (n === "node_modules" || n.startsWith(".")) return [];
    return statSync(p).isDirectory() ? sources(p) : /\.tsx$/.test(n) ? [p] : [];
  });
}

describe("help texts", () => {
  it("every entry is a real explanation written for a person", () => {
    expect(helpIds.length).toBeGreaterThan(25);
    for (const id of helpIds) {
      const h = HELP[id];
      expect(h.title.length, id).toBeGreaterThan(2);
      expect(h.body.length, id).toBeGreaterThan(60);
      expect(h.body.length, id).toBeLessThan(420);                          // short enough to read in a tooltip
      if (h.link) { expect(h.link.href, id).toMatch(/^\/[a-z-]*\/?(#[a-z-]+)?$/); expect(h.link.label.length).toBeGreaterThan(2); }
    }
  });

  it("every tip used anywhere in the app has text (no dead icons)", () => {
    const used = new Set<string>();
    for (const dir of ["app", "components"]) for (const f of sources(join(root, dir))) {
      for (const m of readFileSync(f, "utf8").matchAll(/<InfoTip id="([a-z0-9-]+)"/g)) used.add(m[1]);
    }
    expect(used.size).toBeGreaterThan(15);
    expect([...used].filter((id) => !HELP[id])).toEqual([]);
  });
});
