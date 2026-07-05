# LearnPulse dashboard — design & aesthetics review

*Reviewed 2026-07-05 with Playwright (Chromium 1.61) against a local build of `docs/`:
1440×900 and 390×844 viewports × light and dark `prefers-color-scheme`, full-page and
viewport captures, plus keyboard-focus and filtered-state captures. Contrast ratios
below were computed from the actual CSS custom-property values in `docs/style.css`
using the WCAG 2.x relative-luminance formula.*

**Audience note: this document is written as an implementation brief.** Every finding
has a Problem / Evidence / Implementation spec / Acceptance criteria structure so it
can be executed without re-deriving the analysis. Respect the two standing project
constraints throughout: **no frameworks, no build step, no external requests**
(CONTRACT.md "Constraints"), and all record-derived text stays `textContent`-only
(never `innerHTML`).

**Overall verdict:** the "instrument readout" identity (mono labels, EKG sparkline,
restrained severity palette) is distinctive and well executed — keep it. The problems
are: one broken CSS variable, an inverted information hierarchy on the change cards, a
"Notable this week" strip that currently displays duplicated commit noise with no
product attribution, three WCAG AA contrast failures, and a handful of polish items.

To reproduce the screenshots:

```bash
python3 -m http.server 8123 -d docs   # then point Playwright/chromium at localhost:8123
# capture at 1440×900 & 390×844, colorScheme 'light'|'dark', deviceScaleFactor 2
```

---

## P0 — Verified bugs (fix first; all are small)

### B1. `var(--accent)` is undefined — the wordmark accent never renders

- **Problem:** `docs/style.css:149` sets `.wordmark .mark-pulse { color: var(--accent); }`
  but no `--accent` custom property is defined anywhere in the file (the palette
  defines `--readout`, `--pulse`, etc.). The declaration resolves to the inherited
  text color, so "Learn Pulse" renders as a single-color wordmark. Screenshots
  confirm: both words are `--text` in both themes. The two-tone wordmark that commit
  `079915e` ("Restyle wordmark to 'Learn Pulse'") intended is silently dead.
- **Implementation:** change to `color: var(--readout);` (teal reads as the "pulse"
  word in both themes and matches the eyebrow), **or** define
  `--accent: var(--readout)` in `:root` if `--accent` is wanted as a semantic alias.
- **Acceptance:** in both themes, "Pulse" renders teal (`#0c8f84` light / `#6ee7d6`
  dark) while "Learn" stays `--text`. `grep -c 'var(--accent)' docs/style.css`
  returns 0 or the variable is defined.

### B2. Pluralization bug: "1 changes across 3 pages"

- **Problem:** `countLabel()` (`docs/app.js:302-308`) only handles the
  `changes === 1 && pages <= 1` case; a single change touching multiple pages emits
  "1 changes across 3 pages". Visible on the Fleet Manager card (18 June) in the
  full-page capture.
- **Implementation:** pluralize both nouns independently:
  ```js
  function countLabel(batch) {
    var changes = batch.records.length;
    var pages = batch.pages.length;
    var c = changes + (changes === 1 ? " change" : " changes");
    if (pages > 1) return c + " across " + pages + " pages";
    return c;
  }
  ```
- **Acceptance:** "1 change across 3 pages", "2 changes", "1 change" all render
  correctly; no combination emits "1 changes".

### B3. Selected filter chip fails WCAG AA in light mode (3.71:1)

- **Problem:** `button.chip[aria-pressed="true"]` (`style.css:337-342`) sets
  `color: var(--bg)` on `background: var(--readout)`. In light mode that is `#f5f7fa`
  on `#0c8f84` = **3.71:1**, below the 4.5:1 requirement for 12.5px semibold text.
  (Dark mode is fine: 12.76:1.)
- **Implementation:** in the light palette only, either darken the pressed-chip fill
  (e.g. introduce `--chip-pressed-bg: #0b6e66;` used by the pressed state) or use
  white text `#ffffff` on a darkened teal. Keep dark mode as is.
- **Acceptance:** computed contrast of pressed-chip text vs fill ≥ 4.5:1 in both
  themes (verify with the same luminance formula); the chip still reads as
  "selected" at a glance.

### B4. `--faint` text is below AA at the sizes it's used at

