"""Ingest stage: pull path-scoped commits from GitHub into SQLite.

For each product in products.yml:
  GET /repos/{repo}/commits?path={path}&since={cursor}&per_page=100  (paginated)
then for each unseen, non-merge commit:
  GET /repos/{repo}/commits/{sha}  for file-level patch data.

Uses urllib only. Auth via GITHUB_TOKEN env if present, else unauthenticated.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import db
except ImportError:  # pragma: no cover - package-style invocation
    from pipeline import db

API = "https://api.github.com"
USER_AGENT = "LearnPulse-pipeline (+https://github.com/nthewara/LearnPulse)"
PATCH_CAP_PER_FILE = 4000  # chars of patch stored per file

# Sync/merge automation commits: duplicates of content that arrives via other
# commits — skip the expensive detail fetch entirely.
MERGE_SKIP_RES = [
    re.compile(r"^Merging changes synced from "),
    re.compile(r"^Merge pull request #\d+ from MicrosoftDocs/(main|live)\b"),
]

_rate_limited = False  # set when X-RateLimit-Remaining hits the floor


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(url: str):
    """GET a GitHub API URL. Returns (json_body, headers) or (None, None) on
    rate-limit exhaustion (sets the module flag)."""
    global _rate_limited
    if _rate_limited:
        return None, None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) <= 2:
                print(f"  [ingest] rate limit nearly exhausted (remaining={remaining}); "
                      "stopping further API calls this run", file=sys.stderr)
                _rate_limited = True
            return body, resp.headers
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            reset = exc.headers.get("X-RateLimit-Reset", "?")
            print(f"  [ingest] HTTP {exc.code} (rate limited?) for {url}; "
                  f"reset={reset}; stopping API calls this run", file=sys.stderr)
            _rate_limited = True
            return None, None
        print(f"  [ingest] HTTP {exc.code} for {url}: {exc.reason}", file=sys.stderr)
        return None, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  [ingest] request failed for {url}: {exc}", file=sys.stderr)
        return None, None


def _next_link(headers) -> str | None:
    link = headers.get("Link") if headers else None
    if not link:
        return None
    for part in link.split(","):
        section = part.split(";")
        if len(section) >= 2 and 'rel="next"' in section[1]:
            return section[0].strip().strip("<>")
    return None


def is_merge_skip(message: str) -> bool:
    first_line = (message or "").splitlines()[0] if message else ""
    return any(rx.search(first_line) for rx in MERGE_SKIP_RES)


def list_commits(repo: str, path: str, since_iso: str) -> list[dict]:
    """List commits touching `path` since `since_iso`, following pagination."""
    params = urllib.parse.urlencode(
        {"path": path, "since": since_iso, "per_page": 100}
    )
    url = f"{API}/repos/{repo}/commits?{params}"
    out: list[dict] = []
    while url:
        body, headers = _request(url)
        if body is None:
            break
        out.extend(body)
        url = _next_link(headers)
    return out


_detail_cache: dict[tuple[str, str], dict | None] = {}


def fetch_commit_detail(repo: str, sha: str) -> dict | None:
    key = (repo, sha)
    if key not in _detail_cache:
        body, _ = _request(f"{API}/repos/{repo}/commits/{sha}")
        _detail_cache[key] = body
    return _detail_cache[key]


def build_patch_summary(detail: dict, path_prefix: str) -> tuple[str, list[str]]:
    """Return (raw_patch_summary JSON string, product-path file list)."""
    all_files = detail.get("files") or []
    prefix = path_prefix.rstrip("/") + "/"
    files_out = []
    product_files = []
    for f in all_files:
        name = f.get("filename", "")
        prev = f.get("previous_filename")
        if not (name.startswith(prefix) or (prev and prev.startswith(prefix))):
            continue
        product_files.append(name)
        files_out.append({
            "filename": name,
            "previous_filename": prev,
            "status": f.get("status"),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch": (f.get("patch") or "")[:PATCH_CAP_PER_FILE],
        })
    summary = {
        "total_files_in_commit": len(all_files),
        "files": files_out,
    }
    return json.dumps(summary, ensure_ascii=False), product_files


def ingest_product(conn, product: dict, since_days: int | None,
                   max_commits: int | None) -> dict:
    """Ingest one product. Returns counters."""
    pid = product["id"]
    repo = product["repo"]
    path = product["path"]

    cursor = None if since_days is not None else db.get_cursor(conn, pid)
    if cursor is None:
        days = since_days if since_days is not None else 30
        cursor = (datetime.now(timezone.utc) - timedelta(days=days)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[ingest] {pid}: listing commits in {repo}/{path} since {cursor}")

    commits = list_commits(repo, path, cursor)
    # API returns newest-first; process oldest-first so that if --max-commits
    # truncates the run, the cursor only advances past processed commits.
    commits.reverse()

    counters = {"listed": len(commits), "new": 0, "merge_skipped": 0,
                "detail_fetched": 0, "capped": 0}
    detail_budget = max_commits
    max_seen_committer_date = None

    for c in commits:
        sha = c.get("sha")
        if not sha:
            continue
        commit_meta = c.get("commit") or {}
        committer_date = ((commit_meta.get("committer") or {}).get("date")
                          or (commit_meta.get("author") or {}).get("date"))
        author_date = ((commit_meta.get("author") or {}).get("date")
                       or committer_date or _now_iso())
        message = commit_meta.get("message") or ""

        if db.is_seen(conn, sha, pid):
            if committer_date and (max_seen_committer_date is None
                                   or committer_date > max_seen_committer_date):
                max_seen_committer_date = committer_date
            continue

        if is_merge_skip(message):
            db.mark_seen(conn, sha, pid, _now_iso())
            counters["merge_skipped"] += 1
            if committer_date and (max_seen_committer_date is None
                                   or committer_date > max_seen_committer_date):
                max_seen_committer_date = committer_date
            continue

        if detail_budget is not None and detail_budget <= 0:
            counters["capped"] += 1
            continue  # do NOT mark seen / advance cursor past this commit

        detail = fetch_commit_detail(repo, sha)
        if detail is None:
            # rate limited or transient failure — leave for next run
            break
        if detail_budget is not None:
            detail_budget -= 1
        counters["detail_fetched"] += 1

        raw_patch_summary, product_files = build_patch_summary(detail, path)
        record_id = db.next_record_id(conn, sha)
        db.insert_raw_record(
            conn,
            record_id=record_id,
            product=pid,
            date=(author_date or _now_iso())[:10],
            commit_url=f"https://github.com/{repo}/commit/{sha}",
            sha=sha,
            created_at=_now_iso(),
            raw_commit_message=message,
            raw_patch_summary=raw_patch_summary,
        )
        db.mark_seen(conn, sha, pid, _now_iso())
        counters["new"] += 1
        if committer_date and (max_seen_committer_date is None
                               or committer_date > max_seen_committer_date):
            max_seen_committer_date = committer_date

    if max_seen_committer_date:
        db.set_cursor(conn, pid, max_seen_committer_date)
    conn.commit()
    print(f"[ingest] {pid}: listed={counters['listed']} new={counters['new']} "
          f"merge-skipped={counters['merge_skipped']} "
          f"detail-fetched={counters['detail_fetched']} capped={counters['capped']}")
    return counters


def run(products=None, since_days: int | None = None,
        max_commits: int | None = None) -> dict:
    conn = db.connect()
    all_products = db.load_products()
    if products:
        all_products = [p for p in all_products if p["id"] in products]
    totals = {}
    for product in all_products:
        totals[product["id"]] = ingest_product(conn, product, since_days, max_commits)
        if _rate_limited:
            print("[ingest] stopping product loop: rate limited", file=sys.stderr)
            break
    conn.close()
    return totals


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="LearnPulse ingest stage")
    ap.add_argument("--since-days", type=int, default=None)
    ap.add_argument("--max-commits", type=int, default=None)
    ap.add_argument("--products", type=str, default=None,
                    help="comma-separated product ids")
    args = ap.parse_args()
    prods = args.products.split(",") if args.products else None
    run(products=prods, since_days=args.since_days, max_commits=args.max_commits)
