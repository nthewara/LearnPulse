"""LearnPulse pipeline orchestrator: ingest → triage → summarize → feeds → digest.

Usage:
    python pipeline/run.py [--max-commits N] [--since-days N] [--products aks,fleet]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db        # noqa: E402
import digest    # noqa: E402
import feeds     # noqa: E402
import ingest    # noqa: E402
import summarize # noqa: E402
import triage    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="LearnPulse pipeline")
    ap.add_argument("--max-commits", type=int, default=None,
                    help="cap commit-detail fetches per product (rate-limit friendly)")
    ap.add_argument("--since-days", type=int, default=None,
                    help="backfill window in days (overrides stored cursor)")
    ap.add_argument("--products", type=str, default=None,
                    help="comma-separated product ids (default: all)")
    args = ap.parse_args()
    products = [p.strip() for p in args.products.split(",")] if args.products else None

    known = {p["id"] for p in db.load_products()}
    if products:
        unknown = set(products) - known
        if unknown:
            print(f"error: unknown product id(s): {', '.join(sorted(unknown))} "
                  f"(known: {', '.join(sorted(known))})", file=sys.stderr)
            return 2

    print("=== LearnPulse pipeline ===")
    print(f"products={products or 'all'} since_days={args.since_days} "
          f"max_commits={args.max_commits}")

    print("\n--- stage 1/5: ingest ---")
    ingest_totals = ingest.run(products=products, since_days=args.since_days,
                               max_commits=args.max_commits)
    new_total = sum(c["new"] for c in ingest_totals.values())
    print(f"[ingest] done: {new_total} new commits across "
          f"{len(ingest_totals)} product(s)")

    print("\n--- stage 2/5: triage ---")
    triage.run(products=products)

    print("\n--- stage 3/5: summarize ---")
    summarize.run(products=products)

    print("\n--- stage 4/5: feeds ---")
    feeds.run(products=products)

    print("\n--- stage 5/5: digest ---")
    digest.run()

    print("\n=== pipeline complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
