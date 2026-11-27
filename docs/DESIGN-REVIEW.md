# Design review, November 2026

A review of every screen of Agent Studio against one rubric, with the fixes that followed. It was done from real
screenshots of a throw-away copy of the app (its own empty data folder, seeded with a few chats, workflows, apps, a
canvas and two test sets), not from reading the code.

- **Viewports:** 1440 × 900 (desktop) and 375 × 812 (phone).
- **Themes:** light on every screen; dark on 55 desktop screens in the first pass, on every screen at both sizes in the second.
- **Captured:** 90 screens and states × 2 viewports = 180 light captures, plus 55 dark ones, before the fixes; the
  same 180 again after them.
- **Measured, not guessed:** each capture also ran a script in the page that computed the contrast of every visible
  piece of text against its real background, the size of every control, and whether anything was wider than the
  screen.

## The rubric

| # | Question | How it was checked |
|---|---|---|
| 1 | **Hierarchy.** Is it clear what the page is, what matters most and what to do next? | By eye, per screenshot |
| 2 | **Spacing and alignment.** Do things sit on one scale and line up? | By eye |
| 3 | **Type.** One scale, serif for titles, sans for controls; readable line lengths; nothing under 11px | By eye, plus the font sizes found on each screen |
| 4 | **Colour and contrast.** WCAG AA: 4.5:1 for text, 3:1 for large text | Computed for every text node |
| 5 | **Consistent parts.** Buttons, inputs, tables, cards, tabs, badges, empty states and page titles look and behave the same everywhere | By eye, across screens |
| 6 | **Phone.** No sideways scroll, touch targets of at least 40px, tables and toolbars adapt | Computed (page width, control sizes) and by eye |
| 7 | **Keyboard and focus.** Everything clickable can be reached and shows where focus is | Code and markup of the parts that were changed |
| 8 | **States.** Hover, selected, disabled, empty, loading and error states exist and are clear | By eye where the seeded data showed them |
| 9 | **Overflow.** Long names, paths and addresses wrap or shorten cleanly | An 80-character workflow name, long paths, long addresses |
| 10 | **Icons and motion.** One icon set; motion is small and can be switched off | By eye |
| 11 | **Dark theme.** Same quality as light | 55 dark captures plus the same contrast script |
| 12 | **Words.** Plain, short, no stray symbols or typos | By eye |

Severity: **P0** unusable or misleading; **P1** clearly wrong, hurts use or fails a rule above; **P2** polish.

## Result in numbers

