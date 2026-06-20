#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path

from ai_reviewer import AiCodeReviewer, ReviewerConfig


class AiReviewerConfigTest(unittest.TestCase):
    def test_ignore_patterns_exclude_paths_from_project_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-reviewer-ignore").write_text("generated/\n*.vendor.py\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "generated").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "generated" / "client.py").write_text("password = 'a' * 32\n", encoding="utf-8")
            (root / "src" / "sdk.vendor.py").write_text("password = 'b' * 32\n", encoding="utf-8")

            config = ReviewerConfig.load(root)
            report = AiCodeReviewer(config=config, verbose=True).review_directory(root, recursive=True)

            reviewed = {Path(result.file_path).name for result in report.file_results}
            self.assertEqual(reviewed, {"app.py"})
            self.assertEqual(report.total_files, 1)
            self.assertEqual(report.reviewed_files, 1)
            self.assertEqual(report.ignored_files, 2)

    def test_disabled_rules_remove_findings_from_results_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-reviewer.yml").write_text(
                "disabled_rules:\n"
                "  - SEC-HARDCODED-KEY\n"
                "  - STYLE-LINE-LENGTH\n",
                encoding="utf-8",
            )
            source = "password = 'abcdefghijklmnopqrstuvwxyz123456'\n" + ("x = '" + "a" * 120 + "'\n")
            (root / "app.py").write_text(source, encoding="utf-8")

            config = ReviewerConfig.load(root)
            reviewer = AiCodeReviewer(config=config, verbose=True)
            report = reviewer.review_directory(root, recursive=True)
            payload = json.loads(reviewer.generate_report_json(report))

            findings = report.file_results[0].findings
            rule_ids = {rule for finding in findings for rule in finding.rules}
            self.assertNotIn("SEC-HARDCODED-KEY", rule_ids)
            self.assertNotIn("STYLE-LINE-LENGTH", rule_ids)
            self.assertGreaterEqual(report.disabled_rule_findings, 2)
            self.assertEqual(payload["disabled_rule_findings"], report.disabled_rule_findings)

    def test_negated_ignore_pattern_reincludes_specific_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-reviewer-ignore").write_text("generated/\n!generated/keep.py\n", encoding="utf-8")
            (root / "generated").mkdir()
            ignored = root / "generated" / "client.py"
            kept = root / "generated" / "keep.py"
            ignored.write_text("print('ignored')\n", encoding="utf-8")
            kept.write_text("print('kept')\n", encoding="utf-8")

            config = ReviewerConfig.load(root)

            self.assertTrue(config.is_ignored(ignored))
            self.assertFalse(config.is_ignored(kept))


if __name__ == "__main__":
    unittest.main()
