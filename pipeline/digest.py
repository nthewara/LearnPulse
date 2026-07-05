"""Digest stage: generate/overwrite the weekly markdown digest.

Writes digests/<ISO-year>-W<week>.md for the current ISO week from the
non-noise records dated within that week, grouped by product.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone

try:
    import db
except ImportError:  # pragma: no cover
    from pipeline import db

KIND_EMOJI = {
    "new-feature": "🆕",
    "ga": "✅",
    "preview": "🧪",
    "deprecation": "⚠️",
    "breaking-change": "💥",
    "doc-update": "📝",
}


def _week_bounds(today: date) -> tuple[date, date, int, int]:
    iso_year, iso_week, iso_weekday = today.isocalendar()
    monday = date.fromordinal(today.toordinal() - (iso_weekday - 1))
    sunday = date.fromordinal(monday.toordinal() + 6)
    return monday, sunday, iso_year, iso_week


def _optional_row_text(row, key: str) -> str | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _author_credit(row) -> str:
    author_login = _optional_row_text(row, "author_login")
    if author_login:
        return f"@{author_login}"
    return _optional_row_text(row, "author_name") or ""


def _digest_without_generated_at(content: str) -> str:
    return re.sub(
        r"\(generated \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\)",
        "(generated <ignored>)",
        content,
    )


def _write_digest_if_changed(path: str, content: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    except FileNotFoundError:
        existing = None
    if existing is not None and _digest_without_generated_at(existing) == _digest_without_generated_at(content):
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True


def run() -> dict:
    conn = db.connect()
    products = db.load_products()
    today = datetime.now(timezone.utc).date()
    monday, sunday, iso_year, iso_week = _week_bounds(today)
    filename = f"{iso_year}-W{iso_week:02d}.md"
    path = os.path.join(db.DIGESTS_DIR, filename)

    lines = [
        f"# LearnPulse digest — {iso_year}-W{iso_week:02d}",
        "",
        f"Week of {monday.isoformat()} to {sunday.isoformat()} "
        f"(generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}).",
        "",
    ]

    total = 0
    for p in products:
        rows = [r for r in db.signal_records(conn, p["id"])
                if monday.isoformat() <= r["date"] <= sunday.isoformat()]
        if not rows:
            continue
        lines.append(f"## {p['name']}")
        lines.append("")
        for r in rows:
            emoji = KIND_EMOJI.get(r["kind"], "📝")
            doc_urls = r.get("doc_urls") or []
            links = [f"[commit]({r['commit_url']})"]
            if doc_urls:
                links.insert(0, f"[doc]({doc_urls[0]})")
            credit = _author_credit(r)
            suffix = f" — {credit}" if credit else ""
            lines.append(f"- {emoji} **{r['title']}** ({r['kind']}, {r['date']}) — "
                         + " · ".join(links) + suffix)
        lines.append("")
        total += len(rows)

    if total == 0:
        lines.append("_No notable changes recorded this week._")
        lines.append("")

    os.makedirs(db.DIGESTS_DIR, exist_ok=True)
    wrote = _write_digest_if_changed(path, "\n".join(lines))

    conn.close()
    action = "wrote" if wrote else "unchanged"
    print(f"[digest] {action} {os.path.relpath(path, db.ROOT)} with {total} records")
    return {"file": path, "records": total, "written": wrote}


if __name__ == "__main__":
    run()
