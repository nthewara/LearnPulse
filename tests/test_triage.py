import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import triage  # noqa: E402


PRODUCT = {
    "id": "aks",
    "path": "articles/aks",
    "learn_base": "https://learn.microsoft.com/azure/aks/",
}

AZURE_OPENAI_PRODUCT = {
    "id": "azure-openai",
    "path": "articles/foundry/openai",
    "learn_base": "https://learn.microsoft.com/azure/foundry/openai/",
}


def record(message, files):
    return {
        "raw_commit_message": message,
        "raw_patch_summary": json.dumps({"total_files_in_commit": len(files), "files": files}),
    }


class TriageClassificationTests(unittest.TestCase):
    def test_metadata_only_diff_is_noise(self):
        result = triage.classify(record("refresh metadata", [{
            "filename": "articles/aks/index.md",
            "status": "modified",
            "additions": 1,
            "deletions": 1,
            "patch": "-ms.date: 01/01/2026\n+ms.date: 07/05/2026",
        }]), PRODUCT)

        self.assertEqual(result["is_noise"], 1)
        self.assertEqual(result["kind"], "doc-update")
        self.assertEqual(result["reasons"], ["metadata-only"])
        self.assertEqual(result["doc_urls"], [])

    def test_new_preview_page_is_signal_with_doc_url(self):
        result = triage.classify(record("Add widget autoscaling preview (#123)", [{
            "filename": "articles/aks/widget-autoscaling.md",
            "status": "added",
            "additions": 4,
            "deletions": 0,
            "patch": "+title: Widget autoscaling preview\n+Widget autoscaling is now in preview for AKS clusters.",
        }]), PRODUCT)

        self.assertEqual(result["is_noise"], 0)
        self.assertEqual(result["kind"], "new-feature")
        self.assertIn("new-file", result["reasons"])
        self.assertIn("keyword:preview", result["reasons"])
        self.assertEqual(result["title"], "Add widget autoscaling preview")
        self.assertEqual(result["doc_urls"], ["https://learn.microsoft.com/azure/aks/widget-autoscaling"])

    def test_removed_markdown_page_is_retired_signal_without_doc_url(self):
        result = triage.classify(record("Remove retired feature page", [{
            "filename": "articles/aks/legacy-feature.md",
            "status": "removed",
            "additions": 0,
            "deletions": 2,
            "patch": "-# Legacy feature\n-This page is retired.",
        }]), PRODUCT)

        self.assertEqual(result["is_noise"], 0)
        self.assertEqual(result["kind"], "deprecation")
        self.assertIn("retired-page", result["reasons"])
        self.assertEqual(result["doc_urls"], [])

    def test_azure_ai_repo_path_maps_to_learn_url(self):
        self.assertEqual(
            triage.doc_url_for(
                "articles/foundry/openai/how-to/fine-tuning.md",
                AZURE_OPENAI_PRODUCT,
            ),
            "https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning",
        )

    def test_azure_ai_repo_path_excludes_non_pages(self):
        self.assertIsNone(
            triage.doc_url_for(
                "articles/foundry/openai/includes/quota.md",
                AZURE_OPENAI_PRODUCT,
            )
        )


if __name__ == "__main__":
    unittest.main()
