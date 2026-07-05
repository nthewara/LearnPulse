import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import db  # noqa: E402


class JsonStoreTests(unittest.TestCase):
    def setUp(self):
        self.scratch_dir = ROOT / "tests" / ".scratch" / self._testMethodName
        shutil.rmtree(self.scratch_dir, ignore_errors=True)
        self.scratch_dir.mkdir(parents=True)
        self.db_path = self.scratch_dir / "learnpulse.db"

    def tearDown(self):
        shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def connect(self):
        return db.connect(str(self.db_path))

    def add_raw(self, store, product="aks", sha="abcdef123456", record_id=None,
                date="2026-07-05", patch_excerpt=None):
        if record_id is None:
            record_id = db.next_record_id(store, sha)
        db.insert_raw_record(
            store,
            record_id=record_id,
            product=product,
            date=date,
            commit_url=f"https://github.com/example/repo/commit/{sha}",
            sha=sha,
            created_at=f"{date}T01:02:03Z",
            raw_commit_message="Add widget autoscaling",
            patch_excerpt=patch_excerpt or json.dumps({
                "files": [
                    {
                        "filename": "articles/aks/widget.md",
                        "status": "added",
                        "patch": "+Widget autoscaling is now available.",
                    },
                ],
            }),
            author_login="octocat",
            author_name="Octo Cat",
        )
        return record_id

    def test_round_trip_load_mutate_save_load_equality(self):
        store = self.connect()
        record_id = self.add_raw(store)
        db.mark_seen(store, "abcdef123456", "aks", "2026-07-05T00:00:00Z")
        db.set_cursor(store, "aks", "2026-07-06T00:00:00Z")
        db.apply_triage(
            store,
            record_id,
            kind="new-feature",
            title="Add widget autoscaling",
            reasons=["new-file"],
            files=["articles/aks/widget.md"],
            doc_urls=["https://learn.microsoft.com/azure/aks/widget"],
            is_noise=0,
        )
        db.set_summary(store, record_id, kind="new-feature",
                       title="Add widget autoscaling",
                       summary="Added widget autoscaling docs.",
                       change_summary="Widget autoscaling is now available.",
                       page_change_category="new-page",
                       batch_key="new-page:aks:widget-autoscaling-is-now-available")
        store.save()

        reloaded = self.connect()
        self.assertEqual(reloaded.state, store.state)
        self.assertEqual(reloaded.records, store.records)
        rows = db.signal_records(reloaded, "aks")
        self.assertEqual(rows[0]["reasons"], ["new-file"])
        self.assertEqual(rows[0]["change_summary"], "Widget autoscaling is now available.")

        records_file = self.scratch_dir / "records" / "aks.json"
        persisted = json.loads(records_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["records"][0]["patch_excerpt"], rows[0]["patch_excerpt"])

    def test_atomic_write_success_leaves_no_tmp_files(self):
        store = self.connect()
        self.add_raw(store)
        db.set_cursor(store, "aks", "2026-07-06T00:00:00Z")
        store.save()

        self.assertFalse(list(self.scratch_dir.rglob("*.tmp")))

    def test_atomic_write_failure_between_tmp_write_and_replace_keeps_original(self):
        store = self.connect()
        self.add_raw(store)
        store.save()
        records_file = self.scratch_dir / "records" / "aks.json"
        original = records_file.read_bytes()

        self.add_raw(store, sha="fedcba987654")
        with mock.patch.object(db.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                store.save()

        self.assertEqual(records_file.read_bytes(), original)
        self.assertTrue((self.scratch_dir / "records" / "aks.json.tmp").exists())

    def test_next_record_id_avoids_sha_prefix_collisions(self):
        store = self.connect()
        first_sha = "abcdef12aaaabbbb"
        second_sha = "abcdef12ccccdddd"

        first_id = db.next_record_id(store, first_sha)
        self.assertEqual(first_id, "abcdef12-0")
        self.add_raw(store, sha=first_sha, record_id=first_id)

        second_id = db.next_record_id(store, second_sha)
        self.assertEqual(second_id, "abcdef12-1")
        self.add_raw(store, sha=second_sha, record_id=second_id)

        self.assertEqual(db.next_record_id(store, first_sha), "abcdef12-2")

    def test_seen_pruning_uses_cursor_window_and_record_dates(self):
        store = self.connect()
        db.set_cursor(store, "aks", "2026-07-06T00:00:00Z")
        db.mark_seen(store, "old-no-record", "aks", "2026-01-01T00:00:00Z")

        old_record_sha = "11111111aaaa"
        self.add_raw(store, sha=old_record_sha, date="2026-03-01")
        db.mark_seen(store, old_record_sha, "aks", "2026-07-01T00:00:00Z")

        recent_record_sha = "22222222bbbb"
        self.add_raw(store, sha=recent_record_sha, date="2026-05-01")
        db.mark_seen(store, recent_record_sha, "aks", "2026-01-01T00:00:00Z")

        db.mark_seen(store, "recent-no-record", "aks", "2026-06-01T00:00:00Z")
        store.save()

        seen = self.connect().state["seen"]["aks"]
        self.assertNotIn("old-no-record", seen)
        self.assertNotIn(old_record_sha, seen)
        self.assertIn(recent_record_sha, seen)
        self.assertIn("recent-no-record", seen)

    def test_unchanged_data_serializes_byte_identically(self):
        store = self.connect()
        record_id = self.add_raw(
            store,
            patch_excerpt=json.dumps({"z": "é", "a": [2, 1]}, ensure_ascii=False),
        )
        db.apply_triage(
            store,
            record_id,
            kind="doc-update",
            title="Résumé update",
            reasons=["doc-update"],
            files=["articles/aks/résumé.md"],
            doc_urls=[],
            is_noise=0,
        )
        store.save()

        records_file = self.scratch_dir / "records" / "aks.json"
        first_bytes = records_file.read_bytes()
        first_text = first_bytes.decode("utf-8")
        self.assertIn('\n  {"', first_text)
        self.assertNotIn("reasons_json", first_text)

        reloaded = self.connect()
        db.apply_triage(
            reloaded,
            record_id,
            kind="doc-update",
            title="Résumé update",
            reasons=["doc-update"],
            files=["articles/aks/résumé.md"],
            doc_urls=[],
            is_noise=0,
        )
        reloaded.save()

        self.assertEqual(records_file.read_bytes(), first_bytes)

    def test_save_orders_records_newest_first(self):
        store = self.connect()
        old_id = self.add_raw(store, sha="11111111aaaa", date="2026-07-01")
        new_id = self.add_raw(store, sha="22222222bbbb", date="2026-07-05")
        store.save()

        payload = json.loads((self.scratch_dir / "records" / "aks.json").read_text(encoding="utf-8"))
        self.assertEqual([record["id"] for record in payload["records"]], [new_id, old_id])

    def test_triage_persists_display_fields(self):
        store = self.connect()
        record_id = self.add_raw(store)
        db.apply_triage(
            store,
            record_id,
            kind="new-feature",
            title="Add widget autoscaling",
            reasons=["new-file"],
            files=["articles/aks/widget.md"],
            doc_urls=["https://learn.microsoft.com/azure/aks/widget"],
            is_noise=0,
        )

        record = store.records["aks"][0]
        self.assertEqual(record["page_change_category"], "new-page")
        self.assertTrue(record["batch_key"].startswith("new-page:aks:"))
        self.assertEqual(record["change_summary"], "Widget autoscaling is now available.")


if __name__ == "__main__":
    unittest.main()
