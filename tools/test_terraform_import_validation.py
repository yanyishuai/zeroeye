#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from terraform_import import (
    InvalidTerraformResourceName,
    ResourceToImport,
    TerraformImporter,
    load_resources_from_csv,
    terraform_address_for,
)


class TerraformImportResourceNameValidationTests(unittest.TestCase):
    def test_valid_resource_name_builds_address(self):
        resource = ResourceToImport(
            resource_type="aws_instance",
            resource_name="web_server_01",
            resource_id="i-1234567890abcdef0",
        )

        self.assertEqual(terraform_address_for(resource), "aws_instance.web_server_01")
        self.assertEqual(resource.terraform_address, "aws_instance.web_server_01")

    def test_hyphenated_name_is_rejected_before_import(self):
        importer = TerraformImporter(terraform_binary="/missing/terraform")
        resource = ResourceToImport(
            resource_type="aws_instance",
            resource_name="web-server",
            resource_id="i-1234567890abcdef0",
        )

        self.assertFalse(importer.import_resource(resource))
        self.assertEqual(importer.results[0]["status"], "invalid")
        self.assertIn("aws_instance.web-server", importer.results[0]["error"])
        self.assertIn("Use underscores instead of hyphens", importer.results[0]["error"])

    def test_generate_script_rejects_invalid_name_without_output(self):
        importer = TerraformImporter()
        resources = [
            ResourceToImport(
                resource_type="aws_instance",
                resource_name="web-server",
                resource_id="i-1234567890abcdef0",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "import.sh"
            with self.assertRaises(InvalidTerraformResourceName):
                importer.generate_import_script(resources, str(output_file))
            self.assertFalse(output_file.exists())

    def test_csv_driven_import_preserves_valid_names_and_rejects_invalid(self):
        csv_body = (
            "type,name,id,state_file\n"
            "aws_instance,web_server,i-1234567890abcdef0,terraform.tfstate\n"
            "aws_security_group,app-sg,sg-1234567890abcdef0,network.tfstate\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_file = Path(temp_dir) / "imports.csv"
            csv_file.write_text(csv_body)

            resources = load_resources_from_csv(str(csv_file))

        self.assertEqual(len(resources), 2)
        self.assertEqual(terraform_address_for(resources[0]), "aws_instance.web_server")
        with self.assertRaises(InvalidTerraformResourceName):
            terraform_address_for(resources[1])

    def test_dry_run_reports_invalid_resource_as_failure(self):
        importer = TerraformImporter(terraform_binary="/missing/terraform")
        result = importer.import_batch(
            [
                ResourceToImport(
                    resource_type="aws_instance",
                    resource_name="web_server",
                    resource_id="i-1234567890abcdef0",
                ),
                ResourceToImport(
                    resource_type="aws_instance",
                    resource_name="web-server",
                    resource_id="i-abcdef01234567890",
                ),
            ],
            dry_run=True,
        )

        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.results[1]["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
