# LearnPulse data contract

The contract between the **pipeline** (producer) and the **website** (consumer).
Both sides must conform to this file. If you change it, change both sides.

## Repo layout

```
products.yml                 # watchlist config (repo, path) pairs — see file
pipeline/                    # Python pipeline (stdlib + pyyaml only)
data/learnpulse.db           # SQLite system of record (committed)
docs/                        # GitHub Pages root (served at /LearnPulse/)
  index.html  style.css  app.js
  data/                      # JSON feeds the site fetches (relative paths!)
    products.json
    <product-id>.json        # one per product: aks, kubernetes-fleet, application-network
    summary.json
digests/                     # weekly digest markdown, e.g. 2026-W27.md
.github/workflows/pipeline.yml
```

## docs/data/products.json

```json
{
  "generated_at": "2026-07-05T03:00:00Z",
  "products": [
    {"id": "aks", "name": "Azure Kubernetes Service"},
    {"id": "kubernetes-fleet", "name": "Azure Kubernetes Fleet Manager"},
    {"id": "application-network", "name": "AKS Application Networking"}
  ]
}
```

## docs/data/<product-id>.json — per-product feed

Newest-first, capped at 200 records. Older history stays in SQLite only.

```json
{
  "product": "aks",
  "generated_at": "2026-07-05T03:00:00Z",
  "records": [ /* ChangeRecord, newest first */ ]
}
```

### ChangeRecord

```json
{
  "id": "817854da-0",
  "product": "aks",
  "date": "2026-07-02",
  "kind": "preview",
  "title": "Add note on Azure Spot node pools for stateful workloads",
  "summary": "One to two sentences describing what product capability changed.",
  "page_change_category": "new-page",
  "reasons": ["keyword:preview", "new-file"],
  "files": ["articles/aks/spot-node-pools.md"],
  "doc_urls": ["https://learn.microsoft.com/azure/aks/spot-node-pools"],
  "commit_url": "https://github.com/MicrosoftDocs/azure-aks-docs/commit/817854da",
  "sha": "817854da"
}
```

Field rules:
- `id`: `<short-sha>-<n>` — unique, stable across regenerations.
- `date`: ISO date (YYYY-MM-DD) of the commit (author date, UTC).
- `kind`: exactly one of `new-feature | ga | preview | deprecation | breaking-change | doc-update`.
- `title`: <= 100 chars, human-readable (cleaned commit title or LLM-generated).
- `summary`: 1–2 sentences. May equal title-derived text when LLM is unavailable.
- `page_change_category`: `new-page` when the record adds at least one markdown
  page; otherwise `existing-page` for modified or renamed markdown pages. Older
  feeds may omit it; the website derives the category from `files` and `reasons`.
- `reasons`: rule-triage reason codes (free-form kebab strings, shown as small tags).
- `doc_urls`: derived from files via product `learn_base` (strip `.md`); may be empty.
- All string fields are plain text — the website must escape them when rendering
  (records are built from third-party commit data; treat as untrusted).

## docs/data/summary.json

```json
{
  "generated_at": "2026-07-05T03:00:00Z",
  "window_days": 7,
  "counts_by_kind": {"new-feature": 2, "ga": 1, "preview": 4, "deprecation": 0, "breaking-change": 0, "doc-update": 11},
  "counts_by_product": {"aks": 15, "kubernetes-fleet": 2, "application-network": 1},
  "top_changes": [ /* up to 8 full ChangeRecord objects from the window, ranked */ ],
  "total_records": 18
}
```

Ranking weight for `top_changes`: breaking-change > deprecation > ga > new-feature > preview > doc-update; tie-break newest first.

## Kind display (website)

| kind | badge | color intent |
|------|-------|--------------|
| new-feature | 🆕 New | accent |
| ga | ✅ GA | green |
| preview | 🧪 Preview | purple |
| deprecation | ⚠️ Deprecation | amber |
| breaking-change | 💥 Breaking | red |
| doc-update | 📝 Update | neutral/muted |

## Constraints

- Website: fetch feeds with **relative** paths (`data/summary.json`) — the site is
  served under `https://<user>.github.io/LearnPulse/`. No frameworks, no CDN, no
  build step, no external requests of any kind.
- Pipeline: Python 3.11+, stdlib + `pyyaml` only. Anthropic API called via urllib
  when `ANTHROPIC_API_KEY` is set; graceful heuristic fallback when not.
- Feeds must always be valid JSON and match this schema even when empty
  (`records: []`).
