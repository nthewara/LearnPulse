#!/usr/bin/env python3
"""Migrate the legacy SQLite data store to JSON store files."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import db  # noqa: E402
import derived  # noqa: E402
import feeds  # noqa: E402

LEGACY_PATCH_COLUMN = "raw" "_patch_summary"
ARRAY_COLUMNS = {
    "reasons_json": "reasons",
    "files_json": "files",
    "doc_urls_json": "doc_urls",
}


def _parse_array(value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _read_docs_payloads(docs_data_dir: Path) -> dict[str, dict]:
    payloads = {}
    if not docs_data_dir.is_dir():
        return payloads
    for path in docs_data_dir.glob("*.json"):
        with path.open("r", encoding="utf-8") as fh:
            payloads[path.stem] = json.load(fh)
    return payloads


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


def _comparison_now(before_payloads: dict[str, dict]) -> datetime:
    generated_at = (before_payloads.get("summary") or {}).get("generated_at")
    if isinstance(generated_at, str):
        try:
            return datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _semantic_feed_summary(store: db.Store, products: list[dict], before_payloads: dict[str, dict]) -> dict:
    if not before_payloads:
        return {"checked": 0, "matched": 0, "mismatched": []}

    now = _comparison_now(before_payloads)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    after_payloads = feeds.build_payloads(store, products, generated_at=generated_at, now=now)
    checked = 0
    matched = 0
    mismatched = []
    for key, before in sorted(before_payloads.items()):
        after = after_payloads.get(key)
        if after is None:
            continue
        checked += 1
        if _strip_generated_at(before) == _strip_generated_at(after):
            matched += 1
        else:
            mismatched.append(key)
    return {"checked": checked, "matched": matched, "mismatched": mismatched}


def load_legacy(sqlite_path: Path) -> tuple[dict[str, list[dict]], dict, Counter]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    records_by_product: dict[str, list[dict]] = defaultdict(list)
    counts_in: Counter = Counter()

    for row in conn.execute("SELECT * FROM change_records ORDER BY product, date, created_at, id"):
        source = _row_dict(row)
        product = source["product"]
        counts_in[product] += 1
        full_patch = source.get(LEGACY_PATCH_COLUMN) or ""
        record = {
            "id": source["id"],
            "product": product,
            "date": source["date"],
            "kind": source.get("kind"),
            "title": source.get("title"),
            "summary": source.get("summary"),
            "reasons": _parse_array(source.get("reasons_json")),
            "files": _parse_array(source.get("files_json")),
            "doc_urls": _parse_array(source.get("doc_urls_json")),
            "commit_url": source.get("commit_url"),
            "sha": source["sha"],
            "created_at": source["created_at"],
            "is_noise": source.get("is_noise"),
            "raw_commit_message": source.get("raw_commit_message"),
            "patch_excerpt": derived.cap_patch_excerpt(full_patch),
        }
        for optional in ("author_login", "author_name"):
            if optional in source and source[optional]:
                record[optional] = source[optional]

        display_source = dict(record)
        display_source["patch_excerpt"] = full_patch
        record.update(derived.compute_display_fields(display_source))
        records_by_product[product].append(record)

    state = {"schema_version": db.SCHEMA_VERSION, "cursors": {}, "seen": {}}
    for row in conn.execute("SELECT product_id, last_iso_ts FROM cursors ORDER BY product_id"):
        state["cursors"][row["product_id"]] = row["last_iso_ts"]
    for row in conn.execute("SELECT sha, product_id, seen_at FROM commits_seen ORDER BY product_id, sha"):
        product_seen = state["seen"].setdefault(row["product_id"], {})
        product_seen[row["sha"]] = str(row["seen_at"] or "")[:10]

    conn.close()
    return dict(records_by_product), state, counts_in


def migrate(sqlite_path: Path, data_dir: Path, docs_data_dir: Path | None = None) -> dict:
    records_by_product, state, counts_in = load_legacy(sqlite_path)
    products = db.load_products()
    store = db.Store(str(data_dir))
    store.records = records_by_product
    store.state = state
    store.dirty_products = {product["id"] for product in products}
    for product in products:
        store.product_records(product["id"])
        store.state.setdefault("seen", {}).setdefault(product["id"], {})
    store.state_dirty = True
    store.save()

    counts_out = Counter({
        product: len(records)
        for product, records in store.records.items()
        if records
    })
    docs_dir = docs_data_dir or (ROOT / "docs" / "data")
    semantic = _semantic_feed_summary(store, products, _read_docs_payloads(docs_dir))
    summary = {
        "sqlite_path": str(sqlite_path),
        "data_dir": str(data_dir),
        "counts_in": dict(sorted(counts_in.items())),
        "counts_out": dict(sorted(counts_out.items())),
        "semantic_feed_equality": semantic,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate data/learnpulse.db to JSON store files")
    parser.add_argument("--sqlite-path", type=Path, default=ROOT / "data" / "learnpulse.db")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--docs-data-dir", type=Path, default=ROOT / "docs" / "data")
    args = parser.parse_args()

    if not args.sqlite_path.exists():
        print(f"error: SQLite database not found: {args.sqlite_path}", file=sys.stderr)
        return 2
    summary = migrate(args.sqlite_path, args.data_dir, args.docs_data_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
