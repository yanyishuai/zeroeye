#!/usr/bin/env python3

import unittest
from typing import ClassVar

import health_check


class FakeResponse:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body


class SequencedConnection:
    outcomes: ClassVar[list] = []
    calls: ClassVar[int] = 0

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def request(self, method, path):
        self.method = method
        self.path = path

    def getresponse(self):
        type(self).calls += 1
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        pass

    @classmethod
    def reset(cls, outcomes):
        cls.outcomes = list(outcomes)
        cls.calls = 0


class HealthCheckRetryCircuitTests(unittest.TestCase):
    def test_http_probe_retries_until_success(self):
        SequencedConnection.reset([
            ConnectionRefusedError("refused"),
            FakeResponse(200),
        ])
        sleeps = []

        status, detail, code = health_check.check_http_service(
            "svc", 8080, "/health", 1,
            max_retries=1,
            retry_base_delay=0.5,
            backoff_factor=2,
            sleep_func=sleeps.append,
            connection_factory=SequencedConnection,
        )

        self.assertEqual(status, "OK")
        self.assertEqual(code, 200)
        self.assertIn("after 2 attempts", detail)
        self.assertEqual(sleeps, [0.5])

    def test_exponential_backoff_uses_attempt_number(self):
        SequencedConnection.reset([
            ConnectionRefusedError("first"),
            ConnectionRefusedError("second"),
            FakeResponse(200),
        ])
        sleeps = []

        status, _detail, _code = health_check.check_http_service(
            "svc", 8080, "/health", 1,
            max_retries=2,
            retry_base_delay=0.25,
            backoff_factor=3,
            sleep_func=sleeps.append,
            connection_factory=SequencedConnection,
        )

        self.assertEqual(status, "OK")
        self.assertEqual(sleeps, [0.25, 0.75])

    def test_circuit_opens_after_consecutive_failures(self):
        SequencedConnection.reset([
            ConnectionRefusedError("first"),
            ConnectionRefusedError("second"),
            FakeResponse(200),
        ])

        status, detail, code = health_check.check_http_service(
            "svc", 8080, "/health", 1,
            max_retries=2,
            circuit_threshold=2,
            retry_base_delay=0,
            sleep_func=lambda _delay: None,
            connection_factory=SequencedConnection,
        )

        self.assertEqual(status, "CRITICAL")
        self.assertEqual(code, 0)
        self.assertIn("Circuit open", detail)
        self.assertEqual(SequencedConnection.calls, 2)

    def test_open_circuit_skips_probe_until_cooldown(self):
        breaker = health_check.CircuitBreaker(threshold=1, cooldown_seconds=30)
        breaker.record_failure()
        SequencedConnection.reset([FakeResponse(200)])

        status, detail, code = health_check.check_http_service(
            "svc", 8080, "/health", 1,
            circuit_breaker=breaker,
            connection_factory=SequencedConnection,
        )

        self.assertEqual(status, "CRITICAL")
        self.assertEqual(code, 0)
        self.assertIn("Circuit open", detail)
        self.assertEqual(SequencedConnection.calls, 0)

    def test_warning_response_does_not_open_circuit(self):
        breaker = health_check.CircuitBreaker(threshold=1, cooldown_seconds=30)
        SequencedConnection.reset([FakeResponse(404, "missing")])

        status, detail, code = health_check.check_http_service(
            "svc", 8080, "/health", 1,
            circuit_breaker=breaker,
            connection_factory=SequencedConnection,
        )

        self.assertEqual(status, "WARNING")
        self.assertEqual(code, 404)
        self.assertIn("HTTP 404", detail)
        self.assertEqual(breaker.failure_count, 0)
        self.assertFalse(breaker.is_open())

    def test_circuit_resets_after_cooldown(self):
        breaker = health_check.CircuitBreaker(threshold=1, cooldown_seconds=1)
        breaker.record_failure(now=100)

        self.assertTrue(breaker.is_open(now=100.5))
        self.assertFalse(breaker.is_open(now=101.1))
        self.assertEqual(breaker.failure_count, 0)

    def test_summary_counts_statuses_by_category(self):
        results = {
            "services": {
                "backend": {"status": "OK"},
                "market": {"status": "CRITICAL"},
            },
            "infrastructure": {
                "redis": {"status": "WARNING"},
            },
            "system": {
                "disk": {"status": "OK"},
            },
        }

        summary = health_check.summarize_results(results)

        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["OK"], 2)
        self.assertEqual(summary["WARNING"], 1)
        self.assertEqual(summary["CRITICAL"], 1)
        self.assertEqual(summary["by_category"]["services"]["CRITICAL"], 1)


if __name__ == "__main__":
    unittest.main()
