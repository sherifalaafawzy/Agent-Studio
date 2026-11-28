// A small square tile that identifies a service: its brand mark when one is bundled, a plain glyph for built-in tools
// ("Filesystem", "Memory"…), or the name's initials. Every tile has the same size and neutral colours, so rows line up and
// both themes work without per-brand contrast checks. The tile is decoration: it always sits beside the visible name.
import { BRAND_PATHS } from "@/lib/brandIcons";

/** id or word in the name → slug in BRAND_PATHS. Keys are lower-case with only letters and digits. */
const BRAND: Record<string, string> = {
  git: "git", elevenlabs: "elevenlabs", github: "github", brave: "brave", bravesearch: "brave", docker: "docker", codesandbox: "docker", eslint: "eslint", snyk: "snyk",
  xero: "xero", hubspot: "hubspot", airtable: "airtable", notion: "notion", shopify: "shopify", shopifydev: "shopify", googleanalytics: "googleanalytics", figma: "figma",
  freecad: "freecad", opentofu: "opentofu", kubernetes: "kubernetes", k8s: "kubernetes", chromedevtools: "googlechrome", chrome: "googlechrome", duckdb: "duckdb", motherduck: "duckdb",
  postgres: "postgresql", postgresql: "postgresql", clickhouse: "clickhouse", mongodb: "mongodb", mongo: "mongodb", neo4j: "neo4j", sqlite: "sqlite", qdrant: "qdrant",
  huggingface: "huggingface", kaggle: "kaggle", mlflow: "mlflow", arxiv: "arxiv",
  gemini: "googlegemini", vertexai: "googlecloud", vertex: "googlecloud", gcp: "googlecloud", googlecloud: "googlecloud", google: "google", adk: "google", antigravity: "google",
  anthropic: "anthropic", claude: "claude", claudesdk: "claude", claudecode: "claude", openrouter: "openrouter", ollama: "ollama", mistral: "mistralai", mistralai: "mistralai",
  cursor: "cursor", nvidia: "nvidia", garak: "nvidia", digitalocean: "digitalocean", pytorch: "pytorch", keras: "keras", langgraph: "langgraph", langchain: "langgraph",
  pydantic: "pydantic", pydanticai: "pydantic", deepseek: "deepseek", perplexity: "perplexity",
  redis: "redis", elasticsearch: "elasticsearch", elastic: "elasticsearch", supabase: "supabase", firebase: "firebase", jira: "jira", confluence: "confluence", gitlab: "gitlab",
  bitbucket: "bitbucket", discord: "discord", telegram: "telegram", zapier: "zapier", asana: "asana", trello: "trello", dropbox: "dropbox", googlesheets: "googlesheets",
  sheets: "googlesheets", googlecalendar: "googlecalendar", calendar: "googlecalendar", googledrive: "googledrive", gdrive: "googledrive", drive: "googledrive", gmail: "gmail",
  obsidian: "obsidian", puppeteer: "puppeteer", snowflake: "snowflake", databricks: "databricks", mysql: "mysql", terraform: "terraform", grafana: "grafana", sentry: "sentry",
  stripe: "stripe", linear: "linear", cloudflare: "cloudflare", vercel: "vercel", wikipedia: "wikipedia", reddit: "reddit", duckduckgo: "duckduckgo", jupyter: "jupyter",
  python: "python", nodejs: "nodedotjs", node: "nodedotjs", npm: "npm",
};

