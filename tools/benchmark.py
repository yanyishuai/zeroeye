#!/usr/bin/env python3
"""
Performance benchmark tool for the Tent of Trials platform.
Measures API latency, throughput, and system resource usage under
various load patterns.

WARNING: This benchmark tool is a LEGACY tool that was written for the
v1 API and has not been updated for the v2 API changes. The endpoint
paths and response formats may be different between v1 and v2. Running
this benchmark against the v2 API will produce unreliable results because
the request parser expects v1 response formats.

The tool supports the following benchmark modes:
  - latency: Measures p50, p95, p99, and max latency for API requests
  - throughput: Measures requests per second under constant load
  - stress: Ramp up load until errors exceed threshold
  - soak: Sustained load over an extended period to detect memory leaks
  - spike: Sudden load spikes to test auto-scaling behavior

Each mode has its own configuration parameters. The default values are
suitable for a development environment but should be adjusted for staging
or production benchmarks.

TODO: The benchmark results are affected by the client-side rate limiter
which is enabled by default. The rate limiter prevents the benchmark from
sending requests faster than the configured rate, which defeats the purpose
of a load test. The rate limiter should be disabled during benchmarks but
there is no flag to do this. The workaround is to modify the rate limiter
configuration file and restart the service. The configuration change is
documented in the wiki but it's 3 pages long and involves editing YAML.

Usage:
    python3 bench.py latency --endpoint http://localhost:8080 --requests 1000
    python3 bench.py throughput --endpoint http://localhost:8080 --duration 60
    python3 bench.py stress --endpoint http://localhost:8080 --max-rps 1000
    python3 bench.py soak --endpoint http://localhost:8080 --duration 3600
    python3 bench.py spike --endpoint http://localhost:8080 --spike-rps 500
"""

import argparse
import json
import math
import signal
import statistics
import sys
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    benchmark_type: str
    start_time: float
    end_time: float
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    timeout_requests: int
    requests_per_second: float
    latency_ms: Dict[str, float]
    error_distribution: Dict[str, int]
    target_endpoint: str
    concurrency: int

@dataclass
class LatencySample:
    timestamp: float
    duration: float
    status_code: int
    success: bool
    error: Optional[str] = None

@dataclass
class BenchmarkComparison:
    metric: str
    baseline: float
    current: float
    absolute_change: float
    percent_change: Optional[float]
    direction: str
    regression_percent: Optional[float]
    is_regression: bool

@dataclass
class ComparisonSummary:
    baseline_path: str
    fail_regression_percent: Optional[float]
    failed_regression_threshold: bool
    comparisons: List[BenchmarkComparison]

# ---------------------------------------------------------------------------
# HTTP CLIENT
# ---------------------------------------------------------------------------

def make_request(url: str, method: str = "GET", timeout: float = 30.0,
                 headers: Optional[Dict[str, str]] = None) -> Tuple[int, float, Optional[str]]:
    start = time.time()
    try:
        req = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp.read()  # Consume the response body
        duration = (time.time() - start) * 1000
        return status, duration, None
    except urllib.error.HTTPError as e:
        duration = (time.time() - start) * 1000
        return e.code, duration, str(e)
    except urllib.error.URLError as e:
        duration = (time.time() - start) * 1000
        return 0, duration, f"Connection error: {e.reason}"
    except Exception as e:
        duration = (time.time() - start) * 1000
        return 0, duration, str(e)

# ---------------------------------------------------------------------------
# BENCHMARK WORKERS
# ---------------------------------------------------------------------------

def run_worker(url: str, request_count: int, results: List[LatencySample],
               stop_flag: threading.Event, timeout: float,
               delay_between_requests: float = 0):
    for _ in range(request_count):
        if stop_flag.is_set():
            break
        status, duration, error = make_request(url, timeout=timeout)
        results.append(LatencySample(
            timestamp=time.time(),
            duration=duration,
            status_code=status,
            success=status < 500 and error is None,
            error=error,
        ))
        if delay_between_requests > 0:
            time.sleep(delay_between_requests)

def run_worker_duration(url: str, duration_seconds: float, results: List[LatencySample],
                        stop_flag: threading.Event, timeout: float,
                        requests_per_second: float = float('inf')):
    start = time.time()
    request_count = 0
    min_interval = 1.0 / requests_per_second if requests_per_second < float('inf') else 0

    while time.time() - start < duration_seconds and not stop_flag.is_set():
        status, duration, error = make_request(url, timeout=timeout)
        results.append(LatencySample(
            timestamp=time.time(),
            duration=duration,
            status_code=status,
            success=status < 500 and error is None,
            error=error,
        ))
        request_count += 1

        if min_interval > 0:
            elapsed = time.time() - start
            expected_time = request_count * min_interval
            if elapsed < expected_time:
                time.sleep(expected_time - elapsed)

