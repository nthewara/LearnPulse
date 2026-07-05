# LearnPulse architecture review — proposed changes

*Deep review performed 2026-07-05 by three parallel review agents (storage/data layer,
pipeline/LLM workflow, frontend/ops/docs). Findings below are deduplicated and ranked;
items marked ⭑ were independently found by two or more reviewers. Updated same day with
§7: frontend & hosting platform analysis (GitHub Pages vs Azure vs Kubernetes).*

---

## 1. The database question: keep SQLite the engine, stop committing the binary

**Verdict: SQLite is the right *engine* for this project, but committing
`data/learnpulse.db` to git is the wrong *persistence strategy*.** You do not need a
hosted database (Postgres, Turso, Azure Tables) — that would add a secret, an external
dependency, and ops surface for what is under 100K rows/year, violating the project's
zero-ops/free-on-GitHub constraint. The problem is narrower: a binary file rewritten by
CI four times a day.

Measured from the current repo:

- `data/learnpulse.db` is 192 KB holding only **19 records**; ~110 KB of that is
  `raw_patch_summary` text (one *noise* row alone carries 44.5 KB of patch text that
  will never be read again).
- Git already stores two full db blobs after two pipeline commits. SQLite B-tree page
  churn defeats git delta compression, so at 4 snapshot commits/day (~1,460/yr) the
  realistic pack growth is **30–80 MB/yr**, growing with product count. GitHub's 100 MB
  single-file limit is a real cliff on a multi-year horizon.
- Any branch that runs the pipeline locally creates an unmergeable binary conflict —
  one side's data gets silently discarded.
- There is **no per-commit cap** on stored patch text (`pipeline/ingest.py:29` caps
  4,000 chars *per file* only) — one bulk Acrolinx sweep touching 500 AKS files stores
  ~2 MB in a single row *before* triage marks it noise.

### Recommended target state: append-only NDJSON + a small state file

- `data/records/<product>.ndjson` — one ChangeRecord JSON object per line, append-only.
  Text deltas cost ≈ the appended bytes; diffs are human-reviewable; `merge=union` in
  `.gitattributes` makes branches mergeable.
- `data/state.json` — per-product cursors + seen-SHA dedupe set.
- Drop `raw_patch_summary` from durable storage entirely. It is only needed *within* a
  single run (ingest → triage → summarize are in-process); keep at most a ~2 KB excerpt
  per record for debugging. Prerequisite: persist derived fields
  (`change_summary`, `page_change_category`, file-status list) at triage/summarize time
  so `pipeline/feeds.py:72-93` stops re-deriving them from raw patches on every run.
- If SQL ergonomics are wanted, hydrate `sqlite3.connect(":memory:")` from NDJSON at
  run start — the query helpers in `pipeline/db.py` survive nearly unchanged.

### Alternatives considered and rejected

| Option | Verdict |
|---|---|
| SQLite as a GitHub **release asset** (download at start, upload at end) | Acceptable fallback; keeps git clean but loses history/reviewability |
| SQLite via `actions/cache` | **No** — caches evict after 7 days unused; unacceptable for a system of record |
| Turso / Postgres / Azure Tables | **No** — external dependency + secret + ops for <100K rows/yr |
| DuckDB / parquet in git | **No** — still a binary with the same churn problem |

### Interim guards if staying on committed SQLite for now

- Add a ~100 KB per-commit total patch cap in `build_patch_summary`
  (`pipeline/ingest.py:126-150`).
- Null out `raw_patch_summary` for noise rows and for summarized rows older than ~90
  days, then `VACUUM`.
- Add `PRAGMA user_version` + a numbered migration list in `pipeline/db.py` — today
  the schema is `CREATE TABLE IF NOT EXISTS` only (`db.py:62`), so the first schema
  change to the committed db has no migration path.

---

## 2. Critical correctness bugs (fix before anything else)

### 2.1 ⭑ Ingestion cursor can permanently skip commits (data loss)

Two related bugs in `pipeline/ingest.py`:

- **Pagination truncation:** `list_commits` (`ingest.py:99-112`) silently `break`s on
  any request failure and returns only the newest pages (GitHub lists newest-first).
  The run processes that partial list and advances the cursor to the max committer date
  seen (`ingest.py:233-234`) — commits on the never-fetched older pages fall between
  the old and new cursor and are **never ingested**. Fix: return `(commits, complete)`
  and skip `set_cursor` when incomplete.
