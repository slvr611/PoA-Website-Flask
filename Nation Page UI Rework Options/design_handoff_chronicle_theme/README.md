# Handoff: Chronicle Theme — Path of Ages site-wide UI rework

## Overview
A site-wide visual and structural rework of the Path of Ages Flask website ("Chronicle" theme): a dark parchment-and-ember fantasy look with warm rounded shapes, a consolidated nation page (13 collapsible sections → 6 tabs), a persistent summary stat strip, and a **schema-driven generic data-item page** that replaces per-collection page variants. Approved by the project owner after exploring three directions; this is the chosen hybrid (layout of option "1a Ledger", palette of option "1b Chronicle").

## About the Design Files
The files in this bundle are **design references created in HTML** (Design Component prototypes). They are not production code — do not copy them into the Flask app. The task is to **recreate these designs inside the existing Flask/Jinja codebase** (`templates/*.html`, `static/styles.css`, existing tooltip/breakdown JS), reusing its established patterns: Jinja template inheritance from `layout.html`, the `$jsonSchema` files in `json-data/schemas/`, the `context-tooltip` breakdown mechanism, and existing auth/visibility conditionals. All existing functionality must be preserved — this is a reskin + layout regrouping, not a feature change.

The prototypes were rendered with real data from the 2026-07-20 MongoDB backup (nation **Khanya**, character **Odirile**, wonder **Leyline Amplifier**, race **Leyborne**) so spacing and truncation behavior reflect real content.

## Fidelity
**High-fidelity.** Colors, typography, spacing, radii and states are final. Recreate pixel-perfectly with the values below. The only intentionally unresolved items are listed in "Open items" at the end.

## Design Tokens
Add these as CSS custom properties at the top of `static/styles.css` (names are suggestions; keep them consistent):

```css
:root {
  /* Grounds */
  --bg-page:      #191309;   /* page behind the card */
  --bg-card:      #221a12;   /* main content card */
  --bg-deep:      #1a140d;   /* top nav bar, flag tile */
  --surface:      rgba(240,229,210,.05);  /* inset cards */
  --surface-hi:   rgba(240,229,210,.08);  /* neutral chips, hover fills */
  --border:       rgba(240,229,210,.10);  /* card borders */
  --border-hi:    rgba(240,229,210,.12);  /* table row rules, small cards */
  --border-strong:rgba(240,229,210,.25);  /* table header rule, outline buttons */

  /* Text */
  --text:         #f0e5d2;
  --text-dim:     rgba(240,229,210,.55);  /* labels, captions */
  --text-mid:     rgba(240,229,210,.7);   /* secondary copy */

  /* Voices */
  --accent:       #d98a54;   /* PER-NATION: overridden by nation accent color */
  --accent-hi:    #f4c89a;   /* active nav text, link hover */
  --heading:      #a8b88a;   /* sage green — all card headings */
  --link:         #e8b46a;   /* default link + pending-changes gold */
  --pos:          #a8d88a;   /* good values */
  --warn:         #e8d46a;   /* caution values (over territory, draining) */
  --crit:         #e08a6a;   /* bad values (over pop cap, hostile) */
  --done:         #5a7a44;   /* researched-tech pill fill */
  --avail:        #8aa86a;   /* available-tech pill border */
  --avail-text:   #c8dcae;

  /* Shape */
  --radius-card:  16px;
  --radius-sm:    14px;      /* district/small cards */
  --radius-pill:  999px;     /* every button, chip, pill, progress bar */
}
```

- **Banner gradient** (nation/header band): `linear-gradient(105deg, color-mix(in oklch, var(--accent) 40%, #221a12) 0%, #3a4230 100%)`, decorated with 1–2 large soft circles `background: rgba(240,229,210,.07)` overflowing the edges.
- **Breakdown affordance**: any number backed by a breakdown gets `border-bottom: 2px dotted rgba(217,138,84,.5); cursor: help` and opens the existing `context-tooltip`.
- **Dotted-leader rows** (key/value ledgers): label `var(--text-dim)` left, bold value right, connected by `flex:1; border-bottom:1px dotted rgba(240,229,210,.25)`.
- **Focus**: `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` — never the default blue ring.

