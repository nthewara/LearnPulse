import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import migrate_sqlite_to_json  # noqa: E402

LEGACY_PATCH_COLUMN = "raw" "_patch_summary"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.scratch_dir = ROOT / "tests" / ".scratch" / self._testMethodName
        shutil.rmtree(self.scratch_dir, ignore_errors=True)
        self.scratch_dir.mkdir(parents=True)
        self.sqlite_path = self.scratch_dir / "learnpulse.db"
        self.data_dir = self.scratch_dir / "data"
        self.docs_dir = self.scratch_dir / "docs-data"
        self.docs_dir.mkdir()
        self._create_legacy_db()

    def tearDown(self):
        shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def test_migration_writes_json_store_with_capped_excerpt_and_display_fields(self):
        summary = migrate_sqlite_to_json.migrate(
            self.sqlite_path,
            self.data_dir,
            self.docs_dir,
        )

        self.assertEqual(summary["counts_in"], {"aks": 1})
        self.assertEqual(summary["counts_out"], {"aks": 1})
        records_path = self.data_dir / "records" / "aks.json"
        state_path = self.data_dir / "state.json"
        self.assertTrue(records_path.exists())
        self.assertTrue(state_path.exists())

        payload = json.loads(records_path.read_text(encoding="utf-8"))
        record = payload["records"][0]
        self.assertLessEqual(len(record["patch_excerpt"]), 2048)
        self.assertEqual(record["reasons"], ["new-file"])
        self.assertEqual(record["files"], ["articles/aks/widget.md"])
        self.assertEqual(record["doc_urls"], ["https://learn.microsoft.com/azure/aks/widget"])
        self.assertEqual(record["change_summary"], "Widget autoscaling is now available for AKS clusters.")
        self.assertEqual(record["page_change_category"], "new-page")
        self.assertTrue(record["batch_key"].startswith("new-page:aks:widget-autoscaling"))
        self.assertNotIn("reasons_json", record)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["cursors"], {"aks": "2026-07-06T00:00:00Z"})
        self.assertEqual(state["seen"]["aks"], {"abc123456789": "2026-07-05"})

        reloaded = db.Store(str(self.data_dir))
        self.assertEqual(len(db.signal_records(reloaded, "aks")), 1)

    def _create_legacy_db(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.execute(f"""
            CREATE TABLE change_records (
                id TEXT PRIMARY KEY,
                product TEXT NOT NULL,
                date TEXT NOT NULL,
                kind TEXT,
                title TEXT,
                summary TEXT,
                reasons_json TEXT,
                files_json TEXT,
                doc_urls_json TEXT,
                commit_url TEXT,
                sha TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_noise INTEGER,
                raw_commit_message TEXT,
                {LEGACY_PATCH_COLUMN} TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE commits_seen (
                sha TEXT NOT NULL,
                product_id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (sha, product_id)
            )
        """)
        conn.execute("""
            CREATE TABLE cursors (
                product_id TEXT PRIMARY KEY,
                last_iso_ts TEXT NOT NULL
            )
        """)
        patch = json.dumps({
            "total_files_in_commit": 1,
            "files": [{
                "filename": "articles/aks/widget.md",
                "status": "added",
                "additions": 1,
                "deletions": 0,
                "patch": "+Widget autoscaling is now available for AKS clusters.\n+::: " + "x" * 3000,
            }],
        })
        conn.execute(f"""
            INSERT INTO change_records (
                id, product, date, kind, title, summary, reasons_json, files_json,
                doc_urls_json, commit_url, sha, created_at, is_noise,
                raw_commit_message, {LEGACY_PATCH_COLUMN}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "abc12345-0",
            "aks",
            "2026-07-05",
            "new-feature",
            "Add widget autoscaling",
            "Added widget autoscaling docs.",
            json.dumps(["new-file"]),
            json.dumps(["articles/aks/widget.md"]),
            json.dumps(["https://learn.microsoft.com/azure/aks/widget"]),
            "https://github.com/example/repo/commit/abc12345",
            "abc123456789",
            "2026-07-05T01:02:03Z",
            0,
            "Add widget autoscaling",
            patch,
        ))
        conn.execute(
            "INSERT INTO commits_seen (sha, product_id, seen_at) VALUES (?, ?, ?)",
            ("abc123456789", "aks", "2026-07-05T01:02:03Z"),
        )
        conn.execute(
            "INSERT INTO cursors (product_id, last_iso_ts) VALUES (?, ?)",
            ("aks", "2026-07-06T00:00:00Z"),
        )
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