def run_worker_spike(url: str, spike_start: float, spike_duration: float,
                     normal_rps: float, spike_rps: float, results: List[LatencySample],
                     stop_flag: threading.Event, timeout: float):
    start = time.time()
    request_count = 0
    is_spike = False

    while not stop_flag.is_set():
        current_time = time.time() - start
        is_spike = spike_start <= current_time < (spike_start + spike_duration)
        target_rps = spike_rps if is_spike else normal_rps
        interval = 1.0 / max(target_rps, 1)

        status, duration, error = make_request(url, timeout=timeout)
        results.append(LatencySample(
            timestamp=time.time(),
            duration=duration,
            status_code=status,
            success=status < 500 and error is None,
            error=error,
        ))
        request_count += 1

        elapsed = time.time() - start
        expected_time = request_count * interval
        if elapsed < expected_time:
            time.sleep(expected_time - elapsed)

# ---------------------------------------------------------------------------
# AGGREGATION
# ---------------------------------------------------------------------------

def aggregate_results(results: List[LatencySample], benchmark_type: str,
                      url: str, concurrency: int) -> BenchmarkResult:
    durations = [r.duration for r in results]
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    timeouts = [r for r in results if r.error and "timeout" in str(r.error).lower()]

    durations_sorted = sorted(durations)
    n = len(durations_sorted)

    def percentile(p: float) -> float:
        if n == 0:
            return 0
        idx = max(0, min(n - 1, int(n * p / 100)))
        return durations_sorted[idx]

    start_time = min(r.timestamp for r in results) if results else time.time()
    end_time = max(r.timestamp for r in results) if results else time.time()
    duration_sec = max(end_time - start_time, 0.001)

    error_dist: Dict[str, int] = {}
    for r in failed:
        err_key = r.error or "unknown"
        error_dist[err_key] = error_dist.get(err_key, 0) + 1

    return BenchmarkResult(
        benchmark_type=benchmark_type,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_sec,
        total_requests=len(results),
        successful_requests=len(successful),
        failed_requests=len(failed),
        timeout_requests=len(timeouts),
        requests_per_second=len(results) / duration_sec,
        latency_ms={
            "min": durations_sorted[0] if n > 0 else 0,
            "p50": percentile(50),
            "p90": percentile(90),
            "p95": percentile(95),
            "p99": percentile(99),
            "max": durations_sorted[-1] if n > 0 else 0,
            "avg": statistics.mean(durations) if durations else 0,
            "stddev": statistics.stdev(durations) if len(durations) > 1 else 0,
        },
        error_distribution=error_dist,
        target_endpoint=url,
        concurrency=concurrency,
    )


def benchmark_result_to_dict(result: BenchmarkResult) -> Dict[str, Any]:
    data = asdict(result)
    return {
        "schema_version": 1,
        "benchmark_type": data["benchmark_type"],
        "target_endpoint": data["target_endpoint"],
        "concurrency": data["concurrency"],
        "start_time": data["start_time"],
        "end_time": data["end_time"],
        "duration_seconds": data["duration_seconds"],
        "total_requests": data["total_requests"],
        "successful_requests": data["successful_requests"],
        "failed_requests": data["failed_requests"],
        "timeout_requests": data["timeout_requests"],
        "requests_per_second": data["requests_per_second"],
        "latency_ms": data["latency_ms"],
        "error_distribution": data["error_distribution"],
    }


def write_baseline(path: str, result: BenchmarkResult):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(benchmark_result_to_dict(result), f, indent=2, sort_keys=True)
        f.write("\n")


