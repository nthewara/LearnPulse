"""Triage stage: rule-based classification of ingested commits.

Marks noise (is_noise=1) for editorial churn and assigns kind + reason codes
to signal candidates. One ChangeRecord per commit per product.
"""
from __future__ import annotations

import json
import re

try:
    import db
except ImportError:  # pragma: no cover
    from pipeline import db

KINDS = ("new-feature", "ga", "preview", "deprecation", "breaking-change", "doc-update")

# --- noise patterns ---------------------------------------------------------
META_LINE_RE = re.compile(
    r"^[+-]\s*(ms\.date|ms\.author|ms\.custom|author)\s*:", re.IGNORECASE
)
TYPO_MSG_RE = re.compile(r"typo|broken link|fix link|formatting", re.IGNORECASE)
ACROLINX_MSG_RE = re.compile(r"acrolinx|style\s*guide\s*sweep|acro\s*sweep", re.IGNORECASE)
BULK_MSG_RE = re.compile(r"metadata|freshness|sweep|bulk", re.IGNORECASE)
IMAGE_PATH_RE = re.compile(r"(media/|\.(png|jpe?g|gif|svg))", re.IGNORECASE)
IMAGE_FILE_RE = re.compile(r"\.(png|jpe?g|gif|svg)$", re.IGNORECASE)

# --- signal patterns --------------------------------------------------------
GA_RE = re.compile(r"generally available|now ga\b", re.IGNORECASE)
GA_PAREN_RE = re.compile(r"\(GA\)")
PREVIEW_RE = re.compile(r"preview", re.IGNORECASE)
DEPRECATION_RE = re.compile(
    r"deprecat|retire|end of support|will be unsupported", re.IGNORECASE
)
BREAKING_RE = re.compile(r"breaking change", re.IGNORECASE)
TITLE_LINE_RE = re.compile(r"^\+\s*title\s*:\s*(.+)$", re.IGNORECASE)
PR_SUFFIX_RE = re.compile(r"\s*\(#\d+\)\s*$")

SMALL_DIFF_LINES = 30  # threshold for "small diff" in typo/link noise rule
BULK_FILE_COUNT = 30


def _changed_lines(patch: str):
    """Yield diff content lines (+/-), excluding file headers and hunk marks."""
    for line in (patch or "").splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            yield line


