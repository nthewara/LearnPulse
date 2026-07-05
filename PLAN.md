# mslearndiff — Design & Implementation Plan

Track Azure feature changes by diffing Microsoft Learn documentation, and present a
per-service change feed plus a summarized dashboard.

## 1. Why the docs repo is a good signal

Microsoft's public Learn docs for Azure are open source at
[MicrosoftDocs/azure-docs](https://github.com/MicrosoftDocs/azure-docs). Findings from
inspecting the repo (July 2026):

- **One directory per service.** Everything lives under `articles/<service>/`, e.g.
  `articles/azure-functions/`, `articles/aks/`, `articles/app-service/`. There are
  ~144 top-level service directories. This gives us a natural "application/service"
  partition for free.
- **Every page has structured YAML frontmatter**: `title`, `description`, `ms.topic`
  (overview / how-to / reference / whats-new), `ms.date`, `ms.custom`. `ms.date` is
  bumped on meaningful revisions, which helps separate content updates from mechanical
  edits.
- **Very active**: dozens of commits per day, and commit messages are usually
  descriptive (e.g. `[Functions] Clarify Core Tools installations by version (#317580)`).
- **The GitHub API supports path-scoped history**:
  `GET /repos/MicrosoftDocs/azure-docs/commits?path=articles/<service>&since=<ts>`
  returns just that service's commits. We never need to clone the ~27 GB repo.

### The core challenge: signal vs. noise

Most commits are typo fixes, link repairs, style-guide sweeps, or bulk metadata
changes. The value of this project is **classification**: deciding which doc changes
imply a *product/feature change* vs. mere editorial churn. The plan below treats that
as a first-class pipeline stage.

## 2. Architecture overview

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
│  Ingestion   │──▶│  Diff engine  │──▶│  Classifier / │──▶│  Store       │
│ (GitHub API, │   │ (per-file     │   │  Summarizer   │   │ (SQLite +    │
│  scheduled)  │   │  patch parse) │   │ (rules + LLM) │   │  JSON feeds) │
└─────────────┘   └──────────────┘   └──────────────┘   └──────┬──────┘
                                                                │
                                              ┌─────────────────▼─────────────┐
                                              │  Views: static site dashboard  │
                                              │  per-service feeds, RSS, digest│
                                              └───────────────────────────────┘
```

Everything can run **inside this GitHub repo itself** — a scheduled GitHub Action
ingests, classifies, commits the updated database/feeds, and publishes a static
dashboard to GitHub Pages. No servers to operate.

## 3. Component design

### 3.1 Ingestion (no clone required)

- A scheduled GitHub Action (e.g. every 6 hours) walks a **watchlist** of services
  (start small: `azure-functions`, `aks`, `app-service`, `api-management`, …;
  expandable via config file `services.yml`).
- For each service: `GET /commits?path=articles/<service>&since=<last_run>` to list
  new commits, then `GET /commits/<sha>` for the file-level patch data (filenames,
  status, patch hunks). Rate limits are generous for this volume (5,000 req/h
  authenticated); a full 144-service sweep is fine if batched.
- Persist a cursor (last seen SHA / timestamp) per service so runs are incremental.

**Alternative considered:** shallow/sparse clone of azure-docs and `git log` locally.
Rejected as the default — the repo is ~27 GB and the API gives us everything we need.
Sparse checkout (`articles/<service>` only) remains a fallback if API limits bite.

### 3.2 Diff engine

For each changed `.md` file in a commit:

- Parse frontmatter before/after: did `ms.date` move? did `title`/`description`
  change? was the file **added** (often a brand-new feature page) or **deleted**
  (deprecation signal)?
- Parse the markdown body diff into hunks; extract changed headings and sentences.
- Compute cheap features: lines added/removed, whether changes touch tables
  (support-matrix updates are high-signal), whether "preview", "GA",
  "generally available", "deprecated", "retirement" appear in added lines.
- Special-case high-signal files: pages named `whats-new*.md`, `overview.md`,
  `*-support*.md`, TOC (`toc.yml`) additions (new TOC entry ⇒ new capability page).

### 3.3 Classification & summarization

Two-stage, cheap-first:

1. **Rule-based triage (free, deterministic):**
   - *Noise*: typo/link/format-only diffs, bulk `ms.custom`/metadata sweeps,
     acrolinx/style commits, image path changes → drop or mark `editorial`.
   - *Signal heuristics*: new file, deleted file, TOC entry added, "what's new" page
     touched, preview/GA/deprecation keywords, support-matrix table edits, version
     number bumps → mark `feature-change` candidates with a reason code.
2. **LLM summarization (only for candidates):** send the filtered hunks + commit
   message to a small model (e.g. Claude Haiku) with a constrained prompt:
   *"Summarize what product capability changed; classify as
   new-feature | ga | preview | breaking-change | deprecation | doc-improvement;
   1–2 sentences."* Output is stored as structured JSON. Because triage removes
   ~80–90% of commits, cost stays tiny.

Each surviving event becomes a **ChangeRecord**:

```json
{
  "service": "azure-functions",
  "date": "2026-07-04",
  "sha": "46f00d8e",
  "kind": "new-feature | ga | preview | deprecation | breaking-change | doc-update",
  "title": "Flex Consumption adds .NET 10 support",
  "summary": "…1-2 sentences…",
  "files": ["articles/azure-functions/supported-languages.md"],
  "evidence": ["+ .NET 10 (preview) added to supported versions table"],
  "url": "https://github.com/MicrosoftDocs/azure-docs/commit/46f00d8e"
}
```

### 3.4 Storage

- **SQLite database** committed to the repo (or kept as an Actions artifact) as the
  system of record: `services`, `commits_seen`, `change_records` tables.
- **Generated JSON feeds** under `data/`: `data/<service>.json` (latest N records)
  and `data/summary.json` (cross-service rollup) — these are what the front-end reads.

### 3.5 Views

**A. Per-service change feed** — pick a service, see a chronological timeline of
classified changes with kind badges (🆕 new, ✅ GA, 🧪 preview, ⚠️ deprecation),
summaries, and links to the underlying commit/doc page.

**B. Summary dashboard (the headline view):**
- "This week across Azure": counts by kind, most-active services, and the top N
  notable changes (ranked by kind weight: breaking > deprecation > GA > preview > new
  doc > update).
- Weekly digest generation: a Markdown digest per week
  (`digests/2026-W27.md`) committed to the repo — searchable history, diffable, and
  trivially turned into an email/RSS feed later.

**Delivery:** a static site (plain HTML/JS or Astro) reading the JSON feeds, deployed
via GitHub Pages from this repo. No backend needed.

## 4. Milestones

| # | Milestone | Deliverable |
|---|-----------|-------------|
| 1 | **Ingestion MVP** | Script + Action pulling path-scoped commits for 5 pilot services on a schedule, storing raw commit/patch data in SQLite |
| 2 | **Diff engine + rule triage** | Frontmatter/body diff parsing, noise filtering, reason-coded candidates; measure precision on a hand-labeled sample week |
| 3 | **LLM summarization** | ChangeRecords with kind + summary; prompt + eval on the labeled sample |
| 4 | **Feeds + per-service view** | `data/*.json` generation, static site with service timeline on GitHub Pages |
| 5 | **Summary dashboard + weekly digest** | Cross-service rollup page + committed weekly digest markdown |
| 6 | **Scale-out** | Expand watchlist toward all 144 services; add other Learn repos (e.g. `fabric-docs`, `entra-docs`, `sql-docs`) behind the same pipeline |

## 5. Risks & mitigations

- **Noise overwhelms signal** → rule triage first, LLM second; keep a labeled sample
  to track precision/recall of the classifier as rules evolve.
- **Docs repo restructures** (dirs move, repo splits — has happened before, e.g.
  azure-docs spin-offs) → watchlist is config-driven with per-service paths; renames
  detected via commit `status: renamed`.
- **API rate limits at full scale** → incremental cursors, batched sweeps, ETag
  caching; sparse-checkout fallback.
- **`ms.date` isn't perfectly reliable** → treat it as one feature among several,
  never the sole trigger.
- **LLM cost drift** → hard cap: only triaged candidates are summarized; batch
  requests; digest-level summarization reuses record summaries rather than raw diffs.

## 6. Open questions

- Which services should the pilot watchlist include? (Proposal: the ones you actually
  use day-to-day.)
- Should deleted/renamed pages get their own "retired capability" view?
- Notification channel priority after the dashboard: RSS, email digest, or a
  GitHub Discussion per week?