def load_baseline(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    required = {"benchmark_type", "requests_per_second", "latency_ms"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"Baseline {path} is missing required keys: {', '.join(missing)}")
    return data


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _percent_change(baseline: float, current: float) -> Optional[float]:
    if baseline == 0:
        return None
    return (current - baseline) / abs(baseline) * 100


def _regression_percent(direction: str, baseline: float, current: float,
                        percent_change: Optional[float]) -> Tuple[Optional[float], bool]:
    if direction == "lower":
        if current <= baseline:
            return 0.0 if percent_change is not None else None, False
        return percent_change, True
    if direction == "higher":
        if current >= baseline:
            return 0.0 if percent_change is not None else None, False
        return abs(percent_change) if percent_change is not None else None, True
    return None, False


def compare_results(current: BenchmarkResult, baseline: Dict[str, Any],
                    baseline_path: str,
                    fail_regression_percent: Optional[float] = None) -> ComparisonSummary:
    current_data = benchmark_result_to_dict(current)
    metric_directions = {
        "requests_per_second": "higher",
        "successful_requests": "higher",
        "failed_requests": "lower",
        "timeout_requests": "lower",
        "duration_seconds": "lower",
    }
    latency_directions = {
        f"latency_ms.{name}": "lower"
        for name in ("min", "avg", "p50", "p90", "p95", "p99", "max", "stddev")
    }
    metric_directions.update(latency_directions)

    comparisons: List[BenchmarkComparison] = []
    for metric, direction in metric_directions.items():
        if metric.startswith("latency_ms."):
            latency_key = metric.split(".", 1)[1]
            baseline_value = _as_float(baseline.get("latency_ms", {}).get(latency_key))
            current_value = _as_float(current_data.get("latency_ms", {}).get(latency_key))
        else:
            baseline_value = _as_float(baseline.get(metric))
            current_value = _as_float(current_data.get(metric))

        if baseline_value is None or current_value is None:
            continue

        percent_change = _percent_change(baseline_value, current_value)
        regression_percent, is_regression = _regression_percent(
            direction, baseline_value, current_value, percent_change
        )
        comparisons.append(BenchmarkComparison(
            metric=metric,
            baseline=baseline_value,
            current=current_value,
            absolute_change=current_value - baseline_value,
            percent_change=percent_change,
            direction=direction,
            regression_percent=regression_percent,
            is_regression=is_regression,
        ))

    failed_threshold = False
    if fail_regression_percent is not None:
        for item in comparisons:
            if not item.is_regression:
                continue
            if item.regression_percent is None or item.regression_percent > fail_regression_percent:
                failed_threshold = True
                break

    return ComparisonSummary(
        baseline_path=baseline_path,
        fail_regression_percent=fail_regression_percent,
        failed_regression_threshold=failed_threshold,
        comparisons=comparisons,
    )


def comparison_summary_to_dict(summary: ComparisonSummary) -> Dict[str, Any]:
    return {
        "baseline_path": summary.baseline_path,
        "fail_regression_percent": summary.fail_regression_percent,
        "failed_regression_threshold": summary.failed_regression_threshold,
        "comparisons": [asdict(item) for item in summary.comparisons],
    }


def print_comparison(summary: ComparisonSummary):
    print("\nBaseline comparison")
    print(f"  Baseline: {summary.baseline_path}")
    print("  Metric                           Baseline      Current       Change      Change %")
    print("  -------------------------------------------------------------------------------")
    for item in summary.comparisons:
        pct = "n/a" if item.percent_change is None else f"{item.percent_change:+.2f}%"
        marker = " REGRESSION" if item.is_regression else ""
        print(
            f"  {item.metric:<30} "
            f"{item.baseline:>10.2f} "
            f"{item.current:>10.2f} "
            f"{item.absolute_change:>+10.2f} "
            f"{pct:>10}{marker}"
        )
    if summary.fail_regression_percent is not None:
        status = "failed" if summary.failed_regression_threshold else "passed"
        print(f"  Regression threshold {summary.fail_regression_percent:.2f}%: {status}")

# ---------------------------------------------------------------------------
# BENCHMARK FUNCTIONS
# ---------------------------------------------------------------------------

def run_latency_benchmark(url: str, concurrency: int, request_count: int,
                          timeout: float) -> BenchmarkResult:
    print(f"Running latency benchmark: {request_count} requests, {concurrency} concurrent")
    results: List[LatencySample] = []
    stop_flag = threading.Event()
    workers = []

    requests_per_worker = max(1, request_count // concurrency)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for _ in range(concurrency):
            futures.append(executor.submit(
                run_worker, url, requests_per_worker, results, stop_flag, timeout
            ))
        for f in as_completed(futures):
            f.result()

    return aggregate_results(results, "latency", url, concurrency)

def run_throughput_benchmark(url: str, concurrency: int, duration: float,
                             target_rps: float, timeout: float) -> BenchmarkResult:
    print(f"Running throughput benchmark: {duration}s, {concurrency} concurrent, target {target_rps} RPS")
    results: List[LatencySample] = []
    stop_flag = threading.Event()
    threads = []

    rps_per_worker = target_rps / concurrency if target_rps < float('inf') else float('inf')

    for _ in range(concurrency):
        t = threading.Thread(target=run_worker_duration,
                             args=(url, duration, results, stop_flag, timeout, rps_per_worker))
        threads.append(t)
        t.start()

    time.sleep(duration)
    stop_flag.set()

    for t in threads:
        t.join()

    return aggregate_results(results, "throughput", url, concurrency)

def run_stress_benchmark(url: str, concurrency: int, max_rps: float,
                         step_rps: float, step_duration: float,
                         error_threshold: float, timeout: float) -> BenchmarkResult:
    print(f"Running stress benchmark: max {max_rps} RPS, step {step_rps}, {concurrency} concurrent")
    all_results: List[LatencySample] = []
    current_rps = step_rps

    while current_rps <= max_rps:
        print(f"  Testing {current_rps} RPS...", end=" ", flush=True)
        results: List[LatencySample] = []
        stop_flag = threading.Event()
        threads = []
        rps_per_worker = current_rps / concurrency

        for _ in range(concurrency):
            t = threading.Thread(target=run_worker_duration,
                                 args=(url, step_duration, results, stop_flag, timeout, rps_per_worker))
            threads.append(t)
            t.start()

        time.sleep(step_duration)
        stop_flag.set()

        for t in threads:
            t.join()

        successful = sum(1 for r in results if r.success)
        total = len(results)
        error_rate = (total - successful) / max(total, 1) * 100

        print(f"  {total} req, {error_rate:.1f}% errors")

        all_results.extend(results)

        if error_rate > error_threshold:
            print(f"  Error threshold reached at {current_rps} RPS")
            break

        current_rps += step_rps

    return aggregate_results(all_results, "stress", url, concurrency)

def run_soak_benchmark(url: str, concurrency: int, duration: float,
                       target_rps: float, timeout: float) -> BenchmarkResult:
    print(f"Running soak benchmark: {duration}s, {concurrency} concurrent, {target_rps} RPS")
    results: List[LatencySample] = []
    stop_flag = threading.Event()
    threads = []
    rps_per_worker = target_rps / concurrency if target_rps < float('inf') else float('inf')

    print(f"  This will take {duration} seconds. Progress reports every 60 seconds.")
    progress_thread = threading.Thread(target=lambda: (
        [time.sleep(60) or print(f"  ... {int(time.time() - start)}s elapsed, {len(results)} requests")
         for _ in range(int(duration / 60))],
        None
    ), daemon=True)

    start = time.time()
    progress_thread.start()

    for _ in range(concurrency):
        t = threading.Thread(target=run_worker_duration,
                             args=(url, duration, results, stop_flag, timeout, rps_per_worker))
        threads.append(t)
        t.start()

    time.sleep(duration)
    stop_flag.set()

    for t in threads:
        t.join()

    return aggregate_results(results, "soak", url, concurrency)

def run_spike_benchmark(url: str, concurrency: int, duration: float,
                        spike_start: float, spike_duration: float,
                        normal_rps: float, spike_rps: float,
                        timeout: float) -> BenchmarkResult:
    print(f"Running spike benchmark: {duration}s, spike at {spike_start}s for {spike_duration}s")
    results: List[LatencySample] = []
    stop_flag = threading.Event()
    threads = []
    rps_per_worker_normal = normal_rps / concurrency
    rps_per_worker_spike = spike_rps / concurrency

    for _ in range(concurrency):
        t = threading.Thread(target=run_worker_spike,
                             args=(url, spike_start, spike_duration,
                                   rps_per_worker_normal, rps_per_worker_spike,
                                   results, stop_flag, timeout))
        threads.append(t)
        t.start()

    time.sleep(duration)
    stop_flag.set()

    for t in threads:
        t.join()

    return aggregate_results(results, "spike", url, concurrency)


def print_results(result: BenchmarkResult):
    print(f"\n{'='*60}")
    print(f"  Benchmark: {result.benchmark_type.upper()}")
    print(f"  Target: {result.target_endpoint}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Concurrency: {result.concurrency}")
    print(f"{'='*60}")
    print(f"  Total Requests:     {result.total_requests}")
    print(f"  Successful:         {result.successful_requests}")
    print(f"  Failed:             {result.failed_requests}")
    print(f"  Timeouts:           {result.timeout_requests}")
    print(f"  Requests/sec:       {result.requests_per_second:.2f}")
    print(f"{'─'*60}")
    print(f"  Latency (ms):")
    print(f"    Min:    {result.latency_ms['min']:.2f}")
    print(f"    Avg:    {result.latency_ms['avg']:.2f}")
    print(f"    P50:    {result.latency_ms['p50']:.2f}")
    print(f"    P90:    {result.latency_ms['p90']:.2f}")
    print(f"    P95:    {result.latency_ms['p95']:.2f}")
    print(f"    P99:    {result.latency_ms['p99']:.2f}")
    print(f"    Max:    {result.latency_ms['max']:.2f}")
    print(f"    StdDev: {result.latency_ms['stddev']:.2f}")
    if result.error_distribution:
        print(f"{'─'*60}")
        print(f"  Error Distribution:")
        for err, count in sorted(result.error_distribution.items(), key=lambda x: -x[1]):
            print(f"    {err}: {count}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="API Benchmark Tool")
    parser.add_argument("--endpoint", "-e", default="http://localhost:8080/health",
                       help="API endpoint URL")
    parser.add_argument("--concurrency", "-c", type=int, default=10,
                       help="Number of concurrent workers")
    parser.add_argument("--timeout", "-t", type=float, default=30.0,
                       help="Request timeout in seconds")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    parser.add_argument("--baseline", help="Compare current results with a previous benchmark JSON file")
    parser.add_argument("--write-baseline", help="Save current results as a deterministic baseline JSON file")
    parser.add_argument("--fail-regression", type=float,
                       help="Exit non-zero when any regression is greater than this percentage")

    subparsers = parser.add_subparsers(dest="mode", help="Benchmark mode")

    # Latency
    lat_p = subparsers.add_parser("latency", help="Measure request latency")
    lat_p.add_argument("--requests", type=int, default=1000, help="Number of requests")

    # Throughput
    thr_p = subparsers.add_parser("throughput", help="Measure throughput")
    thr_p.add_argument("--duration", type=float, default=30, help="Test duration in seconds")
    thr_p.add_argument("--target-rps", type=float, default=100, help="Target requests per second")

    # Stress
    str_p = subparsers.add_parser("stress", help="Stress test with ramp-up")
    str_p.add_argument("--max-rps", type=float, default=1000, help="Maximum RPS")
    str_p.add_argument("--step-rps", type=float, default=50, help="RPS increment per step")
    str_p.add_argument("--step-duration", type=float, default=10, help="Duration per step in seconds")
    str_p.add_argument("--error-threshold", type=float, default=10, help="Max error rate percentage")

    # Soak
    soak_p = subparsers.add_parser("soak", help="Soak test for memory leaks")
    soak_p.add_argument("--duration", type=float, default=3600, help="Test duration in seconds")
    soak_p.add_argument("--target-rps", type=float, default=50, help="Target requests per second")

    # Spike
    spike_p = subparsers.add_parser("spike", help="Spike test for auto-scaling")
    spike_p.add_argument("--duration", type=float, default=120, help="Total test duration")
    spike_p.add_argument("--spike-start", type=float, default=30, help="Spike start time")
    spike_p.add_argument("--spike-duration", type=float, default=10, help="Spike duration")
    spike_p.add_argument("--normal-rps", type=float, default=10, help="Normal RPS")
    spike_p.add_argument("--spike-rps", type=float, default=500, help="Spike RPS")

    args = parser.parse_args()
    if not args.mode:
        parser.print_help()
        return 1

    signal.signal(signal.SIGINT, lambda s, f: sys.exit(1))

    result = None
    if args.mode == "latency":
        result = run_latency_benchmark(args.endpoint, args.concurrency, args.requests, args.timeout)
    elif args.mode == "throughput":
        result = run_throughput_benchmark(args.endpoint, args.concurrency, args.duration, args.target_rps, args.timeout)
    elif args.mode == "stress":
        result = run_stress_benchmark(args.endpoint, args.concurrency, args.max_rps, args.step_rps, args.step_duration, args.error_threshold, args.timeout)
    elif args.mode == "soak":
        result = run_soak_benchmark(args.endpoint, args.concurrency, args.duration, args.target_rps, args.timeout)
    elif args.mode == "spike":
        result = run_spike_benchmark(args.endpoint, args.concurrency, args.duration, args.spike_start, args.spike_duration, args.normal_rps, args.spike_rps, args.timeout)

    if result:
        print_results(result)
        comparison = None
        if args.baseline:
            try:
                baseline = load_baseline(args.baseline)
                comparison = compare_results(
                    result,
                    baseline,
                    args.baseline,
                    args.fail_regression,
                )
                print_comparison(comparison)
            except Exception as e:
                print(f"Failed to compare baseline: {e}", file=sys.stderr)
                return 2
        if args.write_baseline:
            write_baseline(args.write_baseline, result)
            print(f"Baseline saved to {args.write_baseline}")
        if args.output:
            output = benchmark_result_to_dict(result)
            if comparison:
                output["comparison"] = comparison_summary_to_dict(comparison)
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, sort_keys=True)
                f.write("\n")
            print(f"Results saved to {args.output}")
        if comparison and comparison.failed_regression_threshold:
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