## Typography
- **Headings**: Caprasimo (Google Fonts) — page title 30–34px, card headings 17px, stat values 20–22px. Used for display only.
- **Body**: Figtree (Google Fonts) — body 13–13.5px, captions 11–12px, table headers 11px uppercase `letter-spacing:.06em` weight 700.
- Card headings are always `color: var(--heading)` with an optional inline count in 12px body-font `var(--text-dim)`, e.g. "Blessings & burdens (16 modifiers)".

## Screens / Views

### 1. Global chrome (`layout.html`)
- Page body `background: var(--bg-page)`; content sits in a centered card `max-width:1240px; background:var(--bg-card); border-radius:var(--radius-card); box-shadow:0 2px 14px rgba(0,0,0,.5); overflow:hidden`.
- **Top nav** (full-width bar inside the card, `background:var(--bg-deep); border-bottom:1px solid rgba(240,229,210,.08); padding:10px 28px`):
  - Left: site name "Path of Ages" in Caprasimo 17px.
  - Nav links as pills (`padding:6px 12px; border-radius:999px`); inactive `color:rgba(240,229,210,.8)`; the **active** section gets `background: color-mix(in oklch, var(--accent) 24%, transparent); color:var(--accent-hi); font-weight:600`.
  - Right: "My Changes" link (with a count badge when > 0: accent bg pill, white text, 11px bold) and a 30px round avatar.
- **Links**: `a { color: var(--link); } a:hover { color: var(--accent-hi); }` site-wide.

### 2. Nation page (`nation_owner.html` / `nation_routes.py`) — reference: `Nation Page Khanya.dc.html`
The 13 collapsible sections regroup into **6 tabs**: Overview · Economy (resources + jobs + trade) · Development (districts + cities + wonders + tech + quests) · Military · Diplomacy (relations + vassals) · Laws. Nothing is removed, only relocated.

- **Banner band** 110px tall (gradient above), with an image slot for the nation banner.
- **Identity row** overlapping the banner by −34px: 84px flag tile (`border-radius:20px; background:var(--bg-deep); border:3px solid var(--accent)`), then name (Caprasimo 34px) with a one-line subtitle: `Region · Ruled by Ruler · Government type · Temperament` (links in gold; temperament "Player" in `var(--heading)`).
- **Action cluster** (right-aligned, baseline of identity row):
  - Optional gold pending-changes chip: `color:var(--link); background:rgba(232,180,106,.14); padding:7px 14px; border-radius:999px` — replaces the always-visible pending link; show only when count > 0.
  - **Edit** — solid pill, `background:var(--accent); color:#221a12; font-weight:700; padding:9px 20px`; hover `filter:brightness(1.1)`.
  - **Customize** — outline pill, `border:2px solid var(--border-strong)`; hover `background:rgba(240,229,210,.08)`.
  - **⋯ menu** — 36px round outline button collapsing: Pending changes, Change history, Recalculate, Visibility toggle (admin). Keep all existing routes/permissions.