- **Problem:** `--faint` is used for genuinely informative small text — notable-row
  dates (0.74rem), card meta rows (0.72rem), the pulse caption (0.66rem), filter
  labels, date headings. Measured: dark `#737f92` on panel `#131a25` = **4.31:1**
  (fail); light `#6c7789` on bg `#f5f7fa` = **4.22:1** (fail). (On-panel light usage
  is 4.53:1 — barely passes.)
- **Implementation:** lighten/darken `--faint` one step in each theme until both
  common surfaces pass: dark → try `#8290a4`; light → try `#5d6a7e`. Re-run the contrast check against **both** `--bg` and `--panel` in each
  theme; adjust until all four pairs ≥ 4.5:1. Do not fix this by shrinking usage —
  the text is informative, not decorative.
- **Acceptance:** `--faint` vs `--bg` and vs `--panel` ≥ 4.5:1 in both themes; the
  visual hierarchy faint < muted < text is preserved (faint must stay visibly
  dimmer than `--muted`).

---

## P1 — Information hierarchy (highest design impact)

### D1. Card headline is the product name; the actual change is buried

- **Problem:** `renderBatch()` builds the card head as
  `badge + batchTitle(batch) + count-pill`, and `batchTitle()`
  (`docs/app.js:295-300`) returns the **product name**. So every AKS card is
  headlined "Azure Kubernetes Service" in 1rem/600 — the most prominent text on the
  card tells you the least (it repeats eight cards in a row and duplicates the
  filter chips). The substance — what actually changed — sits in the 0.9rem muted
  `card-summary` below. The full-page capture shows five consecutive cards whose
  visual anchor is identical text.
- **Implementation** (frontend-only, no contract change):
  1. In `renderBatch()`, make the linked headline the **change**, not the product:
     use the batch summary's first sentence, or where a batch has one record with a
     clean `title`, the title. Concretely: add
     `function batchHeadline(batch)` returning
     `batch.records.length === 1 && batch.records[0].title ? batch.records[0].title : firstSentence(batch.summary)`
     with `firstSentence()` slicing at the first `. ` under ~110 chars (fall back to
     the whole summary).
  2. Demote the product to a small mono tag in the head row, styled like `.tag` but
     using the product accent treatment: `head.appendChild(el("span", "product-tag", batchTitle(batch)))`.
     New CSS: `.product-tag { font-family: var(--mono); font-size: 0.68rem;
     letter-spacing: 0.04em; color: var(--muted); background: var(--panel-2);
     border-radius: 4px; padding: 0.15rem 0.45rem; }`.
  3. Keep `card-summary` for the remaining summary text, but skip rendering it when
     it equals the headline (reuse the existing `normalizeText()` comparison).
  4. Remove `white-space: nowrap` from `.card-title` at ≤560px (`style.css:401-411`)
     and allow a 2-line clamp so headlines aren't ellipsized on mobile:
     `-webkit-line-clamp: 2` pattern already used by `.card-summary`.
