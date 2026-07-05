import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import summarize  # noqa: E402


def row(title, files):
    return {
        "title": title,
        "raw_patch_summary": json.dumps({"files": files}),
    }


class SummarizeHeuristicTests(unittest.TestCase):
    def test_applies_to_markdown_is_summarized_without_raw_tokens(self):
        result = summarize.doc_change_summary(row("Adding AKS Automatic", [{
            "filename": "articles/aks/image-cleaner.md",
            "status": "modified",
            "patch": "+**Applies to**: :heavy_check_mark: AKS Automatic :heavy_check_mark: AKS Standard",
        }]))

        self.assertEqual(
            result,
            "Applies-to matrix now includes AKS Automatic and AKS Standard.",
        )
        self.assertNotIn(":heavy_check_mark:", result)
        self.assertNotIn("**", result)

    def test_prefers_real_guidance_over_metadata(self):
        result = summarize.doc_change_summary(row("Apply suggestions", [{
            "filename": "articles/aks/vertical-pod-autoscaler.md",
            "status": "modified",
            "patch": "\n".join([
                "+ms.date: 07/05/2026",
                "+If you're unfamiliar with VPA, use this deployment pattern during application development,",
            ]),
        }]))

        self.assertEqual(
            result,
            "If you're unfamiliar with VPA, use this deployment pattern during application development.",
        )

    def test_table_rows_become_table_summary_not_numeric_fragments(self):
        result = summarize.doc_change_summary(row("Update compatibility table", [{
            "filename": "articles/aks/app-routing.md",
            "status": "modified",
            "patch": "+| 1.3 | 1.28 | ~Aug 2026 (expected) | 1.30, 1.31, 1.32 |",
        }]))

        self.assertEqual(result, "Updated documentation table.")

    def test_falls_back_to_page_count_when_patch_has_no_content_summary(self):
        result = summarize.doc_change_summary(row("Formatting", [{
            "filename": "articles/aks/scale-cluster.md",
            "status": "modified",
            "patch": "+::: zone pivot=\"azure-cli\"",
        }]))

        self.assertEqual(result, "Updated documentation page.")


if __name__ == "__main__":
    unittest.main()
