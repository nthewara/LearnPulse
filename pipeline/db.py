"""JSON store and helpers for LearnPulse.

The JSON store keeps all data in memory and writes it back atomically:
  data/state.json              — cursors and per-product seen SHA dates
  data/records/<product>.json  — one envelope per product
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "learnpulse.db")
RECORDS_DIR = os.path.join(DATA_DIR, "records")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
DOCS_DATA_DIR = os.path.join(ROOT, "docs", "data")
DIGESTS_DIR = os.path.join(ROOT, "digests")
PRODUCTS_YML = os.path.join(ROOT, "products.yml")

SCHEMA_VERSION = 1
SEEN_RETENTION_DAYS = 90
PATCH_EXCERPT_MAX_CHARS = 2048


def _data_dir_for(db_path: str) -> str | None:
    if db_path == ":memory:":
        return None
    if os.path.splitext(os.path.basename(db_path))[1]:
        return os.path.dirname(db_path) or "."
    return db_path


def _empty_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "cursors": {}, "seen": {}}


def _date_part(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value)
    return value[:10] if len(value) >= 10 else None


def _parse_day(value: str | None) -> date | None:
    value = _date_part(value)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _json_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _normalize_state(raw) -> dict:
    state = _empty_state()
    if isinstance(raw, dict):
        state["schema_version"] = raw.get("schema_version", SCHEMA_VERSION)
        if isinstance(raw.get("cursors"), dict):
            state["cursors"] = dict(raw["cursors"])
        if isinstance(raw.get("seen"), dict):
            seen = {}
            for product, entries in raw["seen"].items():
                if isinstance(entries, dict):
                    seen[product] = {
                        str(sha): (_date_part(seen_at) or "")
                        for sha, seen_at in entries.items()
                    }
                elif isinstance(entries, list):
                    seen[product] = {str(sha): "" for sha in entries}
            state["seen"] = seen
    return state


def _normalize_record(raw: dict, product: str) -> dict:
    record = dict(raw)
    record.setdefault("product", product)

    for legacy_key, new_key in (
        ("reasons_json", "reasons"),
        ("files_json", "files"),
        ("doc_urls_json", "doc_urls"),
    ):
        if new_key not in record and legacy_key in record:
            record[new_key] = _json_list(record[legacy_key])
        record.pop(legacy_key, None)
    return record


def _record_json(record: dict) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ": "),
    )


def _record_sort_key(record: dict) -> tuple[str, str, str]:
    return (
        str(record.get("date") or ""),
        str(record.get("created_at") or ""),
        str(record.get("id") or ""),
    )


def _serialize_product(product: str, records: list[dict]) -> str:
    lines = [
        "{",
        '  "schema_version": 1,',
        f'  "product": {json.dumps(product, ensure_ascii=False)},',
        '  "records": [',
    ]
    if records:
        lines.append(",\n".join(f"  {_record_json(record)}" for record in records))
    lines.extend(["  ]", "}"])
    return "\n".join(lines) + "\n"


def _serialize_state(state: dict) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "cursors": state.get("cursors", {}),
        "seen": state.get("seen", {}),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
    ) + "\n"


def _atomic_write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, path)


class Store:
    """In-memory JSON store.

    Seen pruning policy: on save, each product's seen SHAs are retained for a
    90-day window behind that product's cursor. The comparison date is the
    record date for the same product/SHA when available, otherwise seen_at.
    Entries with unparsable dates, or products without cursors, are retained.
    """

    def __init__(self, data_dir: str | None = DATA_DIR):
        self.data_dir = data_dir
        self.records_dir = None if data_dir is None else os.path.join(data_dir, "records")
        self.state_path = None if data_dir is None else os.path.join(data_dir, "state.json")
        self.records: dict[str, list[dict]] = {}
        self.state = _empty_state()
        self.dirty_products: set[str] = set()
        self.state_dirty = False
        self.closed = False
        self._load()

    def _load(self) -> None:
        if self.data_dir is None:
            return

        if self.state_path and os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as fh:
                self.state = _normalize_state(json.load(fh))
        else:
            self.state = _empty_state()
            self.state_dirty = True

        if not self.records_dir or not os.path.isdir(self.records_dir):
            return
        for name in sorted(os.listdir(self.records_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.records_dir, name)
            with open(path, "r", encoding="utf-8") as fh:
                envelope = json.load(fh)
            product = envelope.get("product") or name[:-5]
            raw_records = envelope.get("records") or []
            self.records[product] = [
                _normalize_record(record, product)
                for record in raw_records
                if isinstance(record, dict)
            ]

    def product_records(self, product: str) -> list[dict]:
        return self.records.setdefault(product, [])

    def all_records(self) -> list[dict]:
        out = []
        for product in sorted(self.records):
            out.extend(self.records[product])
        return out

    def mark_product_dirty(self, product: str) -> None:
        self.dirty_products.add(product)

    def mark_state_dirty(self) -> None:
        self.state_dirty = True

    def _record_dates(self) -> dict[str, dict[str, str]]:
        dates: dict[str, dict[str, str]] = {}
        for product, records in self.records.items():
            product_dates = dates.setdefault(product, {})
            for record in records:
                sha = record.get("sha")
                record_day = _date_part(record.get("date"))
                if not sha or not record_day:
                    continue
                current = product_dates.get(sha)
                if current is None or record_day > current:
                    product_dates[sha] = record_day
        return dates

    def _prune_seen(self) -> bool:
        changed = False
        record_dates = self._record_dates()
        cursors = self.state.get("cursors", {})
        seen = self.state.get("seen", {})
        for product, entries in list(seen.items()):
            cursor_day = _parse_day(cursors.get(product))
            if cursor_day is None or not isinstance(entries, dict):
                continue
            cutoff = cursor_day - timedelta(days=SEEN_RETENTION_DAYS)
            product_record_dates = record_dates.get(product, {})
            for sha, seen_at in list(entries.items()):
                comparison_day = _parse_day(product_record_dates.get(sha) or seen_at)
                if comparison_day is not None and comparison_day < cutoff:
                    del entries[sha]
                    changed = True
        return changed

    def save(self) -> None:
        if self.closed:
            raise RuntimeError("store is closed")
        if self._prune_seen():
            self.state_dirty = True

        for product in sorted(self.dirty_products):
            self.product_records(product).sort(key=_record_sort_key, reverse=True)

        if self.data_dir is None:
            self.dirty_products.clear()
            self.state_dirty = False
            return

        for product in sorted(self.dirty_products):
            path = os.path.join(self.records_dir, f"{product}.json")
            _atomic_write(path, _serialize_product(product, self.product_records(product)))
        self.dirty_products.clear()

        if self.state_dirty:
            _atomic_write(self.state_path, _serialize_state(self.state))
            self.state_dirty = False

    def commit(self) -> None:
        self.save()

    def close(self) -> None:
        self.closed = True


def connect(db_path: str = DB_PATH) -> Store:
    """Open the LearnPulse JSON store."""
    return Store(_data_dir_for(db_path))


def load_products():
    """Load the product watchlist from products.yml (list of dicts)."""
    import yaml  # stdlib + pyyaml only

    with open(PRODUCTS_YML, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg["products"]


# ---------------------------------------------------------------- cursors

def get_cursor(conn: Store, product_id: str):
    return conn.state.get("cursors", {}).get(product_id)


def set_cursor(conn: Store, product_id: str, iso_ts: str) -> None:
    conn.state.setdefault("cursors", {})[product_id] = iso_ts
    conn.mark_state_dirty()


# ----------------------------------------------------------- commits_seen

def is_seen(conn: Store, sha: str, product_id: str) -> bool:
    return sha in conn.state.get("seen", {}).get(product_id, {})


def mark_seen(conn: Store, sha: str, product_id: str, seen_at: str) -> None:
    product_seen = conn.state.setdefault("seen", {}).setdefault(product_id, {})
    product_seen.setdefault(sha, _date_part(seen_at) or _today())
    conn.mark_state_dirty()


# --------------------------------------------------------- change_records

def _row(record: dict) -> dict:
    row = dict(record)
    row.setdefault("kind", None)
    row.setdefault("title", None)
    row.setdefault("summary", None)
    row.setdefault("reasons", [])
    row.setdefault("files", [])
    row.setdefault("doc_urls", [])
    row.setdefault("is_noise", None)
    row.setdefault("patch_excerpt", "")
    row.setdefault("change_summary", "")
    row.setdefault("page_change_category", "")
    row.setdefault("batch_key", "")
    return row


def next_record_id(conn: Store, sha: str) -> str:
    """Return <sha[:8]>-<n>, avoiding collisions across SHAs with the same prefix."""
    prefix = f"{sha[:8]}-"
    suffixes = []
    for record in conn.all_records():
        record_id = str(record.get("id", ""))
        if not record_id.startswith(prefix):
            continue
        suffix = record_id[len(prefix):]
        if suffix.isdigit():
            suffixes.append(int(suffix))
    next_suffix = max(suffixes) + 1 if suffixes else 0
    return f"{prefix}{next_suffix}"


def insert_raw_record(conn: Store, *, record_id, product, date, commit_url, sha,
                      created_at, raw_commit_message, patch_excerpt=None,
                      author_login=None,
                      author_name=None) -> None:
    patch_excerpt = (patch_excerpt or "")[:PATCH_EXCERPT_MAX_CHARS]
    record = {
        "id": record_id,
        "product": product,
        "date": date,
        "commit_url": commit_url,
        "sha": sha,
        "created_at": created_at,
        "raw_commit_message": raw_commit_message,
        "patch_excerpt": patch_excerpt,
    }
    if author_login:
        record["author_login"] = author_login
    if author_name:
        record["author_name"] = author_name
    conn.product_records(product).append(record)
    conn.mark_product_dirty(product)


def untriaged_records(conn: Store):
    return [_row(record) for record in conn.all_records() if record.get("is_noise") is None]


def _find_record(conn: Store, record_id: str) -> tuple[str, dict]:
    for product, records in conn.records.items():
        for record in records:
            if record.get("id") == record_id:
                return product, record
    raise KeyError(f"record not found: {record_id}")


def apply_triage(conn: Store, record_id, *, kind, title, reasons=None, files=None,
                 doc_urls=None, is_noise, reasons_json=None, files_json=None,
                 doc_urls_json=None) -> None:
    product, record = _find_record(conn, record_id)
    record.update({
        "kind": kind,
        "title": title,
        "reasons": _json_list(reasons) if reasons is not None else _json_list(reasons_json),
        "files": _json_list(files) if files is not None else _json_list(files_json),
        "doc_urls": _json_list(doc_urls) if doc_urls is not None else _json_list(doc_urls_json),
        "is_noise": is_noise,
    })
    try:
        import derived
    except ImportError:  # pragma: no cover
        from pipeline import derived
    record.update(derived.compute_display_fields(record))
    conn.mark_product_dirty(product)


def set_triage(conn: Store, record_id, **kwargs) -> None:
    apply_triage(conn, record_id, **kwargs)


def unsummarized_records(conn: Store):
    return [
        _row(record)
        for record in conn.all_records()
        if record.get("is_noise") == 0 and not record.get("summary")
    ]


def set_summary(conn: Store, record_id, *, kind, title, summary,
                change_summary=None, page_change_category=None, batch_key=None) -> None:
    product, record = _find_record(conn, record_id)
    updates = {"kind": kind, "title": title, "summary": summary}
    if change_summary is not None:
        updates["change_summary"] = change_summary
    if page_change_category is not None:
        updates["page_change_category"] = page_change_category
    if batch_key is not None:
        updates["batch_key"] = batch_key
    record.update(updates)
    conn.mark_product_dirty(product)


def signal_records(conn: Store, product: str | None = None):
    """Non-noise records, newest first."""
    records = [
        record for record in conn.all_records()
        if record.get("is_noise") == 0 and (product is None or record.get("product") == product)
    ]
    records.sort(key=lambda record: record.get("id") or "")
    records.sort(
        key=lambda record: (record.get("date") or "", record.get("created_at") or ""),
        reverse=True,
    )
    return [_row(record) for record in records]
