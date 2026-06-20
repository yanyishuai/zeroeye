#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

import benchmark


def make_result(**overrides):
    data = {
        "benchmark_type": "latency",
        "start_time": 1.0,
        "end_time": 2.0,
        "duration_seconds": 1.0,
        "total_requests": 10,
        "successful_requests": 10,
        "failed_requests": 0,
        "timeout_requests": 0,
        "requests_per_second": 10.0,
        "latency_ms": {
            "min": 5.0,
            "p50": 10.0,
            "p90": 15.0,
            "p95": 20.0,
            "p99": 30.0,
            "max": 40.0,
            "avg": 12.0,
            "stddev": 2.0,
        },
        "error_distribution": {},
        "target_endpoint": "http://localhost:8080/health",
        "concurrency": 1,
    }
    data.update(overrides)
    return benchmark.BenchmarkResult(**data)


class BenchmarkBaselineTests(unittest.TestCase):
    def test_write_and_load_baseline_is_deterministic_json(self):
        result = make_result()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "baseline.json")
            benchmark.write_baseline(path, result)
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)

        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["benchmark_type"], "latency")
        self.assertEqual(loaded["requests_per_second"], 10.0)
        self.assertEqual(loaded["latency_ms"]["p95"], 20.0)

    def test_compare_results_marks_latency_regression(self):
        baseline = benchmark.benchmark_result_to_dict(make_result())
        current = make_result(latency_ms={**baseline["latency_ms"], "p95": 30.0})

        summary = benchmark.compare_results(
            current,
            baseline,
            "baseline.json",
            fail_regression_percent=25.0,
        )
        p95 = next(item for item in summary.comparisons if item.metric == "latency_ms.p95")

        self.assertTrue(p95.is_regression)
        self.assertAlmostEqual(p95.absolute_change, 10.0)
        self.assertAlmostEqual(p95.percent_change, 50.0)
        self.assertTrue(summary.failed_regression_threshold)

    def test_compare_results_marks_throughput_regression(self):
        baseline = benchmark.benchmark_result_to_dict(make_result())
        current = make_result(requests_per_second=8.0)

        summary = benchmark.compare_results(
            current,
            baseline,
            "baseline.json",
            fail_regression_percent=10.0,
        )
        rps = next(item for item in summary.comparisons if item.metric == "requests_per_second")

        self.assertTrue(rps.is_regression)
        self.assertAlmostEqual(rps.percent_change, -20.0)
        self.assertAlmostEqual(rps.regression_percent, 20.0)
        self.assertTrue(summary.failed_regression_threshold)

    def test_compare_results_allows_improvements(self):
        baseline = benchmark.benchmark_result_to_dict(make_result())
        current = make_result(
            requests_per_second=12.0,
            latency_ms={**baseline["latency_ms"], "p95": 15.0},
        )

        summary = benchmark.compare_results(
            current,
            baseline,
            "baseline.json",
            fail_regression_percent=1.0,
        )

        self.assertFalse(summary.failed_regression_threshold)
        self.assertFalse(any(item.is_regression for item in summary.comparisons))

    def test_missing_baseline_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "baseline.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"benchmark_type": "latency"}, f)

            with self.assertRaisesRegex(ValueError, "missing required keys"):
                benchmark.load_baseline(path)


if __name__ == "__main__":
    unittest.main()
