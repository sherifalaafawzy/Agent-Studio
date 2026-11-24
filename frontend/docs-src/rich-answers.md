---
title: Rich answers (AG-UI)
section: Build
order: 39
summary: Tables, charts, time series, maps and cards an agent can show instead of plain text, how they stay safe, and how to switch them off.
tags: ag-ui, components, charts, tables, maps, time series, cards, render_ui, gallery, export, json, html preview, audio, video
routes: /components/
---
Agents can show data the way a person reads it best: a table, a chart, a map, a card. Each answer is a typed piece of data (never code), so it is safe.

![The AG-UI component gallery](shot:components-gallery)

## What is available

- **Tables** (fenced as `table`, `csv` or `tsv`): sort, filter, hide columns, page, expand long cells, copy, and export what you see as CSV, JSON, Excel or Markdown. Formats, data bars, groups with subtotals.
- **JSON tree viewer:** search, copy a path or value, download.
- **26 chart types:** bar, line, area, scatter, bubble, histogram, box, candlestick, heatmap, calendar, Gantt, waterfall, funnel, pie, treemap, sunburst, tree, Sankey, network, radar, gauge and more.
- **Time series** (Apache ECharts): zoom, range buttons, grouping, moving average, index to 100, log scale, annotations, thresholds, unusual points and a forecast with its uncertainty band.
- **Maps** (MapLibre): points, clusters, heat maps, bubbles, coloured countries, GeoJSON, routes, arcs, hex bins and a time slider.
- **42 cards:** weather, quotes, people, findings, forecasts and many more.
- **HTML preview** in an empty sandbox frame with a no-network policy (Preview or Source, download, full screen).
- **Audio and video players** for artifact files only (speed, captions).

Every component has a **Show as table** view and an **Export** menu (CSV, JSON, Excel, Markdown, PNG, SVG, a standalone web page, PDF through print, and GeoJSON, KML or GPX for maps), plus **Keep**, **Open in Artifacts** and **Open in canvas**.

## How an agent uses them

An agent calls the `render_ui` tool (validated; a chart that cannot be drawn is shown as a plain table of the same data) and `ui_catalog` (lists the types, with each one's shape and an example). A fenced `ui` block works too. External AG-UI clients receive the same data as `ui.component` events.

## Safe by construction

A component is data, never code. Specs are cleaned on the server and again in the browser: no event handlers or styles, links only to https, mailto, tel, artifact and app pages, secrets removed, size limits. Components never fetch anything. Remote images are not loaded. HTML only runs in the empty-sandbox frame.

## Maps are offline by default

The land is a bundled public-domain Natural Earth outline. A real basemap is opt-in in Settings, Agents and sandbox, Rich components, with your own tile address or a PMTiles file. The page says who could then see which area you look at.

## The gallery

`/components/` draws every kind from its built-in example and lets you paste your own spec.

## Switch it off for an agent

Each agent has two switches in its **AG-UI** section: **Rich components in answers** and **AG-UI stream for other apps**. See [Agent editor](doc:agent-editor#ag-ui).

## Not built yet

3D molecule viewers, a deck.gl or H3 layer, map measure and draw tools, and syntax colouring in code blocks.
