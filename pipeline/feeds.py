"""Feeds stage: generate the JSON feeds the website consumes.

Writes, exactly per CONTRACT.md:
  docs/data/products.json
  docs/data/<product-id>.json   (non-noise records, newest-first, cap 200)
  docs/data/summary.json        (7-day window rollup)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

try:
    import db
    import derived
except ImportError:  # pragma: no cover
    from pipeline import db
    from pipeline import derived

FEED_CAP = 200
WINDOW_DAYS = 7
ALL_KINDS = ["new-feature", "ga", "preview", "deprecation", "breaking-change", "doc-update"]
KIND_WEIGHT = {"breaking-change": 5, "deprecation": 4, "ga": 3,
               "new-feature": 2, "preview": 1, "doc-update": 0}
TOP_CHANGES_CAP = 8


def page_change_category(files, reasons) -> str:
    return derived.page_change_category(files, reasons)


def _batch_key(category: str, product: str, summary: str, title: str) -> str:
    return derived.batch_key(category, product, summary, title)


def _optional_row_text(row, key: str) -> str | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _record_to_json(row) -> dict:
    reasons = row.get("reasons") or []
    files = row.get("files") or []
    doc_urls = row.get("doc_urls") or []
    change_summary = row.get("change_summary") or row.get("summary") or ""
    category = row.get("page_change_category") or "existing-page"
    batch_key = row.get("batch_key") or _batch_key(
        category,
        row["product"],
        change_summary,
        row.get("title") or "",
    )
    record = {
        "id": row["id"],
        "product": row["product"],
        "date": row["date"],
        "kind": row["kind"] or "doc-update",
        "title": row["title"] or "",
        "summary": change_summary,
        "change_summary": change_summary,
        "page_change_category": category,
        "batch_key": batch_key,
        "reasons": reasons,
        "files": files,
        "doc_urls": doc_urls,
        "commit_url": row["commit_url"],
        "sha": (row["sha"] or "")[:8],
    }
    author_login = _optional_row_text(row, "author_login")
    author_name = _optional_row_text(row, "author_name")
    if author_login:
        record["author"] = author_login
    if author_name:
        record["author_name"] = author_name
    return record


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _strip_generated_at(value):
    if isinstance(value, dict):
        return {
            key: _strip_generated_at(child)
            for key, child in value.items()
            if key != "generated_at"
        }
    if isinstance(value, list):
        return [_strip_generated_at(child) for child in value]
    return value


def _write_json_if_changed(path: str, payload: dict) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = None
    if existing is not None and _strip_generated_at(existing) == _strip_generated_at(payload):
        return False
    _write_json(path, payload)
    return True


def build_payloads(conn, all_products, generated_at: str | None = None,
                   now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    generated_at = generated_at or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    payloads: dict[str, dict] = {
        "products": {
            "generated_at": generated_at,
            "products": [{"id": p["id"], "name": p["name"]} for p in all_products],
        }
    }

    all_records: list[dict] = []
    for product in all_products:
        rows = db.signal_records(conn, product["id"])
        records = [_record_to_json(row) for row in rows]
        all_records.extend(records)
        payloads[product["id"]] = {
            "product": product["id"],
            "generated_at": generated_at,
            "records": records[:FEED_CAP],
        }

    window_start = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    window = [record for record in all_records if record["date"] >= window_start]

    counts_by_kind = {kind: 0 for kind in ALL_KINDS}
    for record in window:
        counts_by_kind[record["kind"]] = counts_by_kind.get(record["kind"], 0) + 1

    counts_by_product = {product["id"]: 0 for product in all_products}
    for record in window:
        counts_by_product[record["product"]] = counts_by_product.get(record["product"], 0) + 1

    top_changes = sorted(window, key=lambda record: record["date"], reverse=True)
    top_changes = sorted(
        top_changes,
        key=lambda record: KIND_WEIGHT.get(record["kind"], 0),
        reverse=True,
    )[:TOP_CHANGES_CAP]

    payloads["summary"] = {
        "generated_at": generated_at,
        "window_days": WINDOW_DAYS,
        "counts_by_kind": counts_by_kind,
        "counts_by_product": counts_by_product,
        "top_changes": top_changes,
        "total_records": len(window),
    }
    return payloads


def run(products=None) -> dict:
    conn = db.connect()
    all_products = db.load_products()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payloads = build_payloads(conn, all_products, generated_at)

    counters = {"files_written": 0, "records_total": 0}
    if _write_json_if_changed(os.path.join(db.DOCS_DATA_DIR, "products.json"), payloads["products"]):
        counters["files_written"] += 1

    for product in all_products:
        payload = payloads[product["id"]]
        records = payload["records"]
        rows_total = len(db.signal_records(conn, product["id"]))
        if _write_json_if_changed(os.path.join(db.DOCS_DATA_DIR, f"{product['id']}.json"), payload):
            counters["files_written"] += 1
        counters["records_total"] += rows_total
        print(f"[feeds] {product['id']}.json: {len(records)} records ({rows_total} total non-noise)")

    if _write_json_if_changed(os.path.join(db.DOCS_DATA_DIR, "summary.json"), payloads["summary"]):
        counters["files_written"] += 1
    print(f"[feeds] summary.json: {payloads['summary']['total_records']} records in "
          f"{WINDOW_DAYS}-day window, {len(payloads['summary']['top_changes'])} top changes")

    conn.close()
    return counters


if __name__ == "__main__":
    run()