- **Stat strip**: 6 equal cards (`grid-template-columns:repeat(6,1fr); gap:12px`), each `background:var(--surface); border:1px solid var(--border); border-radius:16px; padding:12px 16px`; 11px uppercase label, Caprasimo 22px value (dotted-breakdown underline where a breakdown exists), 11.5px bold sub-line. Contents: Stability (state word colored: Fragile=warn; sub "gain% · loss%"), Treasury (sub "+income / tick · cap"), Pops "x / cap" (crit when over; sub "N over capacity · N working"), Territory "current / effective" (warn when over), Administration, Infamy (crit when > 0; sub karma).
- **Tab bar**: text buttons `padding:10px 16px; border-radius:12px 12px 0 0`; active = `font-weight:700; color:var(--accent-hi); border-bottom:3px solid var(--accent)` sitting on a 2px `rgba(240,229,210,.15)` rule; inactive `color:var(--text-mid)`.
- **Tab contents** (all cards are `var(--surface)` + `var(--border)` + 16px radius, padding 18–20px):
  - **Overview**: 1.1fr/.9fr grid — "At a glance" 4-col dl (dotted breakdowns on stability chances), "Blessings & burdens" modifiers table (Effect / Value / Duration / Source; value colored pos/crit; strip raw Discord URLs from sources, keep the leading text); right column: "Progress quests" (sage-tinted card `background:rgba(90,122,68,.16); border-color:rgba(168,184,138,.25)`, slotted quests get 8px progress bars `background:#8aa86a` on `rgba(240,229,210,.12)` track, unslotted as small chips, done state "✓ x / y" in pos), Demographics (linked race/culture/religion + minority count), Chronicle notes (italic, `white-space:pre-line`).
  - **Economy**: resources table (Resource / Production / Consumption / Net / Storage / note; net colored pos/warn/crit/dim; note column in gold for warnings like "draining stock"); Jobs table with per-worker upkeep/production and an accent "Edit jobs" pill button; Trade dl + "Income ledger" dotted-leader breakdown (Total row in `var(--accent-hi)`).
  - **Development**: district cards 4-per-row (`border-left:4px solid <tint>; border-radius:14px` — tint sage for education, accent for civic/production, `#7a9ac0` for military, faded for empty slots); Cities and Wonders cards; **tech tier list** replacing the Cytoscape graph as default: one row per tier (Roman numeral label), techs as pills — researched `background:var(--done); color:#fff` · investing `background:var(--accent); color:#221a12` with "n/cost" in the label · available `border:2px solid var(--avail); color:var(--avail-text)` · locked `background:rgba(240,229,210,.1); color:rgba(240,229,210,.45)`; `title` tooltip = state · cost · prerequisites; legend row underneath. Tier = prerequisite depth. Keep the graph reachable via a secondary link if desired.
  - **Military**: when empty, a centered empty-state card ("The realm keeps no standing army" + capacities + doctrines) over three "Recruitable" chip cards (land/naval/support). When units exist, use the unit stat table pattern (same kh-table styling).
  - **Diplomacy**: relations table (dot + relation word: Hostile=crit, Unfriendly=warn, Neutral=dim, Friendly=pos; sorted hostile-first), pact slots caption; Vassals card with dotted-leader tribute rows and a crit warning line when vassal relations are hostile.
  - **Laws**: single card, 3-column grid of label/value rows separated by `1px solid var(--border)`.

### 3. Generic data item page (`dataItem.html`) — reference: `Data Item Page.dc.html` + `schema_render.js`
**One template for every collection**, driven by `json-data/schemas/<collection>.json` — the prototype proves it on a character, a wonder and a race. Rendering rules (mirror of the existing Jinja logic, restyled):

- **Header**: slim 72px banner band; 74px round image tile (`border:3px solid var(--accent)`; shows `properties.*.image` field when present); kicker line `<Collection singular> — data item` in 11px uppercase sage; name in Caprasimo 30px; subtitle = the schema's `laws` fields joined with " · ". Action cluster identical to the nation page.
- **Stat strip**: first ~6 schema `number` fields **without** a cap field (`max` not a string), formatted per `format` ("percentage" → ×100 with %); `show_breakdown` fields get the dotted underline + context-tooltip.
- **Left column**:
  - **"Capped values" bars** — every number whose `max` names another field: label / pill track (fill = value ÷ largest cap; `var(--accent)`, `#8aa86a` at cap, `var(--crit)` when negative) / "value / cap" with dotted breakdown.
  - **"Details" ledger** — dotted-leader rows for: string/enum/date (skip `long_text`), boolean (✓ pos / ✗ crit), `linked_object` (resolved name as gold link; `noneResult` fallback), `json_resource_enum`/`json_district_enum` (display name), plus overflow numbers.
  - **Chip cards** — one per string-array (strengths, weaknesses, titles) and per linked-object array (artifacts, with preview fields as `title` tooltip); rotate chip tints (accent / sage / ember / neutral).
  - **Object fields** (resource tables etc.) — dotted-leader sub-cards; keep the existing resource-table special case (Production / Stockpile ÷ Capacity) for nation-like objects.