- **Acceptance:** scanning the timeline reads as a list of *changes* ("Applies-to
  matrix now includes AKS Automatic and AKS Standard") with product as secondary
  metadata; when the "All" filter is active every card still shows its product; no
  card renders the same string twice (headline ≠ summary).

### D2. "Notable this week" shows duplicates and commit noise, with no product context

- **Problem:** the current strip renders `summary.top_changes` verbatim
  (`renderSummary`, `docs/app.js:453-464`): the capture shows **five** rows titled
  "Adding AKS Automatic", plus "Apply suggestions from code review" and "Merge pull
  request #3434 from schaffererin/vpa-automatic" — raw commit titles that survived
  triage (pipeline issue, cross-referenced in ARCHITECTURE-REVIEW.md §5) — and
  **no row says which product it belongs to**, which stops making sense the moment
  a second product has a notable week. Every row also repeats the same date.
- **Implementation:**
  1. **Frontend dedupe (defensive, do now):** before rendering, collapse
     `top_changes` on `normalizeText(title)`, keeping the highest-`KIND_WEIGHT`
     record and a count. Render a count suffix when > 1 using the existing
     `.count-pill` style (`"×5"` or `"5 commits"`).
  2. **Product attribution:** append the same `.product-tag` element as D1 to each
     row (mono, muted), between title and date:
     `li.appendChild(el("span", "product-tag", SHORT_NAMES[rec.product] || rec.product))`.
  3. **Date de-emphasis:** the date column currently repeats "30 June 2026" seven
     times. Render the date only when it differs from the previous row's date, or
     switch to short form (`30 Jun`) via a `formatDateShort()`; keep `white-space:
     nowrap`.
  4. **Pipeline fix (the real cure, tracked in ARCHITECTURE-REVIEW.md):**
     `top_changes` should be batched/deduped and use summaries, not raw commit
     titles, in `pipeline/feeds.py`. The frontend dedupe still stays as a guard for
     older feeds.
- **Acceptance:** no two visible notable rows have the same title; each row shows a
  product tag; merge-commit boilerplate ("Merge pull request…") never appears (drop
  any deduped row whose title matches `/^merge pull request/i` client-side as a
  second guard).

### D3. Filters silently don't apply to the summary strip, notable list, or sparkline

- **Problem:** selecting a product filters only the timeline. The stat tiles ("7
  changes, last 7 days"), the notable list, and the header sparkline all remain
  global with no indication of scope — verified by the filtered-state capture
  (Azure Kubernetes Service selected; summary strip unchanged). Users reasonably
  read "7 changes" as "7 AKS changes".
- **Implementation** (all data is already client-side; no new fetches):
  1. Recompute stat tiles from `filteredRecords()` restricted to the summary window:
     extract a `renderTiles(records, windowDays)` that counts by kind over records
     with `date >= today - windowDays`, and call it from `applyFilters()` as well as
     `init()`. Keep `summary.json` as the source only for `generated_at` and the
     initial unfiltered render (so the strip still works if feeds partially fail).
  2. `pulseBuckets()` (`docs/app.js:338-354`) already reads `state.records` —
     change it to read `filteredRecords()` and re-render the sparkline in
     `applyFilters()` (rebuild the SVG; skip the draw-in animation on re-render by
     not re-adding `pulse-live`).
  3. Scope the notable list the same way (filter `top_changes` by
     `state.product`), and when the filter empties it, hide `#notable-block`.
  4. Label the scope: when a product filter is active, change the summary `<h2>` to
     `"Notable this week — " + SHORT_NAMES[state.product]` and the first tile label
     stays "changes, last 7 days" (the scope is now implied by the visible chip +
     heading).
- **Acceptance:** with "Azure Kubernetes Service" selected, the tiles, notable rows,
  and sparkline all reflect AKS-only records; switching back to "All" restores the
  global view; no additional network requests are made on filter changes.

### D4. Date appears three times per card group

- **Problem:** the group header (`h3.date-heading`, "30 JUNE 2026") is immediately
  followed by cards whose meta rows each repeat "30 June 2026"
  (`renderBatch`, `docs/app.js:546-548`) — and the notable strip repeats dates
  again. Pure redundancy that widens every meta row.
- **Implementation:** remove `formatDate(batch.date)` (and its trailing `.sep`)
  from the card meta row; the date-heading already carries it. Keep the commit
  count as the first meta item.
- **Acceptance:** card meta rows read `N commits · tag tag` with no date; grouping
  headers remain the only date display in the timeline.

---

## P2 — Visual identity & polish

### D5. Replace emoji badges with CSS severity glyphs

- **Problem:** the kind badges use color emoji (`💥 Breaking`, `📝 Update`,
  `🧪 Preview`, `✅ GA`, `⚠️ Deprecation`, `🆕 New` — `KINDS`, `docs/app.js:6-13`)
  inside monospace pills. Emoji clash with the otherwise disciplined instrument
  aesthetic, render inconsistently across platforms (Segoe UI Emoji vs Apple Color
  Emoji vs Noto), ignore the theme (the same 📝 sits on light and dark pills), and
  repeat in three places (badges, stat tiles via `k.badge`, kind filter chips).
- **Implementation:**
  1. Drop the emoji from `KINDS[].badge` strings → `"New"`, `"GA"`, `"Preview"`,
     `"Deprecation"`, `"Breaking"`, `"Update"`.
  2. Add a colored glyph via CSS `::before` on `.badge` (and optionally kind filter
     chips): `content: ""; display: inline-block; width: 0.5em; height: 0.5em;
     border-radius: 50%; margin-right: 0.35em; background: currentColor;` — the
     badge fg colors already encode severity, so `currentColor` is enough. For
     extra differentiation give `breaking-change` a diamond
     (`border-radius: 1px; transform: rotate(45deg)`) — severity should not be
     color-only (WCAG 1.4.1); the text label already satisfies this, so the shape
     variation is a bonus, not a requirement.
  3. Update the "Kind display" table in CONTRACT.md (it currently specifies the
     emoji) — per the contract's own rule, change both sides.
  4. Stat tile labels (`renderSummary` uses `k.badge`) automatically lose the emoji;
     confirm the tiles still read clearly ("6 / Update").
- **Acceptance:** no emoji anywhere in the UI; badges show dot+label in the existing
  badge palette; identical rendering across macOS/Windows/Linux screenshots;
  CONTRACT.md table updated to match.

### D6. Humanize the reason tags

- **Problem:** raw triage codes are exposed verbatim as user-facing tags:
  `keyword:breaking-change`, `keyword:preview`, `doc-update`, `table-edit`
  (meta row, full-page capture). They read as debug output — the only element on
  the page that leaks pipeline internals.
- **Implementation:** add a small display map in `app.js`
  (`{"keyword:preview": "mentions preview", "keyword:breaking-change": "breaking-change keyword", "table-edit": "table edit", "new-file": "new page", "doc-update": "doc update"}`),
  falling back to `reason.replace("keyword:", "").replace(/-/g, " ")`. Keep the raw
  code in a `title` attribute for the curious.
- **Acceptance:** no tag containing `:` or kebab-case renders in the meta row; hover
  shows the raw code.

### D7. Sparkline: anchor to today and don't invent freshness

- **Problem:** `pulseBuckets()` anchors the 14-day window to the **newest record's
  date**, not today (`docs/app.js:346`). If the pipeline stalls for a week, the
  trace still ends in a confident spike at the right edge with a "live" cursor dot —
  the instrument reads healthy precisely when it isn't. (The EKG lead-in/lead-out
  interpolation is a stylistic choice and fine to keep; it doesn't misrepresent
  daily totals.)
- **Implementation:** anchor `end` to today (UTC) always: `var end = new Date();`.
  A stalled pipeline then shows the spike drifting leftward and a flat tail —
  honest decay. Pair with the staleness indicator below (D8).
- **Acceptance:** with system date mocked 10 days after the newest record, the
  right edge of the trace is flat at baseline and the cursor dot sits at 0.

### D8. Staleness indicator on `generated N ago`

- **Problem:** `relativeTime()` output (`docs/app.js:75-82`, rendered into
  `#generated-at`) is the site's only health signal, and it renders identically at
  "2 hours" and "9 days". (Also flagged from the ops side in
  ARCHITECTURE-REVIEW.md §2.4 — this is the design half of that fix.)
- **Implementation:** when `Date.now() - generated_at > 24h`, add class `stale` to
  `#generated-at` and prefix "data may be stale — ". CSS:
  `.readout.stale { color: var(--sev-dep); }` (amber in both themes, already
  defined). Keep the normal state `--faint`.
- **Acceptance:** mock `generated_at` 3 days old → amber warning text; 3 hours old →
  unchanged quiet readout.

### D9. Summary text should truncate at sentence boundaries

- **Problem:** `.card-summary` uses a 3-line clamp, so summaries cut mid-word with
  a bare ellipsis ("…test VPA to verify it's functioning properly. 1. Establish
  observability first by collecting actual resource utilization telemetr…" — VPA
  card, full-page capture). Raw CLI text also appears as a summary on the Azure
  OpenAI card ("az feature unregister --name …") — that's pipeline content quality
  (ARCHITECTURE-REVIEW.md §2.2 wires the real LLM summaries through), but the
  frontend can still degrade more gracefully.
- **Implementation:** keep the clamp as a hard backstop, but pre-trim in JS: in
  `summaryForRecord()`, after cleaning, if text length > ~220 chars, cut at the last
  sentence end (`. `) before 220 and drop the remainder (append nothing — a clean
  period beats an ellipsis). Numbered-list fragments (`" 1. "` patterns mid-text)
  should be cut before the list starts: truncate at the first occurrence of
  `/\s\d+\.\s/` when present.
- **Acceptance:** no visible card summary ends mid-word or with "…"; none contains
  a numbered-list fragment.

### D10. Notable-row links and page links: affordance and dead hrefs

- **Problem:** page links with no URL render as `href="#"` (`appendPageLinks` →
  `link(page.url || "#", …)`, `docs/app.js:310-318`), which scrolls to top and lies
  to screen readers; and feed-supplied URLs go into `href` unchecked (safe today by
  pipeline construction, but CONTRACT.md says treat records as untrusted).
- **Implementation:** in `link()`, if the href is falsy/`"#"` return
  `el("span", className, text)` instead of an anchor; add the protocol guard
  `if (!/^https:\/\//.test(href)) return el("span", className, text);`. (Both also
  flagged in ARCHITECTURE-REVIEW.md §6 — implement once.)
- **Acceptance:** no `href="#"` anchors in the DOM; a record with
  `doc_urls: ["javascript:alert(1)"]` renders plain text.

### D11. Accessibility odds and ends

- `#status` (`docs/index.html:51`) needs `aria-live="polite"` so filter-result and
  error messages are announced (already tracked in ARCHITECTURE-REVIEW.md §6).
- The kind-filter chips encode state only via `aria-pressed` + fill — fine — but
  after D5 make sure the pressed state still passes contrast (B3).
- `.notable li:hover { background: var(--panel-2); }` implies interactivity for the
  whole row, but only the title is a link. Either make the whole row the link
  target (wrap in one anchor per row, text still `textContent`) or drop the row
  hover and keep the title-only hover. Recommend the former: bigger hit target.
- Focus-visible ring is already good (teal, 2px, offset) — verified in the
  keyboard-focus capture. No change.

### D12. Minor typographic nits (take or leave)

- Two heading systems coexist deliberately (mono micro-caps for instrument labels,
  sans for content headings like "Changes to existing pages"). Keep — but
  `.category-heading` (1.1rem) is barely larger than card headlines (1rem) once D1
  lands; bump to 1.2rem or add `color: var(--muted)` differentiation.
- Stat tiles: only the first tile's value is teal (`style.css:238`); after D3 the
  tiles become filter-scoped and the teal-first convention still works. No change.
- `formatDate` produces "30 June 2026" (en-GB long form) while date-headings
  uppercase it via CSS — consistent; keep.

---

## Explicit non-goals (do not "improve" these)

- **No framework, no build step, no external fonts/scripts.** The system-font mono
  stack and hand-rolled SVG sparkline are the identity, not a limitation.
- **Don't add a theme toggle** unless requested — `prefers-color-scheme` handling is
  correct and both palettes are close siblings.
- **Don't redesign the layout.** The single 47rem column, summary-strip-then-
  timeline structure tested well at both viewports; mobile stacking (pulse card
  full-width, chips wrapping, tiles 3-up) all behave correctly in captures.
- **Keep the XSS discipline exactly as is**: every new element in D1–D6 must use
  `textContent` / `el()` helpers, never markup strings.

## Suggested implementation order

1. **B1–B4** (four small, verifiable fixes: wordmark variable, pluralization, two
   contrast tokens).
2. **D1 + D2 + D4** together (card hierarchy, notable dedupe/attribution, date
   dedup) — they touch the same two functions (`renderBatch`, `renderSummary`) and
   should be reviewed as one visual change. Screenshot before/after.
3. **D5 + D6** (badge glyphs + tag humanization) with the CONTRACT.md table update.
4. **D3** (filter-scoped summary/pulse) — the largest JS change; keep it a separate
   commit.
5. **D7–D11** as a polish batch.

Cross-cutting: after each step, re-run the Playwright capture matrix (2 viewports ×
2 themes) and the contrast script from this review; `node --check docs/app.js` and
the unittest suite stay green (`tests/test_feeds.py` is unaffected by frontend-only
changes).

## Related content-quality work (pipeline, not frontend)

The most jarring things a viewer sees today are **content** defects the frontend
can only paper over, already specified in ARCHITECTURE-REVIEW.md: raw commit titles
("Merge pull request #3434…") reaching `top_changes` (§5, digest/feeds), the paid
LLM summaries never shipping (§2.2 — one-line fix), and the KEDA "Breaking" badge
false positive from keyword triage (§5). Landing §2.2 + the `top_changes`
batching upgrade in `pipeline/feeds.py` will do as much for perceived quality as
everything in this document combined.
