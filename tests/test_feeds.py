import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import feeds  # noqa: E402


class FeedCategoryTests(unittest.TestCase):
    def test_added_markdown_page_is_new_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/widget-autoscaling.md", "status": "added"},
            ], ["new-file"]),
            "new-page",
        )

    def test_modified_markdown_page_is_existing_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/scale-cluster.md", "status": "modified"},
            ], ["doc-update"]),
            "existing-page",
        )

    def test_mixed_added_and_modified_favors_new_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/scale-cluster.md", "status": "modified"},
                {"filename": "articles/aks/widget-autoscaling.md", "status": "added"},
            ], ["new-file", "doc-update"]),
            "new-page",
        )

    def test_legacy_files_use_new_file_reason_for_backward_compatibility(self):
        self.assertEqual(
            feeds.page_change_category([
                "articles/aks/widget-autoscaling.md",
            ], ["new-file"]),
            "new-page",
        )

    def test_record_json_includes_category_from_raw_patch_status(self):
        row = {
            "id": "abc12345-0",
            "product": "aks",
            "date": "2026-07-05",
            "kind": "new-feature",
            "title": "Add widget autoscaling",
            "summary": "",
            "reasons_json": json.dumps(["new-file"]),
            "files_json": json.dumps(["articles/aks/widget-autoscaling.md"]),
            "doc_urls_json": json.dumps(["https://learn.microsoft.com/azure/aks/widget-autoscaling"]),
            "commit_url": "https://github.com/example/repo/commit/abc12345",
            "sha": "abc123456789",
            "raw_patch_summary": json.dumps({
                "files": [
                    {
                        "filename": "articles/aks/widget-autoscaling.md",
                        "status": "added",
                        "patch": "+Widget autoscaling is now in preview for AKS clusters.",
                    },
                ],
            }),
        }

        record = feeds._record_to_json(row)
        self.assertEqual(record["page_change_category"], "new-page")
        self.assertEqual(
            record["change_summary"],
            "Widget autoscaling is now in preview for AKS clusters.",
        )
        self.assertTrue(record["batch_key"].startswith("new-page:aks:widget-autoscaling"))


if __name__ == "__main__":
    unittest.main()
