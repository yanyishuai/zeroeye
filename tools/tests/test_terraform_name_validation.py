#!/usr/bin/env python3
"""Tests for Terraform resource name validation."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.terraform_import import (
    ResourceToImport,
    TerraformImporter,
    TerraformResourceNameError,
    validate_resource,
    validate_resource_name,
)


class TerraformNameValidationTests(unittest.TestCase):
    def test_valid_name_passes(self) -> None:
        validate_resource_name("aws_instance", "web_server")

    def test_hyphenated_name_rejected(self) -> None:
        with self.assertRaises(TerraformResourceNameError) as ctx:
            validate_resource_name("aws_instance", "web-server")
        self.assertIn("aws_instance.web-server", str(ctx.exception))
        self.assertIn("hyphenated", str(ctx.exception))

    def test_invalid_identifier_rejected(self) -> None:
        with self.assertRaises(TerraformResourceNameError):
            validate_resource_name("aws_s3_bucket", "9starts_with_digit")

    def test_dry_run_rejects_invalid_csv_name(self) -> None:
        importer = TerraformImporter()
        resources = [
            ResourceToImport("aws_instance", "valid_name", "i-123"),
            ResourceToImport("aws_instance", "bad-name", "i-456"),
        ]
        result = importer.import_batch(resources, dry_run=True)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.skipped_count, 1)

    def test_generate_script_rejects_invalid_name(self) -> None:
        importer = TerraformImporter()
        resources = [ResourceToImport("aws_lb", "public-lb", "arn:lb")]
        with self.assertRaises(TerraformResourceNameError):
            importer.generate_import_script(resources, "import.sh")

    def test_csv_main_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "resources.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["type", "name", "id"])
                writer.writeheader()
                writer.writerow(
                    {"type": "aws_instance", "name": "legacy-host", "id": "i-abc"}
                )

            with mock.patch("tools.terraform_import.TerraformImporter.check_terraform_version", return_value=True):
                with mock.patch("sys.argv", ["terraform_import.py", "--csv", str(csv_path)]):
                    from tools import terraform_import

                    exit_code = terraform_import.main()
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
