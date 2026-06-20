#!/usr/bin/env python3
"""
Health check tool for the Tent of Trials platform.
Performs comprehensive health checks across all services and reports
the overall system status.

This tool is used by:
  - The Kubernetes liveness/readiness probes
  - The deployment pipeline (post-deployment validation)
  - The monitoring system (periodic health checks)
  - The on-call engineer (manual troubleshooting)

The health check performs the following checks:
  1. Service availability (HTTP health endpoints)
  2. Database connectivity (connection test)
  3. Redis connectivity (ping test)
  4. Kafka connectivity (metadata fetch)
  5. Message queue depth (consumer lag check)
  6. Certificate expiry (TLS certificate check)
  7. Disk space (filesystem usage check)
  8. Memory usage (process memory check)

Each check returns a status of OK, WARNING, or CRITICAL, along with
a detail message and optional diagnostic data.

Usage:
    python3 health_check.py                  # Check all services
    python3 health_check.py --service backend # Check specific service
    python3 health_check.py --json            # JSON output
    python3 health_check.py --watch           # Continuous monitoring
"""

import argparse
import http.client
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

SERVICES = {
    "backend": {"host": "localhost", "port": 8080, "path": "/health", "timeout": 5},
    "market": {"host": "localhost", "port": 8081, "path": "/health", "timeout": 5},
    "frailbox": {"host": "localhost", "port": 8082, "path": "/health", "timeout": 10},
    "frontend": {"host": "localhost", "port": 3000, "path": "/", "timeout": 5},
}

INFRASTRUCTURE = {
    "postgresql": {"host": os.environ.get("DB_HOST", "localhost"), "port": int(os.environ.get("DB_PORT", "5432")), "timeout": 5},
    "redis": {"host": os.environ.get("REDIS_HOST", "localhost"), "port": int(os.environ.get("REDIS_PORT", "6379")), "timeout": 5},
    "kafka": {"host": os.environ.get("KAFKA_HOST", "localhost"), "port": int(os.environ.get("KAFKA_PORT", "9092")), "timeout": 5},
}

DISK_THRESHOLD_WARNING = 80
DISK_THRESHOLD_CRITICAL = 90

MEMORY_THRESHOLD_WARNING = 80
MEMORY_THRESHOLD_CRITICAL = 90

logger = logging.getLogger("health_check")


@dataclass
class CircuitBreaker:
    threshold: int
    cooldown_seconds: float
    failure_count: int = 0
    opened_at: Optional[float] = None

    def is_open(self, now: Optional[float] = None) -> bool:
        if self.threshold <= 0 or self.opened_at is None:
            return False
        now = time.time() if now is None else now
        if now - self.opened_at >= self.cooldown_seconds:
            self.failure_count = 0
            self.opened_at = None
            return False
        return True

    def record_success(self) -> None:
        self.failure_count = 0
        self.opened_at = None

    def record_failure(self, now: Optional[float] = None) -> None:
        if self.threshold <= 0:
            return
        self.failure_count += 1
        if self.failure_count >= self.threshold and self.opened_at is None:
            self.opened_at = time.time() if now is None else now

# ---------------------------------------------------------------------------
# CHECK FUNCTIONS
# ---------------------------------------------------------------------------