- **`--max-commits` interaction:** capped commits are correctly left unseen
  (`ingest.py:202-204`), but `is_seen` (`ingest.py:188-191`) and merge-skip
  (`ingest.py:194-200`) branches still advance `max_seen_committer_date`. In
  oldest-first order, a merge-skip commit *after* budget exhaustion is newer than the
  capped commits, so the cursor jumps past them forever. Fix: once the detail budget
  hits 0, stop updating `max_seen_committer_date` (or break).
- **Out-of-order committer dates:** GitHub `since=` filters on committer date;
  rebases/merges can land commits dated before the cursor. Mitigation: subtract a 24h
  overlap from the cursor when building `since` — `commits_seen` already dedupes, so
  the overlap is cheap.

### 2.2 ⭑⭑ The paid Claude summaries never reach the website

`pipeline/feeds.py:92-93`:

```python
"summary": change_summary or row["summary"] or "",
"change_summary": change_summary or row["summary"] or "",
```

`doc_change_summary()` **always returns non-empty text** (`pipeline/summarize.py:176-202`
falls back to the title), so `row["summary"]` — the LLM output — is unreachable. Both
feed fields always contain the deterministic heuristic text; only the LLM's `kind` and
`title` survive to the site, and `pipeline/digest.py:64` uses raw `title` only. This
contradicts CONTRACT.md:78-80 and means the Anthropic API spend is largely wasted today.

**Fix (one line):** `"summary": row["summary"] or change_summary`. The frontend already
prefers `change_summary` for batching (`docs/app.js:217`), so display is unchanged but
the richer LLM text becomes available — then use `summary` in the digest too, which
currently renders commit-log noise like *"Merge pull request #3434 from …"*.

### 2.3 ⭑⭑⭑ Workflow push race discards entire runs (including paid LLM calls)

`.github/workflows/pipeline.yml:45-51` pushes with no pull/rebase. The `concurrency`
group serializes pipeline-vs-pipeline but not pipeline-vs-human: merge a PR while a
multi-minute run is in flight and the final `git push` fails, discarding the run — and
because cursors/dedupe live only in the committed db, the next run **re-pays** all
GitHub API and Anthropic calls. Fix:

```yaml
git pull --rebase --autostash origin main
git push
```

Safe because the run only touches generated files. Also add `timeout-minutes:` to the
job, and consider skipping the commit entirely when content is unchanged ignoring
`generated_at` (currently CI commits every 6h even with zero data changes).

### 2.4 Silent failure everywhere — no alerting

- No `if: failure()` step in the workflow; scheduled-run failure emails go only to the
  workflow-file committer. Add a step that opens/updates a GitHub issue via `gh` on
  failure (needs `issues: write`).
- Worse, most failure modes **don't fail the run**: rate-limit exhaustion
  (`ingest.py:64-75`) and all-heuristic summarization exit 0. Make `run.py` exit
  non-zero when the rate limiter tripped, or when an API key is set but `llm == 0` and
  `summarized > 0`.
- Dashboard: `docs/app.js:640-643` shows "generated N hours ago" but nothing flags
  staleness. Add an amber `stale` state when age > 24h — this is the only user-facing
  monitor the project has.

---

## 3. LLM workflow improvements

1. **Heuristic fallback permanently masks LLM failures.** `llm_summarize` catches bare
   `Exception` (`summarize.py:262-264`); the fallback writes into the same `summary`
   column, and `unsummarized_records` selects `summary IS NULL` (`db.py:146-150`) — a
   record that fell back is never retried, with no provenance. Add a `summary_source`
   column ("llm" / "heuristic"); on retryable errors (429/5xx/network) leave `summary`
   NULL for retry next run; write heuristic only for permanent failures.
2. **Use the Message Batches API.** This workload is the canonical batch shape:
   non-latency-sensitive, keyed records, 6h cadence. Flat **50% price cut**; submit one
   batch per run keyed by `custom_id = record_id` (harvest last run's batch at the
   start of the next run if you want short jobs). Pair with the `anthropic` SDK —
   built-in 429/5xx retries; the "urllib only" constraint buys nothing since the
   workflow already pip-installs pyyaml.
3. **Structured output instead of regex-scraped JSON.** Replace the "respond with ONLY
   JSON" prompt + `_parse_llm_json` (`summarize.py:209-231`) with a JSON-schema
   constrained output (kind as an enum, `additionalProperties: false`). Guaranteed
   parseable; also cap `summary` length (title is capped at 100, summary is unbounded).
