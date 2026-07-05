# LearnPulse

Track Azure feature changes by watching Microsoft Learn documentation.

**Live site: https://nthewara.github.io/LearnPulse/**

Microsoft updates its public Learn documentation whenever a service changes. That
makes the docs repos a high-signal, machine-readable feed of what's actually
shipping across Azure — often ahead of blog posts and release notes.

**LearnPulse** turns that firehose of doc commits into:

1. **Per-product change tracking** — currently watching AKS and selected Azure AI
   product docs from [MicrosoftDocs/azure-aks-docs](https://github.com/MicrosoftDocs/azure-aks-docs)
   and [MicrosoftDocs/azure-ai-docs](https://github.com/MicrosoftDocs/azure-ai-docs)
2. **A summary view** — a minimal website with a weekly summary and a filterable
   change timeline, with noise (typos, link fixes, metadata sweeps) filtered out

See [PLAN.md](PLAN.md) for the original design,
[ARCHITECTURE.md](ARCHITECTURE.md) for the implemented architecture, and
[CONTRACT.md](CONTRACT.md) for the pipeline↔website data contract.

## What is included

- **Python pipeline** in `pipeline/` that ingests path-scoped GitHub commits,
  triages noisy documentation churn, summarizes signal records, emits JSON feeds,
  and writes weekly Markdown digests.
- **SQLite system of record** at `data/learnpulse.db`, with incremental cursors,
  dedupe state, raw commit data, triage fields, and summaries.
- **GitHub Pages dashboard** in `docs/`: vanilla `index.html`, `style.css`, and
  `app.js` reading committed JSON from `docs/data/`.
- **Refresh workflow** in `.github/workflows/pipeline.yml`, currently run manually
  via `workflow_dispatch` (a 6-hour schedule is stubbed out, ready to re-enable),
  committing refreshed `data/`, `docs/data/`, and `digests/` files.

## How it works

```
products.yml ──▶ pipeline/ (Python, GitHub Actions on manual dispatch)
                   ingest → triage → summarize → feeds → digest
                     │
                     ├─▶ data/learnpulse.db      (SQLite system of record)
                     ├─▶ docs/data/*.json        (feeds the website reads)
                     └─▶ digests/YYYY-Www.md     (weekly digest markdown)

docs/ ──▶ GitHub Pages: index.html + style.css + app.js (vanilla, no framework)
```

- **Ingestion** pulls path-scoped commits from the docs repos via the GitHub API —
  no cloning required — and uses per-product cursors for incremental runs.
- **Triage** drops editorial noise by rule (metadata sweeps, typo/link fixes, sync
  merge commits) and tags candidates (new page, preview/GA/deprecation keywords,
  table edits).
- **Summarization** optionally uses Claude (set the `ANTHROPIC_API_KEY` repo secret
  to enable) to classify and summarize each change; without the key it falls back
  to rule-derived titles and summaries.
- **The website** is three hand-written files — no framework, no build step, no
  external requests — and renders a 7-day summary plus product/kind filters.
- **Digests** are written to `digests/<ISO-year>-W<week>.md` from non-noise records
  in the current ISO week.

## Running locally

```bash
python3 -m pip install -r requirements.txt

# Small, rate-limit-friendly backfill for all configured products
python3 pipeline/run.py --max-commits 5 --since-days 7

# Or limit to product ids from products.yml
python3 pipeline/run.py --products aks,azure-openai --max-commits 5 --since-days 7

# Preview the dashboard
python3 -m http.server 8000 -d docs
```

Then open <http://localhost:8000>.

Pipeline arguments:

- `--since-days N`: backfill from the last N days instead of using stored cursors.
- `--max-commits N`: cap commit-detail fetches per product for local/rate-limited
  runs.
- `--products aks,azure-openai`: process only selected product ids.

## Configuration and environment

- Products are configured in [products.yml](products.yml) as `(repo, path)` pairs
  with a `learn_base` URL used to map changed Markdown files to Learn URLs.
- `GITHUB_TOKEN`: optional locally, recommended for higher GitHub API rate limits;
  provided automatically by Actions.
- `ANTHROPIC_API_KEY`: optional; enables Claude summaries. If unset or failing, the
  pipeline falls back to deterministic heuristic summaries.
- The workflow is triggered manually (`gh workflow run pipeline`, optionally with a
  `since_days` input for backfills); the cron schedule is commented out until the
  pipeline has proven itself on supervised runs.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

## Adding a product

Add an entry to [products.yml](products.yml) — any `(repo, path)` pair in a
Microsoft Learn docs repo works; the next pipeline run picks it up.

## Status

🟢 Dashboard live for AKS and Azure AI docs. Work is tracked in [Issues](../../issues).
