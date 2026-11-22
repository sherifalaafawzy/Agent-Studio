# Agent Studio — Rich UI Components (AG-UI Kit) Plan

_Draft 2026-11-22. Covers tables, charts, time series, maps, 30+ cards, media, JSON, HTML preview, exports, agent teaching, security, tests and phasing. Library names and versions below are **candidates**: licences, current versions and the 14-day publish rule must be confirmed at build time (marked ⚑)._

Relationship to existing work: Phase 5 built the safe renderer (`RichBlocks`: Markdown, code, simple tables, bar/line charts, diffs, forms, images, file cards) and `/api/agui`. Queue item 6 (running now) adds tables with export, audio/video, JSON tree and sandboxed HTML preview. **This plan is the larger follow-on ("AG-UI Kit")** and reuses item 6's table, export and preview pieces rather than replacing them.

---

## 1. Principles (apply to every component)

1. **Components render data; they never fetch it.** An agent calls tools (weather MCP, finance MCP, geocoder…) and passes the *result* to the UI as a typed spec. The UI never calls remote APIs, loads remote images/tiles, or runs agent-supplied scripts.
2. **One typed spec per component**, versioned JSON Schema, validated on both server and client; size and depth limits; unknown or invalid specs fall back to a **table or JSON view** with an explanation, never a blank card.
3. **Every data-bearing component has a Table view** (toggle) and **Export** (see §8). Charts and maps are never the only way to read the numbers.
4. **Safe by construction:** a whitelist registry; text is escaped; links open as external with a warning; HTML only in a no-script, no-network sandboxed frame; media only from artifacts/workspace.
5. **Honest design:** follows the dataviz rules (validated palettes in light and dark, one axis — never dual axis, direct labels, legend for 2+ series, status colours always with icon and text), a11y (keyboard, names, contrast, reduced motion), RTL-ready, 375px-ready.
6. **Lazy and light:** heavy libraries load only when a block of that type appears (code-split); big datasets are down-sampled; rendering runs off the main thread where possible.
7. **Every component can be kept** (saved as an artifact), **opened in the canvas**, and **copied as image/data**.

---

## 2. Architecture