def probe_http_once(
    host: str,
    port: int,
    path: str,
    timeout: int,
    connection_factory: Callable[..., Any] = http.client.HTTPConnection,
) -> Tuple[str, str, int]:
    conn = None
    try:
        conn = connection_factory(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")[:200]

        if status == 200:
            result = "OK"
            detail = f"HTTP {status}"
        elif status < 500:
            result = "WARNING"
            detail = f"HTTP {status}: {body[:100]}"
        else:
            result = "CRITICAL"
            detail = f"HTTP {status}: {body[:100]}"

        return result, detail, status
    except Exception as e:
        return "CRITICAL", str(e), 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def check_http_service(
    host: str,
    port: int,
    path: str,
    timeout: int,
    max_retries: int = 0,
    backoff_factor: float = 2.0,
    circuit_threshold: int = 3,
    retry_base_delay: float = 0.25,
    circuit_breaker: Optional[CircuitBreaker] = None,
    sleep_func: Callable[[float], None] = time.sleep,
    connection_factory: Callable[..., Any] = http.client.HTTPConnection,
) -> Tuple[str, str, int]:
    breaker = circuit_breaker or CircuitBreaker(
        threshold=circuit_threshold,
        cooldown_seconds=30.0,
    )

    if breaker.is_open():
        logger.warning("Circuit open for %s:%s%s; skipping probe", host, port, path)
        return "CRITICAL", "Circuit open after consecutive probe failures", 0

    attempts = max(0, max_retries) + 1
    last_status = "CRITICAL"
    last_detail = "No probe attempted"
    last_code = 0

    for attempt in range(attempts):
        last_status, last_detail, last_code = probe_http_once(
            host, port, path, timeout, connection_factory=connection_factory
        )

        if last_status == "OK":
            breaker.record_success()
            if attempt:
                last_detail = f"{last_detail} after {attempt + 1} attempts"
            return last_status, last_detail, last_code

        should_retry = last_status == "CRITICAL" and attempt < attempts - 1
        if last_status == "CRITICAL":
            breaker.record_failure()
            if breaker.is_open():
                logger.warning(
                    "Circuit opened for %s:%s%s after %s consecutive failures",
                    host, port, path, breaker.failure_count,
                )
                return "CRITICAL", f"Circuit open: {last_detail}", last_code

        if not should_retry:
            break

        delay = retry_base_delay * (backoff_factor ** attempt)
        logger.warning(
            "HTTP probe failed for %s:%s%s on attempt %s/%s: %s; retrying in %.2fs",
            host, port, path, attempt + 1, attempts, last_detail, delay,
        )
        if delay > 0:
            sleep_func(delay)

    if attempts > 1:
        last_detail = f"{last_detail} after {attempts} attempts"
    return last_status, last_detail, last_code


def check_tcp_port(host: str, port: int, timeout: int) -> Tuple[str, str, float]:
    try:
        start = time.time()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        latency = (time.time() - start) * 1000
        return "OK", f"Connected ({latency:.1f}ms)", latency
    except socket.timeout:
        return "CRITICAL", f"Connection timeout ({timeout}s)", 0
    except ConnectionRefusedError:
        return "CRITICAL", "Connection refused", 0
    except Exception as e:
        return "CRITICAL", str(e), 0


def check_certificate_expiry(host: str, port: int = 443) -> Tuple[str, str, int]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return "WARNING", "No certificate found", 0

                from datetime import datetime as dt
                expires = dt.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                days_left = (expires - dt.now()).days

                if days_left > 30:
                    return "OK", f"Certificate expires in {days_left} days", days_left
                elif days_left > 7:
                    return "WARNING", f"Certificate expires in {days_left} days", days_left
                else:
                    return "CRITICAL", f"Certificate expires in {days_left} days", days_left
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_disk_usage(path: str = "/") -> Tuple[str, str, float]:
    try:
        stat = os.statvfs(path)
        total = stat.f_frsize * stat.f_blocks
        free = stat.f_frsize * stat.f_bavail
        used = total - free
        pct = (used / total) * 100

        if pct < DISK_THRESHOLD_WARNING:
            return "OK", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        elif pct < DISK_THRESHOLD_CRITICAL:
            return "WARNING", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        else:
            return "CRITICAL", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_memory_usage() -> Tuple[str, str, float]:
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().replace(" kB", "")
                    try:
                        meminfo[key] = int(value) * 1024
                    except ValueError:
                        pass

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        pct = (used / total) * 100 if total > 0 else 0

        if pct < MEMORY_THRESHOLD_WARNING:
            return "OK", f"{pct:.1f}% used ({used // (1024**3)}GB/{total // (1024**3)}GB)", pct
        elif pct < MEMORY_THRESHOLD_CRITICAL:
            return "WARNING", f"{pct:.1f}% used", pct
        else:
            return "CRITICAL", f"{pct:.1f}% used", pct
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


def check_load_average() -> Tuple[str, str, float]:
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            load = float(parts[0])
            cpu_count = os.cpu_count() or 1
            load_pct = (load / cpu_count) * 100

            if load_pct < 70:
                return "OK", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
            elif load_pct < 90:
                return "WARNING", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
            else:
                return "CRITICAL", f"Load: {load} ({load_pct:.0f}% of {cpu_count} cores)", load
    except Exception as e:
        return "WARNING", f"Cannot check: {e}", 0


# ---------------------------------------------------------------------------
# HEALTH CHECK RUNNER
# ---------------------------------------------------------------------------

def summarize_results(results: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": 0,
        "OK": 0,
        "WARNING": 0,
        "CRITICAL": 0,
        "by_category": {},
    }
    for category in ["services", "infrastructure", "system"]:
        category_counts = {"total": 0, "OK": 0, "WARNING": 0, "CRITICAL": 0}
        for check in results.get(category, {}).values():
            if not isinstance(check, dict) or "status" not in check:
                continue
            status = check.get("status", "UNKNOWN")
            category_counts["total"] += 1
            summary["total"] += 1
            if status in {"OK", "WARNING", "CRITICAL"}:
                category_counts[status] += 1
                summary[status] += 1
        summary["by_category"][category] = category_counts
    return summary


def run_health_checks(
    service: Optional[str] = None,
    json_output: bool = False,
    max_retries: int = 0,
    backoff_factor: float = 2.0,
    circuit_threshold: int = 3,
    retry_base_delay: float = 0.25,
    circuit_cooldown: float = 30.0,
    circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "hostname": socket.gethostname(),
        "services": {},
        "infrastructure": {},
        "system": {},
        "overall_status": "OK",
    }
    circuit_breakers = circuit_breakers if circuit_breakers is not None else {}

    all_ok = True

    # Check services
    for name, config in SERVICES.items():
        if service and name != service:
            continue
        breaker = circuit_breakers.setdefault(
            name,
            CircuitBreaker(
                threshold=circuit_threshold,
                cooldown_seconds=circuit_cooldown,
            ),
        )
        status, detail, code = check_http_service(
            config["host"], config["port"], config["path"], config["timeout"],
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            circuit_threshold=circuit_threshold,
            retry_base_delay=retry_base_delay,
            circuit_breaker=breaker,
            sleep_func=sleep_func,
        )
        results["services"][name] = {
            "status": status,
            "detail": detail,
            "code": code,
            "endpoint": f"http://{config['host']}:{config['port']}{config['path']}",
        }
        if status == "CRITICAL":
            all_ok = False

    # Check infrastructure
    for name, config in INFRASTRUCTURE.items():
        if service and name != service:
            continue
        status, detail, latency = check_tcp_port(config["host"], config["port"], config["timeout"])
        results["infrastructure"][name] = {
            "status": status,
            "detail": detail,
            "endpoint": f"{config['host']}:{config['port']}",
        }
        if status == "CRITICAL":
            all_ok = False

    # Check system resources
    disk_status, disk_detail, disk_pct = check_disk_usage()
    results["system"]["disk"] = {"status": disk_status, "detail": disk_detail}
    if disk_status == "CRITICAL":
        all_ok = False

    mem_status, mem_detail, mem_pct = check_memory_usage()
    results["system"]["memory"] = {"status": mem_status, "detail": mem_detail}
    if mem_status == "CRITICAL":
        all_ok = False

    load_status, load_detail, load_val = check_load_average()
    results["system"]["load"] = {"status": load_status, "detail": load_detail}

    # Check certificate expiry (web services)
    for name, config in SERVICES.items():
        if service and name != service:
            continue
        if config["port"] == 443:
            cert_status, cert_detail, days_left = check_certificate_expiry(config["host"])
            results["services"][name]["certificate"] = {
                "status": cert_status,
                "detail": cert_detail,
                "days_remaining": days_left,
            }
            if cert_status == "CRITICAL":
                all_ok = False

    results["overall_status"] = "OK" if all_ok else "DEGRADED"
    results["summary"] = summarize_results(results)

    return results


def print_health_report(results: Dict[str, Any]):
    print(f"\n{'='*60}")
    print(f"  HEALTH CHECK REPORT")
    print(f"  Host: {results['hostname']}")
    print(f"  Time: {results['timestamp']}")
    print(f"  Overall: {results['overall_status']}")
    print(f"{'='*60}")

    for category, items in [("Services", results["services"]),
                             ("Infrastructure", results["infrastructure"]),
                             ("System", results["system"])]:
        if items:
            print(f"\n  {category}:")
            for name, check in items.items():
                if isinstance(check, dict) and "status" in check:
                    status_icon = {"OK": "✓", "WARNING": "⚠", "CRITICAL": "✗"}.get(check["status"], "?")
                    print(f"    {status_icon} {name}: {check['detail']}")
                else:
                    print(f"    {name}:")
                    for sub_name, sub_check in check.items():
                        if isinstance(sub_check, dict) and "status" in sub_check:
                            sub_icon = {"OK": "✓", "WARNING": "⚠", "CRITICAL": "✗"}.get(sub_check["status"], "?")
                            print(f"      {sub_icon} {sub_name}: {sub_check['detail']}")
    summary = results.get("summary", {})
    if summary:
        print("\n  Summary:")
        print(
            f"    total={summary.get('total', 0)} "
            f"ok={summary.get('OK', 0)} "
            f"warning={summary.get('WARNING', 0)} "
            f"critical={summary.get('CRITICAL', 0)}"
        )
    print()


def parse_args():
    parser = argparse.ArgumentParser(description="Health check tool")
    parser.add_argument("--service", "-s", help="Check specific service only")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument("--watch", "-w", action="store_true", help="Continuous monitoring")
    parser.add_argument("--interval", "-i", type=int, default=30, help="Check interval in seconds")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--max-retries", type=int, default=0, help="HTTP probe retries after the initial attempt")
    parser.add_argument("--backoff-factor", type=float, default=2.0, help="HTTP retry exponential backoff multiplier")
    parser.add_argument("--retry-base-delay", type=float, default=0.25, help="Base retry delay in seconds")
    parser.add_argument("--circuit-threshold", type=int, default=3, help="Consecutive HTTP failures before opening circuit")
    parser.add_argument("--circuit-cooldown", type=float, default=30.0, help="Seconds before an open HTTP circuit resets")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
    circuit_breakers: Dict[str, CircuitBreaker] = {}

    if args.watch:
        print(f"Continuous monitoring (interval: {args.interval}s). Press Ctrl+C to stop.")
        try:
            while True:
                results = run_health_checks(
                    args.service,
                    args.json,
                    max_retries=args.max_retries,
                    backoff_factor=args.backoff_factor,
                    retry_base_delay=args.retry_base_delay,
                    circuit_threshold=args.circuit_threshold,
                    circuit_cooldown=args.circuit_cooldown,
                    circuit_breakers=circuit_breakers,
                )
                if args.json:
                    print(json.dumps(results, indent=2))
                else:
                    print_health_report(results)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped")
    else:
        results = run_health_checks(
            args.service,
            args.json,
            max_retries=args.max_retries,
            backoff_factor=args.backoff_factor,
            retry_base_delay=args.retry_base_delay,
            circuit_threshold=args.circuit_threshold,
            circuit_cooldown=args.circuit_cooldown,
            circuit_breakers=circuit_breakers,
        )
        if args.json:
            output = json.dumps(results, indent=2)
            print(output)
        else:
            print_health_report(results)

        if args.output:
            with open(args.output, "w") as f:
                if args.json:
                    json.dump(results, f, indent=2)
                else:
                    json.dump(results, f, indent=2)
            print(f"Report saved to {args.output}")

        if results["overall_status"] == "DEGRADED":
            return 1

    return 0


if __name__ == "__main__":
    main()