def _added_lines(patch: str):
    for line in (patch or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            yield line


def clean_title(message: str) -> str:
    first = (message or "").splitlines()[0].strip() if message else ""
    first = PR_SUFFIX_RE.sub("", first)
    return first[:100] if first else "(untitled change)"


def doc_url_for(filename: str, product: dict) -> str | None:
    """Map a repo file path to its published Learn URL, or None if unmappable."""
    path_prefix = product["path"].rstrip("/") + "/"
    if not filename.startswith(path_prefix):
        return None
    rel = filename[len(path_prefix):]
    low = rel.lower()
    if not low.endswith(".md"):
        return None
    if "includes/" in low or "media/" in low or low.endswith("toc.yml"):
        return None
    return product["learn_base"].rstrip("/") + "/" + rel[:-3]


def classify(record, product: dict) -> dict:
    """Classify one raw record. Returns dict of triage fields."""
    message = record["raw_commit_message"] or ""
    try:
        patch_summary = json.loads(record["raw_patch_summary"] or "{}")
    except json.JSONDecodeError:
        patch_summary = {}
    files = patch_summary.get("files") or []
    total_files = patch_summary.get("total_files_in_commit", len(files))

    filenames = [f.get("filename", "") for f in files]
    changed = []
    added = []
    total_line_changes = 0
    for f in files:
        patch = f.get("patch") or ""
        cl = list(_changed_lines(patch))
        changed.extend(cl)
        added.extend(_added_lines(patch))
        total_line_changes += f.get("additions", 0) + f.get("deletions", 0)

    reasons: list[str] = []
    noise_reason = None

    # ---------------- noise rules ----------------
    # 1. metadata-only sweep: every changed line is a frontmatter metadata line
    if changed and all(META_LINE_RE.match(l) for l in changed):
        noise_reason = "metadata-only"
    # 2. typo / link-fix / formatting message with a small diff
    elif TYPO_MSG_RE.search(message) and total_line_changes <= SMALL_DIFF_LINES:
        noise_reason = "typo-or-linkfix"
    # 3. image-only changes: all files are images, or every changed line is an image path
    elif filenames and all(IMAGE_FILE_RE.search(n) or "/media/" in n for n in filenames):
        noise_reason = "image-only"
    elif changed and all(IMAGE_PATH_RE.search(l) for l in changed):
        noise_reason = "image-path-change"
    # 4. acrolinx / style sweeps
    elif ACROLINX_MSG_RE.search(message):
        noise_reason = "style-sweep"
    # 5. bulk metadata/freshness commits
    elif total_files > BULK_FILE_COUNT and BULK_MSG_RE.search(message):
        noise_reason = "bulk-sweep"

    if noise_reason:
        return {
            "kind": "doc-update",
            "title": clean_title(message),
            "reasons": [noise_reason],
            "files": filenames,
            "doc_urls": [],
            "is_noise": 1,
        }

    # ---------------- signal rules ----------------
    new_md = [f for f in files
              if f.get("status") == "added" and f.get("filename", "").endswith(".md")]
    removed_md = [f for f in files
                  if f.get("status") == "removed" and f.get("filename", "").endswith(".md")]

    added_text = "\n".join(added)
    new_file_titles = " ".join(
        m.group(1) for f in new_md
        for line in (f.get("patch") or "").splitlines()
        if (m := TITLE_LINE_RE.match(line))
    )

    has_breaking = bool(BREAKING_RE.search(added_text) or BREAKING_RE.search(message))
    has_deprecation_kw = bool(DEPRECATION_RE.search(added_text))
    has_ga = bool(GA_RE.search(added_text) or GA_PAREN_RE.search(added_text))
    has_preview = bool(PREVIEW_RE.search(added_text) or PREVIEW_RE.search(new_file_titles))

    if new_md:
        reasons.append("new-file")
    if removed_md:
        reasons.append("retired-page")
    if has_ga:
        reasons.append("keyword:ga")
    if has_preview:
        reasons.append("keyword:preview")
    if has_deprecation_kw:
        reasons.append("keyword:deprecation")
    if has_breaking:
        reasons.append("keyword:breaking-change")

    # TOC additions
    for f in files:
        name = f.get("filename", "").lower()
        if name.endswith("toc.yml"):
            if any(("name:" in l or "href:" in l) for l in _added_lines(f.get("patch") or "")):
                reasons.append("toc-entry-added")
                break

    # markdown table row edits
    if any(l[1:].lstrip().startswith("|") for l in changed):
        reasons.append("table-edit")

    # kind priority: breaking > deprecation > ga > new-feature > preview > doc-update
    if has_breaking:
        kind = "breaking-change"
    elif removed_md or has_deprecation_kw:
        kind = "deprecation"
    elif has_ga:
        kind = "ga"
    elif new_md:
        kind = "new-feature"
    elif has_preview:
        kind = "preview"
    else:
        kind = "doc-update"

    if not reasons:
        reasons.append("doc-update")

    doc_urls = []
    for f in files:
        if f.get("status") == "removed":
            continue  # page no longer published
        url = doc_url_for(f.get("filename", ""), product)
        if url and url not in doc_urls:
            doc_urls.append(url)

    return {
        "kind": kind,
        "title": clean_title(message),
        "reasons": reasons,
        "files": filenames,
        "doc_urls": doc_urls,
        "is_noise": 0,
    }


def run(products=None) -> dict:
    conn = db.connect()
    prod_by_id = {p["id"]: p for p in db.load_products()}
    rows = db.untriaged_records(conn)
    counters = {"triaged": 0, "noise": 0, "signal": 0}
    for row in rows:
        if products and row["product"] not in products:
            continue
        product = prod_by_id.get(row["product"])
        if product is None:
            continue
        result = classify(row, product)
        db.apply_triage(
            conn, row["id"],
            kind=result["kind"],
            title=result["title"],
            reasons_json=json.dumps(result["reasons"]),
            files_json=json.dumps(result["files"]),
            doc_urls_json=json.dumps(result["doc_urls"]),
            is_noise=result["is_noise"],
        )
        counters["triaged"] += 1
        counters["noise" if result["is_noise"] else "signal"] += 1
    conn.commit()
    conn.close()
    print(f"[triage] triaged={counters['triaged']} "
          f"signal={counters['signal']} noise={counters['noise']}")
    return counters


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="LearnPulse triage stage")
    ap.add_argument("--products", type=str, default=None)
    args = ap.parse_args()
    run(products=args.products.split(",") if args.products else None)
