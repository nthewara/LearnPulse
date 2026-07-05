# LearnPulse

Track Azure feature changes by watching Microsoft Learn documentation.

**Live site: https://nthewara.github.io/LearnPulse/**

Microsoft updates its public Learn documentation whenever a service changes. That
makes the docs repos a high-signal, machine-readable feed of what's actually
shipping across Azure — often ahead of blog posts and release notes.

**LearnPulse** turns that firehose of doc commits into:

1. **Per-product change tracking** — currently piloting with Azure Kubernetes
   Service via [MicrosoftDocs/azure-aks-docs](https://github.com/MicrosoftDocs/azure-aks-docs)
   (three products: AKS, Kubernetes Fleet Manager, AKS Application Networking)
2. **A summary view** — a minimal website with a weekly summary and a filterable
   change timeline, with noise (typos, link fixes, metadata sweeps) filtered out

See [PLAN.md](PLAN.md) for the full design and [CONTRACT.md](CONTRACT.md) for the
pipeline↔website data contract.

## How it works

```
products.yml ──▶ pipeline/ (Python, GitHub Actions every 6h)
                   ingest → triage → summarize → feeds → digest
                     │
                     ├─▶ data/learnpulse.db      (SQLite system of record)
                     ├─▶ docs/data/*.json        (feeds the website reads)
                     └─▶ digests/YYYY-Www.md     (weekly digest markdown)

docs/ ──▶ GitHub Pages: index.html + style.css + app.js (vanilla, no framework)
```

- **Ingestion** pulls path-scoped commits from the docs repos via the GitHub API —
  no cloning required.
- **Triage** drops editorial noise by rule (metadata sweeps, typo/link fixes, sync
  merge commits) and tags candidates (new page, preview/GA/deprecation keywords,
  table edits).
- **Summarization** optionally uses Claude (set the `ANTHROPIC_API_KEY` repo secret
  to enable) to classify and summarize each change; without the key it falls back
  to rule-derived titles and summaries.
- **The website** is three hand-written files — no framework, no build step, no
  external requests.

## Running locally

```bash
pip install pyyaml
python3 pipeline/run.py --max-commits 5 --since-days 7   # small unauthenticated run
python3 -m http.server 8000 -d docs                      # then open localhost:8000
```

Set `GITHUB_TOKEN` for higher API rate limits and `ANTHROPIC_API_KEY` for LLM
summaries.

## Adding a product

Add an entry to [products.yml](products.yml) — any `(repo, path)` pair in a
Microsoft Learn docs repo works; the next pipeline run picks it up.

## Status

🟢 Pilot live for AKS. Work is tracked in [Issues](../../issues).
