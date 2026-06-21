#!/usr/bin/env python3

import json
import subprocess
import sys
import unittest
from pathlib import Path

from validate_bounty_issue import validate_issue_body


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tools" / "fixtures" / "bounty_issues"
SCRIPT = ROOT / "tools" / "validate_bounty_issue.py"


class ValidateBountyIssueTest(unittest.TestCase):
    def read_fixture(self, name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    def test_valid_issue_body_passes(self) -> None:
        result = validate_issue_body(self.read_fixture("valid.md"), "valid.md")

        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_missing_required_section_fails(self) -> None:
        result = validate_issue_body(self.read_fixture("missing_required_validation.md"), "bad.md")

        self.assertFalse(result.ok)
        self.assertIn("missing required section: Required validation:", result.errors)

    def test_changed_commissions_text_fails(self) -> None:
        result = validate_issue_body(self.read_fixture("invalid_commissions.md"), "bad.md")

        self.assertFalse(result.ok)
        self.assertIn("commissions paragraph does not exactly match required text", result.errors)

    def test_validation_without_real_logd_exclusion_fails(self) -> None:
        result = validate_issue_body(self.read_fixture("invalid_validation.md"), "bad.md")

        self.assertFalse(result.ok)
        self.assertIn(
            "required validation must mention a generated/real .logd diagnostic and exclude build-00000000",
            result.errors,
        )

    def test_cli_json_reports_all_files(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--json",
                str(FIXTURES / "valid.md"),
                str(FIXTURES / "invalid_validation.md"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(len(payload), 2)
        self.assertTrue(payload[0]["ok"])
        self.assertFalse(payload[1]["ok"])


if __name__ == "__main__":
    unittest.main()
