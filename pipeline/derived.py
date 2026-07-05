"""Deterministic helpers for capped patch excerpts and display fields."""
from __future__ import annotations

import json
import re

PATCH_EXCERPT_MAX_CHARS = 2048
PATCH_CAP_PER_FILE = 4000

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


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _payload(total_files: int, files: list[dict]) -> dict:
    return {"total_files_in_commit": total_files, "files": files}


def _normalize_file(file_info: dict) -> dict:
    normalized = {
        "filename": file_info.get("filename", ""),
        "status": file_info.get("status"),
        "additions": file_info.get("additions", 0),
        "deletions": file_info.get("deletions", 0),
        "patch": file_info.get("patch") or "",
    }
    previous = file_info.get("previous_filename")
    if previous:
        normalized["previous_filename"] = previous
    return normalized


def cap_patch_excerpt(summary, max_chars: int = PATCH_EXCERPT_MAX_CHARS) -> str:
    """Return a valid JSON patch excerpt capped to max_chars."""
    if isinstance(summary, str):
        try:
            summary = json.loads(summary or "{}")
        except json.JSONDecodeError:
            return _json(_payload(0, []))[:max_chars]
    if not isinstance(summary, dict):
        summary = {}

    source_files = [_normalize_file(f) for f in summary.get("files") or [] if isinstance(f, dict)]
    total_files = int(summary.get("total_files_in_commit") or len(source_files))

    included: list[dict] = []
    for source in source_files:
        meta = dict(source)
        meta["patch"] = ""
        candidate = included + [meta]
        if len(_json(_payload(total_files, candidate))) <= max_chars:
            included.append(meta)
        else:
            break

    for index, source in enumerate(source_files[:len(included)]):
        patch = (source.get("patch") or "")[:PATCH_CAP_PER_FILE]
        if not patch:
            continue
        lo, hi = 0, len(patch)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = [dict(f) for f in included]
            candidate[index]["patch"] = patch[:mid]
            if len(_json(_payload(total_files, candidate))) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        included[index]["patch"] = patch[:lo]

    return _json(_payload(total_files, included))[:max_chars]


def patch_payload(patch_excerpt: str | None) -> dict:
    try:
        payload = json.loads(patch_excerpt or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def patch_files(patch_excerpt: str | None) -> list[dict]:
    files = patch_payload(patch_excerpt).get("files") or []
    return [f for f in files if isinstance(f, dict)]


def patch_text(patch_excerpt: str | None, cap: int = PATCH_CAP_PER_FILE) -> str:
    chunks = []
    for file_info in patch_files(patch_excerpt):
        patch = file_info.get("patch") or ""
        if patch:
            chunks.append(
                f"--- {file_info.get('filename')} ({file_info.get('status')}) ---\n{patch}"
            )
    return "\n\n".join(chunks)[:cap]


def _clean_markdown_text(text: str) -> str:
    text = LINK_RE.sub(r"\1", text or "")
    text = EMOJI_TOKEN_RE.sub(" ", text)
    text = MARKDOWN_TOKEN_RE.sub("", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t-:;\"'")


def sentence(text: str) -> str:
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
    return sentence(text[:220].rstrip())


def added_change_summaries(patch_excerpt: str | None, limit: int = 3) -> list[str]:
    summaries = []
    seen = set()
    for file_info in patch_files(patch_excerpt):
        for line in (file_info.get("patch") or "").splitlines():
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


def _markdown_page(filename: str) -> bool:
    low = (filename or "").lower()
    return low.endswith(".md") and "/includes/" not in low and "/media/" not in low


def _markdown_pages(files: list[dict]) -> list[dict]:
    return [f for f in files if _markdown_page(f.get("filename", ""))]


def doc_change_summary(row) -> str:
    patch_excerpt = row.get("patch_excerpt") or ""
    files = patch_files(patch_excerpt)
    pages = _markdown_pages(files)
    added = added_change_summaries(patch_excerpt, limit=2)
    removed_pages = [f for f in pages if f.get("status") == "removed"]
    new_pages = [f for f in pages if f.get("status") == "added"]

    if added:
        if added[0].startswith("Applies-to matrix"):
            return added[0]
        return " ".join(added)
    if removed_pages:
        return (
            "Removed retired documentation page."
            if len(removed_pages) == 1
            else f"Removed {len(removed_pages)} retired documentation pages."
        )
    if new_pages:
        return (
            "Added a new documentation page."
            if len(new_pages) == 1
            else f"Added {len(new_pages)} new documentation pages."
        )
    if pages:
        return (
            "Updated documentation page."
            if len(pages) == 1
            else f"Updated {len(pages)} documentation pages."
        )
    return sentence((row.get("title") or "Documentation update").rstrip("."))


def page_change_category(files, reasons) -> str:
    reasons = reasons or []
    files = files or []

    for file_info in files:
        if isinstance(file_info, dict):
            if file_info.get("status") == "added" and _markdown_page(file_info.get("filename", "")):
                return "new-page"

    if "new-file" in reasons:
        for file_info in files:
            filename = file_info.get("filename", "") if isinstance(file_info, dict) else str(file_info)
            if _markdown_page(filename):
                return "new-page"

    for file_info in files:
        if isinstance(file_info, dict):
            if file_info.get("status") in {"modified", "renamed"} and _markdown_page(file_info.get("filename", "")):
                return "existing-page"
        elif _markdown_page(str(file_info)):
            return "existing-page"

    return "existing-page"


def batch_key(category: str, product: str, summary: str, title: str) -> str:
    base = summary or title or "documentation update"
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in base)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return f"{category}:{product}:{normalized[:80]}"


def compute_display_fields(row) -> dict:
    reasons = row.get("reasons") or []
    files_for_category = patch_files(row.get("patch_excerpt")) or row.get("files") or []
    category = page_change_category(files_for_category, reasons)
    change_summary = doc_change_summary(row)
    return {
        "change_summary": change_summary,
        "page_change_category": category,
        "batch_key": batch_key(
            category,
            row.get("product") or "",
            change_summary,
            row.get("title") or "",
        ),
    }
