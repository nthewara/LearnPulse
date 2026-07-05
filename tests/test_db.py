import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import db  # noqa: E402


class DbMigrationTests(unittest.TestCase):
    def test_connect_adds_author_columns_to_existing_change_records_table(self):
        scratch_dir = ROOT / "tests" / ".scratch"
        db_path = scratch_dir / "migration-test.db"
        scratch_dir.mkdir(exist_ok=True)
        if db_path.exists():
            db_path.unlink()

        old_conn = sqlite3.connect(str(db_path))
        old_conn.executescript("""
            CREATE TABLE change_records (
                id                 TEXT PRIMARY KEY,
                product            TEXT NOT NULL,
                date               TEXT NOT NULL,
                kind               TEXT,
                title              TEXT,
                summary            TEXT,
                reasons_json       TEXT,
                files_json         TEXT,
                doc_urls_json      TEXT,
                commit_url         TEXT,
                sha                TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                is_noise           INTEGER,
                raw_commit_message TEXT,
                raw_patch_summary  TEXT
            );
        """)
        old_conn.close()

        migrated = None
        try:
            migrated = db.connect(str(db_path))
            columns = {
                row["name"]
                for row in migrated.execute("PRAGMA table_info(change_records)")
            }

            self.assertIn("author_login", columns)
            self.assertIn("author_name", columns)
            migrated.execute(
                "INSERT INTO change_records "
                "(id, product, date, sha, created_at, author_login, author_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("abc12345-0", "aks", "2026-07-05", "abc12345", "now", "octocat", "Octo Cat"),
            )
        finally:
            if migrated is not None:
                migrated.close()
            if db_path.exists():
                db_path.unlink()
            try:
                scratch_dir.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
