# LearnPulse architecture

LearnPulse is a repo-hosted data pipeline and static dashboard for tracking
meaningful Microsoft Learn documentation changes. The current watchlist covers
AKS documentation in `MicrosoftDocs/azure-aks-docs` and selected Azure AI docs in
`MicrosoftDocs/azure-ai-docs`, classifies noisy editorial churn away from
product-relevant changes, and publishes a GitHub Pages dashboard from committed
JSON feeds.

## System shape

```text
products.yml
    |
    v
GitHub Actions manual dispatch (schedule stubbed out)
    |
    v
pipeline/run.py
    ingest -> triage -> summarize -> feeds -> digest
       |        |          |          |        |
       v        v          v          v        v
data/records/*.json
data/state.json         docs/data/*.json      digests/YYYY-Www.md
                              |
                              v
docs/index.html + docs/app.js + docs/style.css
                              |
                              v
GitHub Pages: https://nthewara.github.io/LearnPulse/
```

There is no application server. The scheduled workflow updates the JSON record
store, dashboard feeds, and digests, commits those generated artifacts back to the
repository, and GitHub Pages serves the static dashboard from `docs/`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `products.yml` | Watchlist of `(repo, path, learn_base)` product definitions, including AKS and Azure AI products. |
| `pipeline/` | Python pipeline stages and JSON storage helpers. |
| `data/records/<product>.json` | Deterministic per-product record history with raw commit metadata, triage output, summaries, and persisted display fields. |
| `data/state.json` | Incremental cursors and seen-commit dedupe state. |
| `docs/` | GitHub Pages site root: static HTML, CSS, JS, and JSON feeds. |
| `docs/data/` | Generated dashboard feeds: `products.json`, per-product feeds, and `summary.json`. |
| `digests/` | Generated weekly Markdown digests. |
| `.github/workflows/pipeline.yml` | Manually dispatched pipeline runner (cron stubbed out). |
| `tests/` | Stdlib unit tests for ingestion, triage, summarization, and feeds, plus Playwright E2E tests. |

The data contract between pipeline and website is documented in
[CONTRACT.md](CONTRACT.md).

## Pipeline stages

The orchestrator is `pipeline/run.py`, which executes five deterministic stages:

1. **Ingest (`pipeline/ingest.py`)**: lists commits from each configured docs repo
   path via the GitHub API, fetches commit details, extracts product-scoped file
   patch summaries, stores raw records, deduplicates by `(sha, product_id)`, and
   advances per-product cursors.
2. **Triage (`pipeline/triage.py`)**: filters editorial noise such as metadata-only
   sweeps, typo/link fixes, image-only edits, and style-guide churn. Signal records
   get a `kind`, reason tags, changed file paths, and mapped Microsoft Learn URLs.
3. **Summarize (`pipeline/summarize.py`)**: optionally calls Anthropic Claude when
   `ANTHROPIC_API_KEY` is configured. Without an API key, it uses deterministic
   patch-aware summaries that describe what changed in the docs while stripping raw
   markdown and emoji tokens.
4. **Feeds (`pipeline/feeds.py`)**: emits website JSON under `docs/data/`, including
   page-change categories (`existing-page` vs `new-page`) and `batch_key` values so
   related commits can collapse into one dashboard card.
5. **Digest (`pipeline/digest.py`)**: writes an ISO-week Markdown digest under
   `digests/` from non-noise records.

## Storage model

Committed JSON is the source of truth:

- `data/records/<product>.json`: deterministic, line-reviewable per-product
  record history. Records include raw commit metadata, triage output,
  summaries, Learn URLs, author fields, noise/signal flags, and persisted
  derived display fields such as `change_summary`, `page_change_category`, and
  `batch_key`.
- `data/state.json`: per-product incremental cursors and seen-commit dedupe
  keys so a commit is processed once per product.

Durable records keep only a bounded `patch_excerpt` for debugging
(`<= 2048` characters), not full raw patches. The pipeline may derive richer
patch summaries within a single run, but persistent data stays compact and
reviewable.

Generated dashboard feeds under `docs/data/` are optimized for the website and
can be regenerated from the JSON store. Per-product feeds are capped for page
weight; older history remains in `data/records/`. If a single product file grows
to roughly 5 MB, shard it by year (for example,
`data/records/<product>/YYYY.json`) in a future migration; do not shard before
that trigger.

## Dashboard runtime

The website is intentionally framework-free:

- `docs/index.html` defines the static shell.
- `docs/style.css` contains all styling.
- `docs/app.js` fetches `data/summary.json`, `data/products.json`, and each
  per-product feed using relative paths so the site works under `/LearnPulse/`.

The dashboard renders a 7-day summary, product and kind filters, and two primary
change sections:

- **Changes to existing pages**: modified or renamed markdown pages.
- **New pages added**: commits adding new markdown pages.

Within each section, related records are batched by category, date, product, and
change summary. Cards emphasize what changed in the documentation, affected pages,
record counts, and reason tags rather than raw commit text.

All third-party commit-derived text is treated as untrusted and assigned through
DOM `textContent`; the dashboard does not use external scripts or CDNs.

## Operations and deployment

`.github/workflows/pipeline.yml` is triggered manually (`workflow_dispatch`) with
an optional `since_days` backfill window. A six-hour cron schedule is present but
commented out; re-enable it once the pipeline has proven itself on supervised runs
(see issue #16).

The workflow:

1. Checks out the repo.
2. Sets up Python.
3. Installs `pyyaml`.
4. Runs `pipeline/run.py`.
5. Commits changed `data/`, `docs/data/`, and `digests/` artifacts back to the
   repository.

GitHub Pages is configured to serve `main` from `/docs`, so committed feed changes
are reflected on the live site after Pages rebuilds.

## Configuration and secrets

- Add or change tracked products in `products.yml`; entries can point to any
  Microsoft Learn docs repo/path pair with a matching `learn_base`.
- `GITHUB_TOKEN` increases API rate limits; it is supplied automatically in
  GitHub Actions.
- `ANTHROPIC_API_KEY` is optional. If unset or failing, the pipeline falls back to
  deterministic summaries and continues.

## Validation

Use the existing tests and syntax checks:

```bash
python3 -m unittest discover -s tests -v
node --check docs/app.js
npm run test:e2e
```

For live validation after deployment, open
<https://nthewara.github.io/LearnPulse/> and confirm the dashboard loads, AKS and
Azure AI product filters are visible, JSON feeds return HTTP 200, and the batched
change cards render without raw markdown tokens.