4. **Prompt injection is a real, published-output risk.** Commit messages and diff
   hunks are third-party text (any MicrosoftDocs community contributor) interpolated
   raw into the prompt (`summarize.py:30-44, 236-240`), and the LLM `title` is
   published on the site and interpolated into digest **markdown** (`digest.py:64`) —
   so `](https://evil)`-style link injection into digests is possible. Mitigations:
   wrap untrusted content in explicit delimiters with a trust-boundary instruction;
   enforce output schema; strip markdown control characters from `title` before
   writing digests. (The dashboard itself is safe: `app.js` is textContent-only.)
5. **Fix the circuit breaker.** The trip condition (`summarize.py:282-284`) never fires
   after one success; distinguish HTTP error classes (400 vs 401 vs 429) and honor
   `retry-after`.
6. **Model choice is right** (Haiku tier fits this volume); prefer the `claude-haiku-4-5`
   alias over the pinned snapshot in `summarize.py:24` unless pinning is deliberate.

---

## 4. Scaling from 8 products to dozens

Ordered by which wall you hit first:

1. **GitHub API rate limit** (Actions token: 1,000 req/hr/repo). Fixes in order:
   ETag conditional requests — a 304 costs **zero quota** and most 6h windows for quiet
   products are 304s → fine-grained PAT or GitHub App token (5,000/hr) → GraphQL
   batching per repo (six of eight products share `azure-ai-docs`). Also: when the rate
   limiter trips, remaining products are skipped wholesale (`ingest.py:251-253`) —
   products late in `products.yml` will systematically starve. Rotate start order or
   budget per product.
2. **LLM cost/wall-clock:** serial per-record calls scale linearly; Batches makes cost
   50% and wall-clock ~constant.
3. **Frontend payload:** `docs/app.js:653-655` fetches every per-product feed on every
   load — fine at 8, not at 40. Emit a merged `docs/data/all.json` (newest-first,
   capped ~300) from `feeds.py` and have the dashboard fetch only
   `summary.json` + `products.json` + `all.json`. ~15 lines, keeps per-product feeds
   for deep links.
4. **Per-product hacks in generic code:** `_applies_to_summary` hardcodes "AKS
   Automatic"/"AKS Standard" (`summarize.py:96-109`) and `MERGE_SKIP_RES`
   (`ingest.py:33-36`) is AKS-tuned — move both into `products.yml` per-product config
   before growing the watchlist.

---

## 5. Triage quality — currently tuned blind

- **The PLAN.md-promised labeled sample doesn't exist** (PLAN.md:214-223). This is the
  cheapest high-leverage addition in the whole review: a
  `tests/fixtures/labeled_week.jsonl` of ~100 real records with human
  `is_noise`/`kind` labels, plus a test asserting precision/recall floors. Every rule
  change today is unmeasurable.
- **No re-triage path:** `untriaged_records` selects `is_noise IS NULL`
  (`db.py:131-134`), so improved rules never re-classify history. Add a
  `triage_version` column + `--retriage` flag.
- Rule weaknesses: `PREVIEW_RE` matches "preview" in *any* added line
  (`triage.py:31,147`) — precision is certainly poor in Azure docs (the W27 digest's
  `keda-about` 💥 breaking-change looks like a keyword false positive); the
  metadata-only rule (`triage.py:104-105`) evaluates the *truncated* patch, so a big
  commit whose visible head is all `ms.date:` lines is misclassified as noise — store
  a `patch_truncated` flag and disable "all lines match" rules when set.
- Test coverage inverts risk: the least-tested code (cursor advancement, capping,
  pagination, LLM parse/fallback) is the highest-risk; `tests/test_ingest.py` covers
  only auth headers.

---

## 6. Smaller fixes and cheap wins

- **`next_record_id` collision crash:** `db.py:111-117` counts by full sha but builds
  ids from `sha[:8]`; an 8-char prefix collision raises `IntegrityError` mid-run.
  Derive `n` from `id LIKE '<sha8>-%'` and add `UNIQUE(sha, product)` as a backstop.
- **URL protocol guard in the dashboard:** `docs/app.js:55-60` assigns feed-supplied
  URLs to `href` unchecked. Safe today by construction, but CONTRACT.md says treat
  records as untrusted — add `if (!/^https:\/\//.test(href)) href = "#";`.
