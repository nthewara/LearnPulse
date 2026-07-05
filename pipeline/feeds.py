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
except ImportError:  # pragma: no cover
    from pipeline import db

FEED_CAP = 200
WINDOW_DAYS = 7
ALL_KINDS = ["new-feature", "ga", "preview", "deprecation", "breaking-change",
             "doc-update"]
KIND_WEIGHT = {"breaking-change": 5, "deprecation": 4, "ga": 3,
               "new-feature": 2, "preview": 1, "doc-update": 0}
TOP_CHANGES_CAP = 8


def _record_to_json(row) -> dict:
    return {
        "id": row["id"],
        "product": row["product"],
        "date": row["date"],
        "kind": row["kind"] or "doc-update",
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "reasons": json.loads(row["reasons_json"] or "[]"),
        "files": json.loads(row["files_json"] or "[]"),
        "doc_urls": json.loads(row["doc_urls_json"] or "[]"),
        "commit_url": row["commit_url"],
        "sha": (row["sha"] or "")[:8],
    }


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def run(products=None) -> dict:
    conn = db.connect()
    all_products = db.load_products()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # products.json — always lists the full watchlist
    _write_json(os.path.join(db.DOCS_DATA_DIR, "products.json"), {
        "generated_at": generated_at,
        "products": [{"id": p["id"], "name": p["name"]} for p in all_products],
    })

    counters = {"files_written": 1, "records_total": 0}

    # per-product feeds
    all_records: list[dict] = []
    for p in all_products:
        rows = db.signal_records(conn, p["id"])
        records = [_record_to_json(r) for r in rows]
        all_records.extend(records)
        _write_json(os.path.join(db.DOCS_DATA_DIR, f"{p['id']}.json"), {
            "product": p["id"],
            "generated_at": generated_at,
            "records": records[:FEED_CAP],
        })
        counters["files_written"] += 1
        counters["records_total"] += len(records)
        print(f"[feeds] {p['id']}.json: {min(len(records), FEED_CAP)} records "
              f"({len(records)} total non-noise)")

    # summary.json — 7-day window
    window_start = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)) \
        .strftime("%Y-%m-%d")
    window = [r for r in all_records if r["date"] >= window_start]

    counts_by_kind = {k: 0 for k in ALL_KINDS}
    for r in window:
        counts_by_kind[r["kind"]] = counts_by_kind.get(r["kind"], 0) + 1

    counts_by_product = {p["id"]: 0 for p in all_products}
    for r in window:
        counts_by_product[r["product"]] = counts_by_product.get(r["product"], 0) + 1

    # ranked by kind weight desc, tie-break newest first (stable two-pass sort)
    top_changes = sorted(window, key=lambda r: r["date"], reverse=True)
    top_changes = sorted(top_changes, key=lambda r: KIND_WEIGHT.get(r["kind"], 0),
                         reverse=True)[:TOP_CHANGES_CAP]

    _write_json(os.path.join(db.DOCS_DATA_DIR, "summary.json"), {
        "generated_at": generated_at,
        "window_days": WINDOW_DAYS,
        "counts_by_kind": counts_by_kind,
        "counts_by_product": counts_by_product,
        "top_changes": top_changes,
        "total_records": len(window),
    })
    counters["files_written"] += 1
    print(f"[feeds] summary.json: {len(window)} records in {WINDOW_DAYS}-day window, "
          f"{len(top_changes)} top changes")

    conn.close()
    return counters


if __name__ == "__main__":
    run()
