# LearnPulse — Design & Implementation Plan

Track Azure feature changes by diffing Microsoft Learn documentation, and present a
per-service change feed plus a summarized dashboard on a deliberately minimal,
framework-free website.

**Initial scope:** Azure Kubernetes Service docs in
[MicrosoftDocs/azure-aks-docs](https://github.com/MicrosoftDocs/azure-aks-docs)
and selected Azure AI docs in
[MicrosoftDocs/azure-ai-docs](https://github.com/MicrosoftDocs/azure-ai-docs).

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

### 1.1 Not everything lives in azure-docs: dedicated docs repos

Some services have been split out of the monolith into dedicated docs repos. AKS is
one of them: [MicrosoftDocs/azure-aks-docs](https://github.com/MicrosoftDocs/azure-aks-docs)
(the public sync of the private `azure-aks-docs-pr` repo). Azure AI docs also live
in [MicrosoftDocs/azure-ai-docs](https://github.com/MicrosoftDocs/azure-ai-docs).
Findings (July 2026):

- **Small and focused**: ~285 MB (vs ~27 GB for azure-docs), 653 markdown pages —
  shallow-cloneable if we ever want local diffing, though the API remains the default.
- **Three product areas** under `articles/`: `aks` (582 pages),
  `kubernetes-fleet` (60), `application-network` (11) — one repo yields three
  trackable products.
- **Same conventions as azure-docs**: identical YAML frontmatter
  (`ms.service: azure-kubernetes-service`, `ms.date`, `ms.topic`), and PR-merge
  commits carry descriptive titles (e.g. *"Add note on Azure Spot node pools for
  stateful workloads (#3395)"*), interleaved with automated sync/metadata commits
  that rule triage can drop by pattern.

**Design consequence:** the watchlist is configured as **(repo, path) pairs**, not
paths within a single repo. `products.yml` looks like:

```yaml
products:
  - id: aks
    name: Azure Kubernetes Service
    repo: MicrosoftDocs/azure-aks-docs
    path: articles/aks
  - id: kubernetes-fleet
    name: Azure Kubernetes Fleet Manager
    repo: MicrosoftDocs/azure-aks-docs
    path: articles/kubernetes-fleet
  - id: azure-openai
    name: Azure OpenAI Service
    repo: MicrosoftDocs/azure-ai-docs
    path: articles/foundry/openai
    learn_base: https://learn.microsoft.com/azure/foundry/openai/
```

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

- A scheduled GitHub Action (e.g. every 6 hours) walks the **watchlist** in
  `products.yml` (AKS and Azure AI product areas; expandable later).
- For each product: `GET /repos/<repo>/commits?path=<path>&since=<last_run>` to list
  new commits, then `GET /commits/<sha>` for the file-level patch data (filenames,
  status, patch hunks). Rate limits are generous for this volume (5,000 req/h
  authenticated); even a future 144-service sweep is fine if batched.
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

### 3.6 The website: minimal by design

Constraints: **no framework, no build step, no npm.** The entire site is three
hand-written files served by GitHub Pages:

```
site/
  index.html    # one page: header, product filter, timeline, summary strip
  style.css     # small hand-written stylesheet, dark/light via prefers-color-scheme
  app.js        # vanilla JS: fetch('data/*.json'), render, filter — no dependencies
```

How it works:

- `app.js` fetches the JSON feeds generated by the pipeline (`data/aks.json`, …,
  `data/summary.json`) and renders them into the DOM with plain template functions.
- **Summary strip on top**: "last 7 days" counts by kind and the top notable changes.
- **Timeline below**: newest-first cards — kind badge (🆕 ✅ 🧪 ⚠️), title, 1–2
  sentence summary, date, links to the doc page and the underlying commit.
- **Product filter**: plain buttons (All / AKS / Fleet / App routing); filtering is a
  client-side array filter, no routing library — at most a `#aks` URL hash.
- Deployment is trivial: GitHub Pages serves the repo; the scheduled Action commits
  refreshed JSON and the site updates on next load. No backend, nothing to operate.

Because feeds are capped (latest ~200 records per product), the page stays a few
hundred KB and needs no pagination machinery; older history remains queryable in
SQLite.

## 4. Milestones

| # | Milestone | Deliverable |
|---|-----------|-------------|
| 1 | **AKS ingestion MVP** | Script + scheduled Action pulling path-scoped commits from `azure-aks-docs` for the three product areas (aks, kubernetes-fleet, application-network), storing raw commit/patch data in SQLite |
| 2 | **Diff engine + rule triage** | Frontmatter/body diff parsing, noise filtering (incl. sync/metadata-sweep commits), reason-coded candidates; measure precision on a hand-labeled sample week |
| 3 | **LLM summarization** | ChangeRecords with kind + summary; prompt + eval on the labeled sample |
| 4 | **Minimal website** | `data/*.json` feeds + the three-file vanilla HTML/CSS/JS site (§3.6) on GitHub Pages: summary strip + filterable timeline |
| 5 | **Weekly digest** | Committed weekly digest markdown generated from ChangeRecords |
| 6 | **Scale-out** | Add more products via `products.yml` — services in azure-docs and other dedicated Learn repos (e.g. `fabric-docs`, `entra-docs`, `sql-docs`) behind the same pipeline |

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

- ~~Which services should the initial watchlist include?~~ **Decided: AKS first,
  then selected Azure AI products** (plus AKS sibling areas kubernetes-fleet and
  application-network, which come free from the same repo).
- Should deleted/renamed pages get their own "retired capability" view?
- Notification channel priority after the dashboard: RSS, email digest, or a
  GitHub Discussion per week?