- **Spec envelope:** `{ "type": "chart.line", "version": 1, "title", "description", "data" | "dataRef", "options", "actions" }`. `dataRef` points to an artifact (CSV/JSON/GeoJSON/Parquet-lite) so large data doesn't ride the stream.
- **Registry:** `frontend/components/ui-kit/registry.ts` maps `type → {schema, Component, toTable(), exporters[], icon, a11yLabel}`. Adding a component = one folder, one schema, one story, one test.
- **Delivery paths (all produce the same spec):**
  1. fenced blocks in the answer (```` ```ui ```` + JSON) for simple cases;
  2. a `render_ui` agent tool (validated server-side, returns an error the model can fix);
  3. AG-UI custom events (`ui.component`, `ui.component.patch` via JSON Patch) so external AG-UI clients get structured data and live updates.
- **Model teaching without token bloat:** the system prompt lists only component *names and one-liners*; a `ui_catalog(type)` tool returns the schema and an example on demand. Agents are told to prefer a table when unsure.
- **Theme layer:** one token set (`--viz-*`, status colours, map styles) with validated light/dark categorical, sequential and diverging palettes (run through the palette validator); CVD-safe; optional texture fills.
- **Export framework:** each component declares exporters; shared helpers for CSV/TSV/JSON/XLSX/Markdown/PNG/SVG/PDF/GeoJSON; everything client-side, no upload.
- **Gallery:** a `/components` page (dev + docs) showing every component with sample data in light/dark/375px — doubles as the screenshot source for the Docs phase and as a visual regression target.

---

## 3. Tables

Typed columns (text, number, currency, percent, date/time, boolean, badge/status, link, image, sparkline, progress, JSON, code, geo). Sort, multi-sort, filter (per-column and global), search, column show/hide/reorder/pin, resize, density toggle, paging or virtualised rows (100k rows), row selection, group-by with subtotals, conditional formatting (data bars, colour scales, icons), expand-long-cell, copy cell/row/selection, frozen header, sticky first column on mobile, "pivot" light (group + aggregate).
Exports: CSV, TSV, JSON, XLSX, Markdown, HTML table (sandboxed), clipboard. Server-side "Keep as artifact" for large tables.

## 4. Charts (≥ 24 types)

| Family | Types |
|---|---|
| Comparison | bar (grouped, stacked, 100%, horizontal), lollipop, dot plot, bullet, slope, dumbbell |
| Trend | line, area, stacked area, step, sparkline, small multiples |
| Distribution | histogram, density, box plot, violin, strip, ECDF |
| Relationship | scatter, bubble, scatter-matrix (light), hexbin, regression/trend line, correlation heatmap |
| Part-to-whole | donut/pie (≤6 slices, with table), treemap, sunburst, waffle, stacked 100%, marimekko |
| Flow / hierarchy | sankey, funnel, waterfall, tree, network graph (force), chord, org chart |
| Matrix | heatmap, calendar heatmap, confusion matrix |
| Finance | candlestick/OHLC with volume, range band, drawdown |
| Progress | gauge, radial progress, bullet, thermometer |
| Planning | Gantt, timeline, burndown |
| Statistical/ML | ROC/PR curve, learning curves, feature importance, residuals, calibration |
| Radar | radar/spider (with caveat text) |

Cross-cutting: tooltips/crosshair, legend with toggle, direct labels, annotations (markers, bands, thresholds), zoom/brush, log scale, number/date formatting, locale and timezone, colour-by-entity stability, "show as table", "swap chart type" for compatible data, reference lines, confidence bands. **No dual axis**: two scales become small multiples or an indexed chart.
Engine candidates ⚑: keep our hand-rolled SVG for the simple core (bar/line/area, already built and palette-validated); add **Apache ECharts** (Apache-2.0, modular imports, lazy-loaded) for the advanced types. Alternative for very large single-series time data: **uPlot** (MIT). Decision rule: one advanced engine (ECharts) unless a measured need appears.

## 5. Time series (first-class)

Range selector (1h…max) and brush/zoom, pan, crosshair with synced tooltips across stacked panels, multiple series with per-series toggle, resample/aggregate (mean/sum/last/min/max), moving average, differences/percent change, indexed-to-100, log scale, timezone and DST-safe axis, gaps shown as gaps, **annotations** (events, deployments, news), **anomaly highlights**, **forecast with prediction bands** (renders output from the forecasting MCP: history + forecast + intervals + backtest overlay), seasonality view, **LTTB down-sampling** for >5k points with "showing N of M" notice, live-append mode via AG-UI deltas, candlestick mode for OHLC, small-multiples for many series, and a synchronized table view with column stats (min/max/mean/last/Δ).
Exports: CSV/JSON/XLSX of the *visible window or full data*, PNG/SVG of the chart, copy-as-image.

## 6. Maps (basic → advanced)

**Engine candidates ⚑:** MapLibre GL JS (BSD-3, WebGL vector/raster) for the base; optional **deck.gl** (MIT) lazy-loaded for arcs, hexagon/3D layers; **h3-js** (Apache-2.0) for hex binning; **Turf.js** (MIT) for geo maths (distance, buffer, bbox) in-browser.
**Basemaps (privacy):** loading tiles tells a tile server what area you look at, so the default is **no external requests**: a bundled simplified Natural Earth world/country/region outline (public domain) plus graticule. Opt-in basemaps in Settings: user-supplied raster/vector tile URL (with required attribution shown), self-hosted PMTiles file as an artifact (fully offline), or a keyed provider (key write-only). Default CSP blocks tile hosts until a basemap is enabled; the UI labels "tiles load from X — location of view is visible to X". Respect each provider's terms (e.g. no bulk use of the public OSM tile servers).

| Level | Capabilities |
|---|---|
| Basic | markers with popups/cards, auto-fit bounds, pan/zoom, scale bar, attribution, fullscreen, "locate" only from supplied coordinates |
| Layers | GeoJSON points/lines/polygons, styling by property, legend, layer toggle panel, tooltip/popup templates, clustering, search within layer |
| Thematic | **choropleth** (bundled boundaries: countries, admin-1, optionally US states/counties via artifact), proportional symbols/bubble map, **heatmap/density**, **hexbin (H3)** aggregation, dot-density, graduated colours with validated sequential/diverging ramps and classification (quantile/equal/natural breaks) |
| Movement | routes/paths with direction, **arc/flow map** (origin–destination), isochrone/polygon overlays supplied by tools, tracks with time slider and playback, speed/colour by attribute |
| Time | **animated time slider** (playback, speed, loop), time-aware layers, small-multiple maps |
| 3D/advanced | extruded polygons/columns, terrain/hillshade (if user provides tiles), pitch/bearing, globe projection, 3D arcs |
| Analysis | measure distance/area, draw shapes, buffer/within/nearest (Turf), compare two layers (swipe), side-by-side maps |
| Output | **table view of features**, export GeoJSON, CSV (with lat/lon), KML/GPX (routes), PNG/SVG snapshot, shareable static map as artifact |

Data comes from tools (geocoder, routing, weather, Overture/OSM extracts, user files). The map component never geocodes by itself.

## 7. Cards (36 types)

Cards are compact, themed, accessible, each with a clear data schema, "table/JSON view", copy, Keep, and (where sensible) actions that **send a message back** (never silent side effects).

1. **Weather** — current, hourly strip, 7-day, precipitation, wind, UV/AQI, alerts, units toggle, sunrise/sunset
2. **Weather forecast chart** — temp/precip combined as small multiples
3. **Stock/crypto quote** — price, Δ, sparkline, range, volume, "as of" time and delayed-data note (never advice)
4. **KPI / metric** — value, delta, target, sparkline, status
5. **Metric grid / scoreboard** — 4–12 KPIs
6. **Comparison** — A vs B (products, options) with highlighted differences and winner-neutral wording
7. **Person / contact** — name, role, links, avatar from artifact
8. **Company / organisation** — facts, size, links
9. **Product** — image from artifact, specs, price, pros/cons
10. **Source / citation** — title, publisher, date, snippet, credibility note, open link (external warning)
11. **Search results list** — ranked, with sources and snippets
12. **News / article summary** — headline, bullets, source, time
13. **File / artifact** — type icon, size, hash, preview, open
14. **Code snippet / diff** — language, copy, line highlights, apply-to-canvas
15. **Timeline / events** — vertical or horizontal, grouped by date
16. **Calendar event / agenda** — day/week view, conflicts highlighted
17. **Task list / checklist** — checkable, progress, due dates (checks send a message)
18. **Kanban board** — columns, cards, drag (emits a change request)
19. **Progress / steps / pipeline** — stages with status icon + text
20. **Alert / callout** — info, success, warning, error (icon + text, never colour alone)
21. **Itinerary / travel** — flights, hotels, times, map mini
22. **Place / location** — name, address, hours, rating, mini map, "directions" as supplied link
23. **Recipe** — ingredients with scaling, steps, timers, nutrition
24. **Receipt / invoice / order** — line items, totals, taxes
25. **Poll / rating / survey** — answers send a message
26. **Quote / definition / glossary** — term, definition, source
27. **FAQ / accordion** — collapsible Q&A
28. **Table of contents / summary** — jump links
29. **Gauge / scorecard** — security score, health, SLO (ties to the security scorecards)
30. **Countdown / timer / stopwatch**
31. **Equation / math** — KaTeX render with copy LaTeX (feeds Science Lab)
32. **Molecule / protein / structure** — Science Lab viewers (later; lazy)
33. **API request/response** — method, URL (secrets redacted), status, timing, body view
34. **Log / terminal output** — ANSI colours, search, follow, copy
35. **Security finding / vulnerability** — severity, evidence, remediation, OWASP mapping
36. **Cost / usage** — tokens, spend vs cap, per-model split
37. **Agent / run card** — status, steps, duration, link to run
38. **Approval request (HITL)** — what will happen, risk, Approve/Reject/Edit (uses AG-UI interrupts)
39. **Contact / email draft** — preview only, never sends
40. **Colour palette / swatches** — hex, contrast ratios, copy
41. **QR code** — from supplied text, offline generation
42. **Sports score / standings** — as structured data from a tool

Layout containers: **carousel**, **tabs**, **grid**, **split**, **stack** so an answer can compose several components.

## 8. Exports (matrix)

| Component | CSV/TSV | JSON | XLSX | MD | PNG | SVG | PDF | GeoJSON | HTML (self-contained, sandboxed) |
|---|---|---|---|---|---|---|---|---|---|
| Table | ✔ | ✔ | ✔ | ✔ | ✔ | – | ✔ | – | ✔ |
| Chart / time series | ✔ (data) | ✔ | ✔ | ✔ (table) | ✔ | ✔ | ✔ | – | ✔ |
| Map | ✔ (features) | ✔ | ✔ | ✔ | ✔ | ✔ (vector layers) | ✔ | ✔ (+KML/GPX) | ✔ |
| Card | ✔ (fields) | ✔ | – | ✔ | ✔ | – | ✔ | – | ✔ |
| Media / JSON / HTML | n/a | ✔ | – | – | ✔ | – | – | – | ✔ |

All exports are generated in the browser, respect the current filters/window (option for "all data"), strip nothing silently, and show size before download. "Keep" stores the export as an artifact (secret scan applies). PDF uses the print stylesheet (no external service).

---

## 9. Security and privacy

- No component makes network requests on its own. Images, audio, video, tiles, fonts and icons come from the app bundle or artifact URLs; remote ones show a note (as remote images do today) unless the user enabled a basemap.
- Specs are data, never code: no `eval`, no function strings (formatters are named presets), no HTML in text fields (Markdown subset, sanitised), URL schemes limited to `https`, `mailto`, `tel`, and `artifact:`.
- Limits: max spec size, rows, features, series, nesting; timeouts for layout; worker isolation for heavy parsing.
- HTML preview and "embed" exports use a sandboxed iframe without scripts or network.
- Secrets/PII: API cards redact auth headers; the secret scan runs before Keep/export-to-cloud; location data (maps/weather) is labelled when it would leave the PC through tiles.
- Guardrails (Phase 4) can inspect `render_ui` payloads like any tool result.

## 10. Accessibility and quality bar

Keyboard operation for every control, ARIA roles and names, focus rings, screen-reader summaries ("line chart, 3 series, rising 12%…") generated from data, table view as the accessible alternative, `prefers-reduced-motion`, high-contrast and forced-colours support, RTL layouts, locale-aware numbers/dates, light/dark parity, 375px layouts, no horizontal overflow, print styles.

## 11. Performance budget

Initial bundle unchanged (all kit code lazy); ECharts and MapLibre loaded on first use; chart render < 100 ms for 5k points, map first paint < 1 s with bundled outlines; virtualised tables; down-sampling and Web Workers; memoised specs; large data via `dataRef`.

## 12. Testing strategy

Schema tests (valid/invalid/limits) for every type; `toTable()` and exporter golden-file tests; component tests with Testing Library; a11y assertions (names, roles, keyboard); fallback behaviour tests (bad spec → table); security tests (script/HTML injection, remote URLs, `javascript:` links, oversized specs); AG-UI event-shape tests; palette validation script in CI; gallery smoke test that renders every component light/dark/375px with no console errors; visual screenshots reviewed by hand (no pixel-diff dependency). Backend: `render_ui` tool validation, spec-size limits, AG-UI emission, guardrail hook. Keep 100% backend statements and frontend green + `tsc` clean.

## 13. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **G0 Foundation** | spec envelope, registry, theme tokens, export framework, `render_ui` tool, `ui_catalog`, fallback view, AG-UI events, gallery shell | one chart + one card through all three delivery paths |
| **G1 Tables** | full table feature set and exports (builds on item 6) | 100k-row table smooth; all export formats |
| **G2 Charts** | core 12 chart types, then the rest of §4 | each has table view, export, a11y summary |
| **G3 Time series** | §5 including forecast bands (forecasting MCP), anomalies, down-sampling | 100k-point series interactive |
| **G4 Maps** | MapLibre base, bundled outlines, layers, choropleth, heatmap, clusters, routes, time slider, exports; opt-in basemaps; deck.gl/H3 arcs and hexbins as G4b | works fully offline with no network calls |
| **G5 Cards** | 42 cards (§7) + layout containers | each card has schema, story, test |
| **G6 Media/JSON/HTML** | players, JSON tree, sandboxed HTML (from item 6) integrated in the kit | security tests pass |
| **G7 Agent integration** | prompt teaching, `ui_catalog`, auto-fallback to table, steering for "chart vs table", Workflows/Canvas integration ("Open in canvas") | agents pick suitable components in eval set |
| **G8 Gallery, docs, polish** | `/components` gallery, screenshots, Docs phase content, performance pass | all budgets met |

Dependencies: item 6 (running) → canvas (item 7) → G0–G8 can start after Apps or earlier if prioritised; the Science Lab reuses G5 equation/molecule cards and G2 statistical charts; the Docs phase consumes the gallery for screenshots.

## 14. Decisions needed (defaults proposed)

1. Charts engine: **ECharts for advanced + our SVG core** (default) vs one engine only.
2. Maps engine: **MapLibre + bundled Natural Earth, no tiles by default** (default); optional deck.gl later.
3. Basemap providers to offer first: **custom tile URL + self-hosted PMTiles** (default); keyed providers later.
4. Card list: the 42 above (default) — tell me any to add or drop.
5. Export PDF via print stylesheet (default) vs server-rendered PDF.

## 15. Risks

Bundle size and complexity of two heavy libraries (mitigated by lazy loading and the gallery perf budget); map data licences and tile terms (mitigated by opt-in basemaps and attribution); model misuse of components (mitigated by validation, fallback to tables and `ui_catalog`); scope creep from 42 cards (mitigated by the registry pattern — each card is a small, independent unit); library versions must pass the 14-day rule at build time.