- **Accessibility:** add `aria-live="polite"` to `#status` (`docs/index.html:51`);
  render a `<span>` instead of `href="#"` for pages with no URL.
- **Partial-data honesty:** when some product feeds fail to load, `app.js:656-660`
  drops them silently — show "N of M product feeds unavailable".
- **RSS/Atom feed** — highest-leverage new feature within the static constraint:
  ~40 lines writing `docs/feed.xml` from the same records `summary.json` uses. Closes
  PLAN §6's open notification question; an email digest then comes free via any
  reader/Zapier.
- **Client-side search:** records are already in memory; a text input filtering
  title+summary is ~20 lines in `applyFilters`.
- **Contract validation in CI:** a stdlib test validating emitted feeds against
  CONTRACT.md's required keys would have caught the summary bug (§2.2).

### Documentation drift to fix

| Doc claim | Reality |
|---|---|
| README.md:64 `pip install -r requirements.txt` | Workflow installs `pyyaml` directly (`pipeline.yml:31`) — point it at requirements.txt |
| CONTRACT.md `summary` ≠ `change_summary` | Always identical in emitted feeds (§2.2) |
| PLAN.md architecture diagram promises RSS | Doesn't exist (see cheap wins) |
| PLAN.md §3.2 frontmatter before/after diffing, `whats-new*.md` special-casing | Not implemented — triage is regex-over-patch-lines only |
| PLAN.md paths `data/`, `site/` | Actually `docs/data/`, `docs/` — add a "superseded by ARCHITECTURE.md" header |
| ARCHITECTURE.md "signal records get a `kind`" | Noise records also get `kind: doc-update` (`triage.py:121-129`) |

---

## 7. Frontend & hosting: should this move off GitHub Pages?

**Short answer: no — stay on GitHub Pages today, and if/when you genuinely need
server-side capability, the right step is Azure Static Web Apps, not Kubernetes.**
Kubernetes would be a category error for this workload.

### 7.1 What kind of workload this actually is

Two components, and they have *different* hosting questions:

- **The pipeline** is a 6-hourly batch job. GitHub Actions *is* the right scheduler
  for it regardless of where the frontend lives — free at this volume, cron built in,
  co-located with the repo it commits to. Nothing in this review changes that.
- **The dashboard** is 3 static files + JSON. A hosting platform only earns its keep
  the day you need one of these **server-side capabilities**:
  1. Querying full history beyond the 200-record feed caps (an API over the database)
  2. User accounts — saved watchlists, per-user notification preferences
  3. On-demand LLM Q&A ("what changed in AKS networking this month?") where the model
     runs at request time, not pipeline time
  4. Push-based ingestion (GitHub webhooks → near-real-time updates instead of 6h polls)
  5. Server-side email/Teams/Slack notification delivery

  None of these are on the current roadmap. RSS, search, per-product pages, digests —
  everything in §6 — works fine as static output. **Don't pay an ops/cost tax for
  capabilities you haven't committed to building.**

### 7.2 Options reviewed

| Option | Monthly cost | Ops burden | What it adds | Verdict |
|---|---|---|---|---|
| **GitHub Pages** (current) | $0 | none | Nothing new; static only. Soft limits (1 GB site, 100 GB bandwidth/mo) are ~1000× above current usage | ✅ **Keep for now** |
| **Azure Static Web Apps — Free tier** | $0 | near-zero | Static hosting + **managed Azure Functions API** + built-in auth (GitHub/Entra) + staging environments per PR; deploys from the same GitHub Actions | ✅ **The upgrade path** when a server-side trigger from §7.1 fires |
| Azure Static Web Apps — Standard | ~$9 | near-zero | SLA, custom auth providers, bigger quotas | Only if Free-tier quotas ever bite |
| Azure Functions (Consumption) alongside Pages | ~$0 (free grant) | low | API endpoints while keeping Pages for static; needs CORS + a second deploy target | Workable, but SWA bundles the same thing more cleanly — skip |
| **Azure Container Apps** (consumption) | ~$0–5 (free grant: 180K vCPU-s, 2M requests/mo) | low-medium | Scale-to-zero containers; custom runtimes, WebSockets, long-running requests | Right choice only if a future backend **outgrows Functions** (e.g. streaming LLM chat over full history). Not before |
| Azure App Service (B1) | ~$13+ | medium | Always-on web server | ❌ Pays for idle 24/7 to serve a site that changes 4×/day — wrong shape |
| Azure Storage static site + Front Door/CDN | ~$1–20 | medium | Static hosting you now operate yourself | ❌ Strictly worse than Pages/SWA here: same capability, more moving parts |
| **AKS / any Kubernetes** | ~$30–70+ (nodes; even with free control plane) | **high** | Cluster upgrades, node patching, ingress, cert management, monitoring… | ❌ **Strongly against.** Kubernetes solves problems this project doesn't have (many services, teams, horizontal scale). Fun irony aside — a tool that *tracks* AKS docs doesn't need to *run* on AKS |