- **Right column**:
  - **"Blessings & burdens"** — the modifiers array as a table (Effect / Value / Duration / Source), scrollable past ~360px with a sticky header (`background:#241b12`). Effect label = modifier type + attribute/resource/job + non-flat scaling (e.g. "Effective Territory · per x resource nodes magic"). Duration −1 → "permanent". Strip raw URLs from sources, keeping any leading text; keep `format_discord_link` behavior for the linked icon if you have one.
  - **Notes cards** — every `long_text` string field, italic, `white-space:pre-line`, heading "Chronicle notes" for `notes`, otherwise the field label (e.g. "Effect Description").
- Respect all existing schema flags: `hidden`, `view_access_level`, `field_tiers`/visibility, `hideIfNone`, `static`. Fields that are caps of other fields never render on their own.

### 4. Data list page (`dataList.html`) — guidance (not prototyped)
Use the same chrome: heading + count, an accent "New" pill button, and the kh-table styling below; row hover `background:rgba(240,229,210,.04)`; names as gold links; keep existing filters as pill inputs.

## Table styling ("kh-table", used everywhere)
```css
table: width 100%; border-collapse: collapse; font-size 13–13.5px; color var(--text);
th: text-left; padding 6–7px 10px; 11px uppercase, letter-spacing .06em, weight 700,
    color var(--text-dim); border-bottom: 2px solid var(--border-strong);
td: padding 6–7px 10px; border-bottom: 1px solid var(--border-hi);
```

## Interactions & Behavior
- Tabs swap content client-side (or server-side with a query param — either is fine); active tab state per the tab-bar spec. Deep links to a tab are desirable.
- Hover: solid pills `filter:brightness(1.1)`; outline pills/nav `background:rgba(240,229,210,.08)`; links → `var(--accent-hi)`. No transitions longer than 150ms.
- All numeric breakdowns keep the existing `context-tooltip` JSON mechanism — the dotted underline is the only visual change.
- The ⋯ menu is a small popover (same surface/border tokens, radius 14px) listing the collapsed admin/secondary actions.
- Per-nation accent: nations already have a cosmetics/customize concept — set `--accent` on the page root from the nation's stored color; every accent-derived style (banner gradient, buttons, active states, tech "investing" pills, flag border) follows automatically. Default `#d98a54`.
- Empty states are sentences in `var(--text-dim)` italic (e.g. "No active trade routes."), never blank cards.

## State Management
No new state. Existing Flask context (item, schema, breakdowns, linked_objects, visibility flags) is sufficient. The schema-driven renderer is a restyling of the existing `dataItem.html` field loop — same conditionals, new markup. Tech tier depth can be computed once server-side from `tech.json` prerequisites.

## Assets
- Google Fonts: **Caprasimo** (400) and **Figtree** (400–700).
- Lucide icons (stroke-width 2.75) if icons are wanted; the prototypes use text glyphs (⋯ ◆ ✓ ✗ →) only.
- Nation banner/flag and character portraits are user-supplied image slots — no bundled imagery.

## Files (design references in this bundle)
- `Nation Page Khanya.dc.html` — the nation page, populated with Khanya's real data (all 6 tabs).
- `Data Item Page.dc.html` — the generic schema-driven item page (switchable between character / wonder / race).
- `schema_render.js` — the schema→view-model rules in executable form; mirror this logic in Jinja/Python.
- `Nation Page Rework.dc.html` — the original three explored directions (1a/1b/1c), for context on rejected alternatives.
These reference their own runtime (`support.js`, `data/`, `_ds/`) from the design project; read them for values and structure rather than running them standalone.

## Open items
1. Pending-changes count on the chip/badge is not wired in the prototype (changes collection wasn't sampled) — use the real count, hide at 0.
2. District card subtitles on the nation page were partly inferred; pull real effects from `district_defs`.
3. `json_unit_enum` / `db_unit_enum` array fields fall back to entry counts in the prototype — extend the Details ledger with resolved display names.
4. Mobile: the tech tier list and stat strip are designed to wrap (pills wrap naturally; stat strip can drop to 3+3, sidebar-less). No dedicated mobile mocks were made.
