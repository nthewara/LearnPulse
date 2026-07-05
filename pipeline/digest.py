"""Digest stage: generate/overwrite the weekly markdown digest.

Writes digests/<ISO-year>-W<week>.md for the current ISO week from the
non-noise records dated within that week, grouped by product.
"""
from __future__ import annotations

import json
import os
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
            doc_urls = json.loads(r["doc_urls_json"] or "[]")
            links = [f"[commit]({r['commit_url']})"]
            if doc_urls:
                links.insert(0, f"[doc]({doc_urls[0]})")
            lines.append(f"- {emoji} **{r['title']}** ({r['kind']}, {r['date']}) — "
                         + " · ".join(links))
        lines.append("")
        total += len(rows)

    if total == 0:
        lines.append("_No notable changes recorded this week._")
        lines.append("")

    os.makedirs(db.DIGESTS_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    conn.close()
    print(f"[digest] wrote {os.path.relpath(path, db.ROOT)} with {total} records")
    return {"file": path, "records": total}


if __name__ == "__main__":
    run()
