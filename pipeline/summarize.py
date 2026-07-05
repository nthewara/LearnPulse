"""Summarize stage: LLM (Claude Haiku) summaries for non-noise records.

If ANTHROPIC_API_KEY is set, calls the Anthropic Messages API via urllib and
asks for strict JSON {"kind", "title", "summary"}. On any error — or with no
key — falls back to heuristic values. This stage must NEVER fail the pipeline.

Only records with an empty/null summary are processed (batch-friendly).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

try:
    import db
except ImportError:  # pragma: no cover
    from pipeline import db

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300
PATCH_CHAR_CAP = 4000

KINDS = {"new-feature", "ga", "preview", "deprecation", "breaking-change", "doc-update"}

PROMPT_TEMPLATE = """\
You classify Microsoft Learn documentation commits for the Azure product "{product}".
Given the commit message and diff excerpts below, decide what product capability
changed (if any) and respond with ONLY a JSON object, no other text:

{{"kind": "<one of: new-feature, ga, preview, deprecation, breaking-change, doc-update>",
 "title": "<human-readable title, max 100 chars>",
 "summary": "<1-2 sentences describing what product capability changed>"}}

Commit message:
{message}

Diff excerpts (truncated):
{patch}
"""

META_OR_MARKUP_RE = re.compile(
    r"^\s*(ms\.[a-z.]+|author|title|description|manager)\s*:|^\s*[#\-|>{{\[]|^\s*$",
    re.IGNORECASE,
)
MARKDOWN_TOKEN_RE = re.compile(r"[*_`]+|</?[^>]+>|\{#[^}]+\}")
LINK_RE = re.compile(r"!?\[([^\]]+)\]\([^)]+\)")
EMOJI_TOKEN_RE = re.compile(r":[a-z0-9_+-]+:")
APPLIES_TO_RE = re.compile(r"applies\s+to", re.IGNORECASE)
NOISE_PHRASES = (
    "learn.microsoft.com",
    "aka.ms/",
    "github.com/",
    "http://",
    "https://",
)


def _patch_excerpt(raw_patch_summary: str) -> str:
    try:
        data = json.loads(raw_patch_summary or "{}")
    except json.JSONDecodeError:
        return ""
    chunks = []
    for f in data.get("files") or []:
        patch = f.get("patch") or ""
        if patch:
            chunks.append(f"--- {f.get('filename')} ({f.get('status')}) ---\n{patch}")
    return "\n\n".join(chunks)[:PATCH_CHAR_CAP]


def _clean_markdown_text(text: str) -> str:
    text = LINK_RE.sub(r"\1", text or "")
    text = EMOJI_TOKEN_RE.sub(" ", text)
    text = MARKDOWN_TOKEN_RE.sub("", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t-:;\"'")


def _sentence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = text.replace(",.", ".")
    text = text.rstrip(" ,;:")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _applies_to_summary(text: str) -> str | None:
    if not APPLIES_TO_RE.search(text):
        return None
    products = []
    for label in ("AKS Automatic", "AKS Standard"):
        if label.lower() in text.lower():
            products.append(label)
    if not products:
        return "Applies-to matrix was updated."
    if len(products) == 1:
        product_list = products[0]
    else:
        product_list = " and ".join([", ".join(products[:-1]), products[-1]]).strip()
    return f"Applies-to matrix now includes {product_list}."


def _summary_from_added_text(text: str) -> str | None:
    applies_to = _applies_to_summary(text)
    if applies_to:
        return applies_to

    stripped = text.lstrip()
    if stripped.startswith(":::"):
        return None
    if stripped.startswith("|"):
        return "Updated documentation table."
    text = _clean_markdown_text(text)
    if len(text) < 20:
        return None
    low = text.lower()
    if any(phrase in low for phrase in NOISE_PHRASES):
        return None
    if META_OR_MARKUP_RE.match(text):
        return None
    if re.fullmatch(r"[-=|:\s]+", text):
        return None
    return _sentence(text[:220].rstrip())


def added_change_summaries(raw_patch_summary: str, limit: int = 3) -> list[str]:
    try:
        data = json.loads(raw_patch_summary or "{}")
    except json.JSONDecodeError:
        return []
    summaries = []
    seen = set()
    for f in data.get("files") or []:
        for line in (f.get("patch") or "").splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            summary = _summary_from_added_text(line[1:].strip())
            if not summary:
                continue
            key = summary.lower()
            if key in seen:
                continue
            summaries.append(summary)
            seen.add(key)
            if len(summaries) >= limit:
                return summaries
    return summaries


def _patch_files(raw_patch_summary: str) -> list[dict]:
    try:
        data = json.loads(raw_patch_summary or "{}")
    except json.JSONDecodeError:
        return []
    return data.get("files") or []


def _markdown_pages(files: list[dict]) -> list[dict]:
    pages = []
    for f in files:
        name = (f.get("filename") or "").lower()
        if name.endswith(".md") and "/includes/" not in name and "/media/" not in name:
            pages.append(f)
    return pages


def doc_change_summary(row) -> str:
    """Return a reader-facing summary of what changed in the documentation.

    This intentionally describes the doc delta rather than repeating the commit
    title. It is deterministic so feeds can be regenerated without an LLM key.
    """
    raw_patch_summary = row["raw_patch_summary"] or ""
    files = _patch_files(raw_patch_summary)
    pages = _markdown_pages(files)
    added = added_change_summaries(raw_patch_summary, limit=2)
    removed_pages = [f for f in pages if f.get("status") == "removed"]
    new_pages = [f for f in pages if f.get("status") == "added"]

    if added:
        if added[0].startswith("Applies-to matrix"):
            return added[0]
        return " ".join(added)
    if removed_pages:
        return "Removed retired documentation page." if len(removed_pages) == 1 \
            else f"Removed {len(removed_pages)} retired documentation pages."
    if new_pages:
        return "Added a new documentation page." if len(new_pages) == 1 \
            else f"Added {len(new_pages)} new documentation pages."
    if pages:
        return "Updated documentation page." if len(pages) == 1 \
            else f"Updated {len(pages)} documentation pages."
    return _sentence((row["title"] or "Documentation update").rstrip("."))


def heuristic_summary(row) -> str:
    return doc_change_summary(row)


def _parse_llm_json(text: str) -> dict | None:
    """Strictly parse the model reply into {kind, title, summary}."""
    text = text.strip()
    # tolerate accidental code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    kind = data.get("kind")
    title = data.get("title")
    summary = data.get("summary")
    if kind not in KINDS:
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None
    return {"kind": kind, "title": title.strip()[:100], "summary": summary.strip()}


def llm_summarize(row, product_name: str, api_key: str) -> dict | None:
    """Call the Anthropic Messages API. Returns parsed dict or None on any error."""
    prompt = PROMPT_TEMPLATE.format(
        product=product_name,
        message=(row["raw_commit_message"] or "")[:2000],
        patch=_patch_excerpt(row["raw_patch_summary"]) or "(no patch available)",
    )
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        blocks = body.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return _parse_llm_json(text)
    except Exception as exc:  # noqa: BLE001 — never fail the pipeline
        print(f"  [summarize] LLM call failed for {row['id']}: {exc}", file=sys.stderr)
        return None


def run(products=None) -> dict:
    conn = db.connect()
    prod_names = {p["id"]: p["name"] for p in db.load_products()}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    rows = db.unsummarized_records(conn)
    counters = {"summarized": 0, "llm": 0, "heuristic": 0}
    llm_disabled = False  # trip breaker after repeated failures

    for row in rows:
        if products and row["product"] not in products:
            continue
        result = None
        if api_key and not llm_disabled:
            result = llm_summarize(row, prod_names.get(row["product"], row["product"]),
                                   api_key)
            if result is None and counters["llm"] == 0 and counters["heuristic"] >= 3:
                # first 3+ calls all failed — likely bad key/network; stop trying
                llm_disabled = True
        if result:
            db.set_summary(conn, row["id"], kind=result["kind"],
                           title=result["title"], summary=result["summary"])
            counters["llm"] += 1
        else:
            db.set_summary(conn, row["id"], kind=row["kind"], title=row["title"],
                           summary=heuristic_summary(row))
            counters["heuristic"] += 1
        counters["summarized"] += 1

    conn.commit()
    conn.close()
    print(f"[summarize] summarized={counters['summarized']} "
          f"llm={counters['llm']} heuristic={counters['heuristic']} "
          f"(api_key={'set' if api_key else 'absent'})")
    return counters


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="LearnPulse summarize stage")
    ap.add_argument("--products", type=str, default=None)
    args = ap.parse_args()
    run(products=args.products.split(",") if args.products else None)
