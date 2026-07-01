#!/usr/bin/env python3
"""Tests for monitoring alert expression validation."""

from __future__ import annotations

import unittest

from tools.monitoring_setup import (
    RECOMMENDED_ALERT_RULES,
    find_self_dividing_expressions,
    validate_alert_rules,
)


class MonitoringAlertExprTests(unittest.TestCase):
    def test_high_memory_usage_uses_machine_memory_denominator(self) -> None:
        rule = next(r for r in RECOMMENDED_ALERT_RULES if r["name"] == "HighMemoryUsage")
        self.assertIn("machine_memory_bytes", rule["expr"])
        self.assertNotIn(
            "process_resident_memory_bytes / process_resident_memory_bytes",
            rule["expr"],
        )

    def test_recommended_rules_have_no_self_dividing_expressions(self) -> None:
        problems = find_self_dividing_expressions(RECOMMENDED_ALERT_RULES)
        self.assertEqual(problems, [])

    def test_validator_flags_self_dividing_expression(self) -> None:
        rules = [
            {
                "name": "BrokenMemory",
                "expr": "process_resident_memory_bytes / process_resident_memory_bytes > 0.9",
            }
        ]
        problems = validate_alert_rules(rules)
        self.assertEqual(len(problems), 1)
        self.assertIn("BrokenMemory", problems[0])


if __name__ == "__main__":
    unittest.main()