| | Before | After |
|---|---|---|
| Light captures with at least one text below 4.5:1 | 177 of 180 | 14 of 180 after the first pass (MCP tiles and the run page's agent tag); 0 after the second |
| Low-contrast text found (capped at 14 per screen) | 1,081 | 20 after the first pass; 0 after the second |
| Phone screens that scrolled sideways | 4 | 0 |
| Phone controls under 36px (excluding links inside sentences, checkboxes with a label, and the small "i") | on every screen (the Assistant button was 31px on all 90) | 0 |
| Console errors | 0 | 0 |

Counts by severity over both passes (one row of the tables below = one finding): **0 P0, 40 P1 and 32 P2 found. All 40 P1 are fixed. Of the P2, 31 are fixed and 1 is left. Five more small things turned up at the very end; they are in the "Left" list** (listed at the end).

## Cross-cutting issues

| # | Issue | Sev | Fix | Status |
|---|---|---|---|---|
| C1 | **Light-theme text colours failed AA almost everywhere.** Muted text was 4.0–4.3:1 on the page, sidebar and soft panels; links and accent text 3.3–3.9:1; green and amber status text 3.1–3.6:1; the white label on the accent button 4.04:1. | P1 | Tokens in `globals.css`: `--mut` darkened (#76736b → #67645c); `--acc` deepened slightly (#c4633f → #b5522d, white on it is now 4.99:1); new text tokens `--acc-text`, `--ok-text`, `--warn-text`, `--bad-text` used wherever a colour is used *as text* (59 rules and 88 inline styles moved over), while fills, borders and icons keep the brand tones. Dark theme got the same text tokens. A test now computes the ratios from the stylesheet. | Fixed |
| C2 | Faint grey (`--faint`, 2.3–2.7:1) was used for some text (timestamps, palette hints). | P1 | Text now uses `--mut`; `--faint` is for dots and decoration only. | Fixed |
| C3 | Dark theme: the selected segment of a switch (Chart / Table, Grid / List…) had white on the accent, 3.1:1. | P1 | Uses `--acc-fg` (dark text on the lighter dark-theme accent, 5.8:1). | Fixed |
| C4 | **Rows of tabs hid the selected tab.** With 16 settings tabs, or on a phone, the selected tab was off-screen and nothing marked the row as scrollable. | P1 | New shared `components/TabStripFx.tsx`: scrolls the selected tab into view (once per selection, so a row you scrolled by hand stays put). A CSS-only fade (driven by the row's own scroll position, so no script touches the markup) marks an edge that has more tabs. Covers `.tabs`, `role="tablist"`, the set-up guide's steps and the agent wizard's steps. | Fixed |
| C5 | Six tab rows had no `role="tablist"` / `role="tab"` / `aria-selected` (Settings, Security, Memory, Discovery, Runs, MCP server details). | P1 | Added. | Fixed |
| C6 | **Phone: touch targets too small.** Assistant button 31px, chips 27–31px, small buttons 31px, icon buttons 32px, checkboxes 15px. | P1 | At 820px and below: 40px for buttons, selects, tabs, links styled as buttons and sidebar rows; 36px for dense chips and switch segments; 18px checkboxes; the 17px "i" keeps its size but gets a 41px touch area. | Fixed |
| C7 | **Phone: four screens scrolled sideways** (Settings › Agents & sandbox by 652px, › External agents 49px, › Runtimes 23px; Workflows › Runs 231px). The floating Assistant button followed the wider page and was cut off. Causes: a long path in a non-shrinking input, a screen-reader-only label escaping a scrolling table, an unbreakable address, a select as wide as its longest option. | P1 | Shared rules: inputs and selects never exceed their container; long words wrap inside cards on a phone; scrolling tables contain their own absolutely positioned children; row items may shrink. The folder picker wraps instead of overlapping its Save button. | Fixed |
| C8 | The capture script used by the docs (`npm run docs:shots`) could not see C7: on a phone Chrome widens `innerWidth` to fit an overflowing page, so "content wider than the window" was never true. | P1 | The check now compares against the device width. | Fixed |
| C9 | Sidebar foot: status, "Ctrl K" and five icon buttons shared one line; since the bell was added, "Ctrl" and "K" wrapped onto two lines. In the narrow sidebar the five icons overflowed its 60px. | P1 | Two lines: status and shortcut, then the five tools (stacked in the narrow sidebar). The theme button got a spoken label. | Fixed |
| C10 | Sidebar: the page list scrolls on its own, and the selected page's link could be below the fold (MCP servers, Catalog, Memory, Telemetry… on a 900px-high window), so no page looked selected. | P1 | The selected link is scrolled into view when the page changes. | Fixed |
| C11 | Card titles (15px, weight 600) looked lighter than the bold row titles inside the same card: hierarchy upside down. | P1 | Card titles are 15.5px bold. | Fixed |
| C12 | Three page titles were off-system: Live and Components used a bare `h1` (sans bold instead of the serif page title), and Components wrapped itself in a second page frame (different margins). | P1 | Both use the shared page-head and sub-title; the extra frame is gone. | Fixed |
| C13 | A label that is also a `.row` stacked its checkbox *above* its text, centred (global `label` is a column). Seen on Live's licence boxes and Settings › Auto. | P1 | `label.row` lays out in a row and does not wrap. `label.check` aligns to the first line and wraps its text in one block. | Fixed |
| C14 | The "i" beside page titles sat below the title's baseline. | P2 | Centred on the title; consistent gap. | Fixed |
| C15 | Two different primary buttons for "create": terracotta on Skills, MCP, Knowledge, Apps, Science; black on Workflows. | P2 | Rule: **terracotta (`acc`) is the page's main create action; black (`pri`) confirms inside forms, dialogs and toolbars.** Workflows' "New workflow" and "Add an API" follow it. Not audited button by button (163 uses): see "Left". | Fixed in the second pass |
| C16 | Dialog titles: the close button dropped under a long title on a phone, and the floating Assistant button covered it. Dialogs were not announced as modal. | P1 | Shared `Modal` head keeps the button on the title's line; dialogs sit above the Assistant button; `aria-modal="true"`. | Fixed |
| C17 | Range sliders used the browser's blue. | P2 | `accent-color` from the palette. | Fixed |
| C18 | Type scale has many half-steps (10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 15…). | P2 | Text under 11px raised to 11px (badges, timeline tags, workflow box headers). Chart tick labels stay at 10–10.5px: raising them clipped the axis. The half-steps themselves are left. | Fixed in the second pass |
| C19 | Rows you pick from (agents, test sets) were `div`s with a click handler: no keyboard access, inline styles, wrapping at random. | P1 | Shared `.ag-item` row: `role="button"`, Tab and Enter/Space, selected state, one layout. | Fixed |
| C20 | KPI rows left one orphan card on its own line (6 cards in 5 columns), and KPI cards that are buttons centred their text while the others were left-aligned. | P2 | The grid fits its cards to the width; button cards align left. | Fixed |
| C21 | Messages from the server showed commands in raw \`backticks\`. | P2 | New `Ticks` component draws them as code (chat error cards, external-agent notes). | Fixed |

## Screen by screen

Only screens with findings of their own are listed; every screen also had C1 (contrast) and, on a phone, C6 (touch targets).

| Screen | Issue | Sev | Fix | Status |
|---|---|---|---|---|
| Chat (phone) | The title shrank to "C…" because four tools shared its line | P1 | Title on its own line, tools under it | Fixed |
| Chat (phone) | The send button sat alone on a third row at the left | P1 | The bar keeps send at the right; pickers wrap beside it | Fixed |
| Chat, error card | Raw backticks in the hint | P2 | C21 | Fixed |
| Dashboard | Orphan "Ready" card; see C20 | P2 | C20 | Fixed |
| Dashboard | The "$" of the y-axis labels touched the card's edge | P2 | Wider left margin in the chart | Fixed |
| Agent Studio | List rows wrapped at random: "· 3 sub · ⚙" on one line, the deploy button on the next, the score badge on a third | P1 | Name and tier on the first line with the score at the right; a quiet second line ("3 sub · builds agents"); C19 | Fixed |
| Agent Studio | "⚙" alone meant "can build agents" | P2 | Replaced by the words | Fixed |
| Agent Studio, tools | "✎" marks tools that can change things: a text glyph, not from the icon set, and never explained on the screen | P2 | | Fixed in the second pass |
| Deploy to Claude Code (phone) | Each choice split into three columns (button, label, explanation), wrapping word by word; Browse ran off the dialog | P1 | Label and explanation wrap as one block; the folder field shrinks (C7, C13) | Fixed |
| Workflows › Flows | The arrow that shows earlier runs sat on its own line above the name; "31 min ago" wrapped in two columns | P1 | Arrow, name and badges share a line; times do not wrap; a long name wraps inside its cell | Fixed |
| Workflows › Runs (phone) | Sideways scroll (C7) | P1 | C7 | Fixed |
| Workflow editor | Opening any workflow without an unsaved draft logged a red `404` for `GET /api/workflows/{id}/draft` in the console | P1 | The server answers `{"doc": null}` (200); the editor treats that as "no draft" | Fixed |
| Workflow editor (phone) | The toolbar takes five rows (265px) before the canvas | P2 | | Fixed in the second pass |
| Apps › a static chat app | The preview reported "Cannot set property history of #<Window>": the template's script named a variable `history`, which is the browser's own. **A real bug: the chat template could not send.** | P1 | Renamed in `backend/app/apps/templates.py` | Fixed |
| Apps › Versions (phone) | The note field is cut to "Not…" | P2 | | Fixed in the second pass |
| Science Lab | Tabs are buttons in a third style (filled pill) | P2 | | Fixed in the second pass |
| Live | Title and licence boxes: C12, C13 | P1 | C12, C13 | Fixed |
| Runs › a run | Timeline labels overlap at the left ("06:47:49 PM+4s") on a phone | P2 | | Fixed in the second pass |
| Runs › a run | The coloured agent tag has white text at 3.5:1 on the pink lane colour | P2 | | Fixed in the second pass |
| External agents | The row of pickers: on a phone the workspace field shrank to 40px beside a wrapping label; on desktop the agent picker collapsed to 44px | P1 | Pickers share the row by weight and wrap as whole fields | Fixed |
| Components | C12 | P1 | C12 | Fixed |
| Telemetry | KPI cards: two centred, two left (C20). Cost per day: slabs a seventh of the width, a blue that is not in the palette (`var(--accent, #5b8def)`, a token that does not exist), no baseline | P1 | C20; thin bars in the chart palette on a baseline | Fixed |
| Telemetry | "5 runs" / "1 failed" wrap inside the small tables | P2 | | Fixed in the second pass |
| Security | Two rows of tabs in the same style, one under the other | P2 | | Fixed in the second pass |
| Evaluations | With nothing picked, the right two thirds of the page were blank | P1 | An empty state that says what to do | Fixed |
| Settings › Agents & sandbox (phone) | Save lay on top of Browse; sideways scroll | P1 | C7 | Fixed |
| Settings › External agents | The agent's description was squeezed into a narrow column between the checkbox and the Test button | P1 | It sits under the header row, with commands drawn as code | Fixed |
| Settings › Voice | Slider in browser blue (C17); stacked full-width selects with no gap between a field and the next label | P2 | C17 fixed; the stacking is left | Fixed in the second pass |
| Settings › API & A2A (phone) | The two switches broke into narrow columns around the address | P1 | C13 | Fixed |
| Settings › Backup & history | The import box was 170px wide in a 1,000px card | P2 | Full width | Fixed |
| Settings › Artifacts | The "i" of "Secret scan before a copy leaves this PC" overlaps the end of the label | P2 | | Left |
| Set-up guide, agent wizard | The step row was cut at the right with no hint | P2 | C4 | Fixed |
| A wrong address | The framework's bare white "404" page: no way back, blinding in the dark theme | P1 | `app/not-found.tsx`: in the app's frame, three ways back | Fixed |

## Second pass

A second pass closed the gaps the first left: the P2 items, the screens skipped the first time, and things the
first set of after-shots got wrong or missed.

**Captured this time:** all 97 screens and states at 1440 × 900 and 375 × 812 in light *and* dark (the dark set now
covers every screen on both sizes); 59 screens at a tablet width (900 × 1100) and at 200% zoom (a 720 × 450 window);
a real streaming chat with Ollama `qwen3:8b`; the canvas beside a chat; the artifact preview; a workflow run and a
run waiting for approval; the simple app builder. The capture now waits for loading sketches to go before it shoots.

| # | Finding | Sev | Fix | Status |
|---|---|---|---|---|
| S1 | **The first after-shot of the Dashboard was its loading state**, and that state was six blank cards and nothing else (the shimmer was hidden by the card's own background). | P1 | The loading state sketches the whole page (six cards, the chart, two panels) with a moving shimmer, a sub-title that says it is loading, and `aria-busy`. It respects reduced motion. No other capture had been taken early (checked by comparing the amount of text on each screen before and after). | Fixed |
| S2 | "Backend offline — run `npm run dev`" (Dashboard, chat header, sidebar) is a developer's message; the installed app has no such command. | P1 | Says the server is not answering and that it is tried again. | Fixed |
| S3 | **Phone: the Assistant button floated over the top bar** as a dark pill with a shadow. | P1 | On a phone it is part of the bar: flat, bordered, 36px, inside the bar's height. The bar reserves its space, so the name beside it shortens instead of sliding under. Dialogs sit above it. | Fixed |
| S4 | Phone chat: the title was one clipped line above tools pushed to the right; the composer's pickers were cramped. | P1 | The title may take two lines; the tools start at the left under it; the pickers have room and the send button keeps the right edge. | Fixed |
| S5 | **Canvas beside a chat (desktop): a full-height terracotta "Chat / Canvas" bar stood between the sidebar and the chat.** It is the phone-only switch; a later global restyle of `.seg` had switched it back on at every width. | P1 | Hidden above phone width again, by a rule the restyle cannot override. | Fixed |
| S6 | In the narrow chat beside a canvas the send button dropped to its own row at the left. | P1 | The composer bar keeps send at the right at every width. | Fixed |
| S7 | Tablet (821–1100px): the Dashboard's meters and the Workflows table were cut off at the right; workflow names wrapped one word per line. | P1 | Meters share the row by weight; the workflows table becomes cards at 1100px and below; the name column has a minimum width above that. | Fixed |
| S8 | Workflows › Runs with a run open: nine columns squeezed beside the panel ("Started by" on two lines, the duration cut to "8 min 17 s…"). | P2 | With a run open the table keeps the identifying columns; the rest are in the panel. | Fixed |
| S9 | No way to skip the sidebar by keyboard: about 30 Tab stops before the page. The composer's pickers showed no focus ring. | P1 | A "Skip to the page" link is the first Tab stop; the pickers show the ring. | Fixed |
| S10 | MCP server tiles: white initials on generated tints, some at 2.3–3.0:1. | P2 | The eight tints were deepened to 4.6:1 or better; a switched-off server's tile goes grey instead of fading. | Fixed |
| S11 | Run page: the agent tag had white text at 3.5:1 on the lighter lane colours. | P2 | Black or white is chosen per colour (never below 4.58:1). | Fixed |
| S12 | Workflow run graph: a skipped box was faded to 60%, taking its text to 2.5:1. | P2 | Marked by a dashed, flat outline instead. | Fixed |
| S13 | Type scale half-steps (11.5, 12.5, 13.5, 14.5, 15.5). | P2 | 78 rules and 2 inline styles moved to whole pixels (12, 13, 14, 15). A test keeps it so. Chart tick labels inside SVG stay at 10–10.5px. | Fixed |
| S14 | Science Lab's tabs were filled pills; Security had two rows of tabs in the same style. | P2 | Science Lab uses the app's tab row; Security's "Overall / Per agent" is a segmented switch. | Fixed |
| S15 | Workflow editor on a phone: five rows of toolbar. | P2 | One row that scrolls sideways, beside the Assistant button. | Fixed |
| S16 | Agent tools: "✎" text glyph, unexplained. | P2 | The edit icon from the icon set, with a spoken label and a one-line legend. | Fixed |
| S17 | Button colours: of the "Save" buttons, 11 were terracotta and about 20 black. | P2 | Audited all 150 primary buttons against the rule (terracotta = create, start, install, send; black = save, apply, confirm). The 11 Saves moved to black. | Fixed |
| S18 | Small items: Telemetry's small tables wrapped "5 runs"; the run timeline's labels overlapped on a phone; the app version note was cut to "Not…"; stacked fields had no gap between a field and the next label. | P2 | No-wrap cells; first and last time label only on a phone; a field beside a button keeps a usable width; a gap between stacked labels. | Fixed |

Measured after the second pass (light, the 113 re-captured screens, which include every changed one): **0 texts
below 4.5:1, 0 screens that scroll sideways, 0 console errors.** In the dark set (194 captures, taken before S10–S12
were fixed) those three were the only texts below 4.5:1.

**Keyboard order** was walked with Tab on the chat, Dashboard, Agent Studio, Workflows, Settings and MCP servers:
focus moves top to bottom, with one backward step on Settings and one on MCP servers, and every stop showed a ring
except the composer's pickers (S9).

**200% zoom:** every page falls back to the phone layout and none scrolls sideways.

## Left, with reasons

1. **Settings › Artifacts, the "i" beside a long field label** overlaps the label's last word when the label fills its column. The "i" is placed at the label's right edge; fixing it properly means making the label's text and its "i" one row in the shared field markup, which touches every form.
2. **Chart tick labels shrink with the chart** on a phone and a tablet (the SVG scales as a whole), down to about 6px on the Dashboard at 375px. The two hand-drawn charts need to be drawn at the width they have: a rework, not a style fix.
3. **MCP servers by keyboard:** one Tab stop lands on an element with no size (a hidden copy of the selected row). Not yet traced.
4. **Runs list on a phone** scrolls its table sideways inside the card and cuts long chat titles; it should become cards like the workflows table.
5. **Type scale:** now whole pixels, but still eight sizes between 11 and 19px. Fewer sizes is a redesign of the scale.
6. **Artifact preview:** "Delete…" sits alone, centred, under the details.
7. **Not a design issue, but seen:** with Auto, the "fast" pick was `ollama/llama3.2`, which was not installed on the test copy; the chat said so and fell back. Auto should prefer installed local models.

## Not reviewed

- **Live sessions in progress** (microphone, avatar video, the cost meter while running): need a real microphone and a paid session.
- **A tool running and an approval card inside a chat:** the streaming capture caught "Thinking…" and finished answers; the plain assistant used no tools. (The workflow approval card was reviewed.)
- **Screens that need installed extras or cloud accounts:** red-team runs and results, a filled security activity log, runtime deploys, data-source results, knowledge-base documents and search, voice once installed, Git on an app.
- **The simple app builder on a phone** (reviewed on desktop only), and the artifact list row opened on a phone.
- **Tablet and 200% zoom** covered 59 of the 97 screens (the main page of each area and every Settings tab), light theme only.
- **Screen-reader output, Windows high-contrast mode,** and keyboard order beyond the six pages above.
- **The packaged desktop window** (same pages; not re-captured).

## How to repeat it

The docs capture (`npm run docs:shots -- --serve --ollama` in `frontend/`) starts the throw-away copy on ports 8777 and
3199. The review drove headless Chrome over the DevTools protocol against that copy; the palette's contrast is now
also checked by `frontend/tests/components/design-review.test.tsx` on every test run.
