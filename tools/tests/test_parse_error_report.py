#!/usr/bin/env python3
"""Tests for --parse-error-report in log_aggregator."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.log_aggregator import LogAggregator, sanitize_error_message


class ParseErrorReportTests(unittest.TestCase):
    def test_valid_json_does_not_create_failure(self) -> None:
        aggregator = LogAggregator(track_parse_errors=True)
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as handle:
            handle.write('{"level":"info","message":"ok"}\n')
            path = handle.name

        try:
            count = aggregator.process_file(path)
            self.assertEqual(count, 1)
            self.assertEqual(aggregator.parse_failures, [])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_malformed_json_is_reported(self) -> None:
        aggregator = LogAggregator(track_parse_errors=True)
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as handle:
            handle.write('{"level":"info","message":broken}\n')
            path = handle.name

        try:
            count = aggregator.process_file(path)
            self.assertEqual(count, 1)
            self.assertEqual(len(aggregator.parse_failures), 1)
            failure = aggregator.parse_failures[0]
            self.assertEqual(failure["parser"], "json")
            self.assertEqual(failure["line"], 1)
            self.assertIn("JSON decode error", failure["error"])
            self.assertNotIn("broken", failure["error"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_report_export_groups_by_file(self) -> None:
        aggregator = LogAggregator(track_parse_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "sample.log"
            log_path.write_text(
                "\n".join(
                    [
                        '{"level":"info","message":"ok"}',
                        '{"not":"closed"',
                        '127.0.0.1 - - [01/Jan/2024:00:00:00 +0000] "GET /" 200',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            aggregator.process_file(str(log_path))
            report_path = Path(tmp) / "parse-errors.json"
            aggregator.export_parse_error_report(str(report_path))
            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["total_failures"], 2)
        self.assertEqual(payload["files"][0]["file"], str(log_path))
        parsers = {item["parser"] for item in payload["files"][0]["failures"]}
        self.assertEqual(parsers, {"json", "nginx"})

    def test_sanitize_error_message_redacts_secrets(self) -> None:
        raw = "invalid token api_key=super-secret-value in payload"
        cleaned = sanitize_error_message(raw)
        self.assertIn("api_key=***", cleaned)
        self.assertNotIn("super-secret-value", cleaned)


if __name__ == "__main__":
    unittest.main()