/** Plain glyphs (stroke, 24 grid, the app's own icon style) for things that are not a brand. */
const GLYPH: Record<string, string> = {
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z",
  brain: "M9 4.5a3 3 0 0 0-3 3v.2A3 3 0 0 0 4.5 13a3 3 0 0 0 1.6 2.6A3 3 0 0 0 9 19.5a3 3 0 0 0 3-3v-12a3 3 0 0 0-3-3ZM15 4.5a3 3 0 0 1 3 3v.2a3 3 0 0 1 1.5 5.3 3 3 0 0 1-1.6 2.6 3 3 0 0 1-3.4 3.4 3 3 0 0 1-3-3",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM10 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z",
  chart: "M4 19V5M4 19h16M8 15l3.5-4 3 2.5L19 8",
  flask: "M9 3h6M10 3v6.5L4.6 18.2A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-2.8L14 9.5V3M7.5 15h9",
  cloud: "M7 18.5a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17.2 9.2 4.7 4.7 0 0 1 17 18.5H7Z",
  shield: "M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3ZM9 12l2.2 2.2L15 10.2",
  browser: "M5 4.5h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2ZM3 9h18M6.5 6.8h.01M9.5 6.8h.01",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5ZM4 19a2 2 0 0 1 2-2h13M9 7h6",
  search: "M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM20 20l-4-4",
  database: "M5 6c0-1.66 3.13-3 7-3s7 1.34 7 3-3.13 3-7 3-7-1.34-7-3ZM5 6v12c0 1.66 3.13 3 7 3s7-1.34 7-3V6M5 12c0 1.66 3.13 3 7 3s7-1.34 7-3",
  table: "M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM3 9.5h18M3 15h18M9 4v16",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7.5V12l3 2",
  calculator: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2ZM8.5 7h7M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01M8.5 15h.01M12 15h.01M15.5 15h.01M8.5 18h.01M12 18h3.5",
  server: "M5 4h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM5 13h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2ZM7 7.5h.01M7 16.5h.01",
  monitor: "M5 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM8 20h8M12 16v4",
  target: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 11.9v.2",
  cube: "M12 3 4 7.5v9L12 21l8-4.5v-9L12 3ZM4 7.5l8 4.5 8-4.5M12 12v9",
};

/** id → glyph, for the tools that come with the app and other things with no brand of their own. */
const PLAIN: Record<string, string> = {
  filesystem: "folder", fetch: "globe", memory: "brain", d1: "sliders", timeseries: "chart", trainer: "flask", cloudml: "cloud", semgrep: "shield", playwright: "browser",
  context7: "book", awsbilling: "cloud", awspricing: "cloud", awsdocs: "cloud", awsapi: "cloud",
  fred: "chart", excel: "table", time: "clock", calculator: "calculator", tavily: "search", pinecone: "database", papersearch: "book",
  mock: "flask", local: "monitor", promptfoo: "target", pyrit: "target", custom: "server",
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

export type Mark = { kind: "brand"; slug: string } | { kind: "glyph"; name: string } | { kind: "initials"; text: string };

/** Which mark a thing gets. The id decides first; then the whole name; then any single word of the name ("Brave Search" → Brave). Pure, so it can be tested. */
export function markFor(id: string | undefined, label: string | undefined): Mark {
  const keys = [norm(id ?? ""), norm(label ?? ""), norm((label ?? "").replace(/\(.*?\)/g, "")), ...(label ?? "").split(/[^A-Za-z0-9]+/).map(norm)].filter(Boolean);
  for (const k of keys.slice(0, 3)) {
    if (BRAND[k]) return { kind: "brand", slug: BRAND[k] };
    if (PLAIN[k]) return { kind: "glyph", name: PLAIN[k] };
  }
  for (const k of keys.slice(3)) if (k.length > 2 && BRAND[k]) return { kind: "brand", slug: BRAND[k] };
  const two = (s: string) => s.replace(/\(.*?\)/g, "").replace(/[^A-Za-z0-9]/g, "").slice(0, 2);
  const text = two(label ?? "") || two(id ?? "") || "?";
  return { kind: "initials", text };
}

/** The tile. `size` is the tile's side in pixels; the mark inside is 56% of it. */
export default function BrandIcon({ id, label, size = 32, className = "" }: { id?: string; label?: string; size?: number; className?: string }) {
  const m = markFor(id, label);
  const inner = Math.round(size * 0.56);
  const b = m.kind === "brand" ? BRAND_PATHS[m.slug] : null;
  // the brand's own colour only where it is readable on the tile in that theme (data-l / data-d); the stylesheet falls back to the text colour
  const style = { width: size, height: size, fontSize: Math.max(12, Math.round(size * 0.38)), ...(b ? { "--brand": b.hex } : {}) } as React.CSSProperties;
  return (
    <span className={`brand-tile ${className}`} data-mark={m.kind === "brand" ? m.slug : m.kind === "glyph" ? m.name : "initials"} data-l={b?.light ? "" : undefined} data-d={b?.dark ? "" : undefined} style={style} aria-hidden="true">
      {m.kind === "brand" ? <svg width={inner} height={inner} viewBox="0 0 24 24" fill="currentColor" focusable="false"><path d={BRAND_PATHS[m.slug].d} /></svg>
        : m.kind === "glyph" ? <svg width={inner + 2} height={inner + 2} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" focusable="false"><path d={GLYPH[m.name]} /></svg>
          : m.text}
    </span>
  );
}