### 7.3 Recommended posture

1. **Today:** stay on GitHub Pages. It is the correct architecture, not a limitation
   you're suffering — the constraint (static, no server) is what keeps this project
   zero-cost and zero-ops, and §6's roadmap (RSS, search, `all.json`, staleness
   indicator) all fits within it.
2. **Define the migration trigger now, not the migration.** Write down: "we move to
   Azure Static Web Apps when we commit to building ⟨history API | accounts |
   on-demand LLM Q&A⟩." The migration itself is cheap when it comes — SWA serves the
   same `docs/` folder, deploys from the same Actions workflow, and the Functions API
   can read NDJSON/SQLite artifacts the pipeline already produces (or graduate the
   data to Azure Blob/Table storage at that point, revisiting §1's hosted-DB rejection
   *only* then).
3. **Kubernetes: no**, at any stage currently foreseeable. If a real always-on backend
   ever emerges, Azure Container Apps gives containers-without-cluster-ops long before
   AKS is justified.

### 7.4 Frontend evolution (independent of hosting)

The no-framework constraint is serving the project well — keep it. Concretely:

- **Near-term** (all within vanilla JS, consolidated from §2/§6): merged `all.json`
  feed so load stays O(1) in product count · staleness indicator (>24h = amber) ·
  partial-feed failure notice · client-side text search (~20 lines) ·
  `aria-live="polite"` on `#status` · URL protocol guard · RSS `<link rel="alternate">`.
- **Structure before framework:** if `app.js` (currently ~700 lines) grows past
  ~1,500 lines or needs real routing (per-product pages beyond hash filters), the
  first step is ES modules (`<script type="module">`, still no build step) — not React.
- **Adopt a build step only when** you need one of: TypeScript across a growing JS
  surface, component reuse across multiple real pages, or a dependency that can't be
  vendored. If that day comes, prefer Vite + something small (Preact/Svelte) over a
  full framework, and note SWA hosts built output just as happily as raw files.
- **What would actually change the frontend calculus** is the on-demand LLM Q&A idea
  (§7.1.3) — a chat-style UI over doc-change history is the one roadmap-plausible
  feature that both justifies SWA (API + auth + rate limiting) and stretches vanilla
  JS. Decide on *that feature*, and the hosting decision falls out of it.

---

## 8. Suggested sequencing

1. **Now (bugs, ~1 day):** cursor fixes (§2.1) · publish LLM summaries (§2.2, one
   line) · `git pull --rebase` + timeout in workflow (§2.3) · failure-issue step +
   non-zero exits (§2.4).
2. **Next (storage + LLM, ~2–3 days):** persist derived fields at triage time; migrate
   `data/` to NDJSON + `state.json`, stop committing the .db · `summary_source` column
   + retry semantics · Batches API + structured output + injection delimiters.
3. **Then (scale + product):** ETag conditional requests · merged `all.json` feed ·
   labeled triage sample + retriage flag · RSS feed · doc-drift cleanup.
4. **Hosting (no action now):** stay on GitHub Pages; record the §7.3 migration
   trigger in README/ARCHITECTURE so the move to Azure Static Web Apps is a planned
   step, not a scramble, if a server-side feature gets committed.

## What's already in good shape

Worth stating: the XSS discipline in `app.js` is exemplary (textContent everywhere, no
innerHTML); workflow `concurrency` group and minimal `contents: write` permission are
correct; oldest-first commit processing so `--max-commits` can't reorder-skip within a
fetched page is thoughtful; feeds' two-pass stable sort for `top_changes` matches the
contract exactly; and the overall repo-as-pipeline, no-server shape is the right
architecture for this project — none of the findings above require abandoning it.
