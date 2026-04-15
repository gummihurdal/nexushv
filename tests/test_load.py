"""
NexusHV Load Testing Script
Run: python tests/test_load.py [num_requests] [concurrency]
Default: 200 requests, 10 concurrent
"""
import asyncio
import httpx
import time
import sys
import json
import statistics

BASE_URL = "http://localhost:8080"

async def make_request(client, method, path, body=None):
    """Make a single request and return timing info."""
    start = time.time()
    try:
        if method == "GET":
            r = await client.get(f"{BASE_URL}{path}")
        elif method == "POST":
            r = await client.post(f"{BASE_URL}{path}", json=body)
        elapsed = time.time() - start
        return {"path": path, "status": r.status_code, "time": elapsed, "error": None}
    except Exception as e:
        elapsed = time.time() - start
        return {"path": path, "status": 0, "time": elapsed, "error": str(e)}

async def load_test(total_requests=200, concurrency=10):
    """Run load test against API endpoints."""
    endpoints = [
        ("GET", "/health", None),
        ("GET", "/api/vms", None),
        ("GET", "/api/vms/prod-db-primary", None),
        ("GET", "/api/hosts/local", None),
        ("GET", "/api/storage", None),
        ("GET", "/api/networks", None),
        ("GET", "/api/metrics/system", None),
        ("GET", "/api/dashboard/overview", None),
        ("GET", "/api/alerts", None),
        ("GET", "/api/recommendations/rightsizing", None),
        ("POST", "/api/auth/login", {"username": "admin", "password": "admin"}),
    ]

    print(f"\n{'='*60}")
    print(f"  NexusHV Load Test")
    print(f"  Target: {BASE_URL}")
    print(f"  Requests: {total_requests} | Concurrency: {concurrency}")
    print(f"  Endpoints: {len(endpoints)}")
    print(f"{'='*60}\n")

    results = []
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_request(client, method, path, body):
        async with semaphore:
            return await make_request(client, method, path, body)

    start_time = time.time()

    async with httpx.AsyncClient(timeout=30) as client:
        tasks = []
        for i in range(total_requests):
            method, path, body = endpoints[i % len(endpoints)]
            tasks.append(bounded_request(client, method, path, body))
        results = await asyncio.gather(*tasks)

    total_time = time.time() - start_time

    # Analyze results
    times = [r["time"] for r in results if r["error"] is None]
    errors = [r for r in results if r["error"] is not None]
    status_counts = {}
    for r in results:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # Per-endpoint stats
    endpoint_times = {}
    for r in results:
        if r["error"] is None:
            p = r["path"]
            if p not in endpoint_times:
                endpoint_times[p] = []
            endpoint_times[p].append(r["time"])

    print(f"Results:")
    print(f"  Total time:     {total_time:.2f}s")
    print(f"  Requests/sec:   {len(results) / total_time:.1f}")
    print(f"  Success:        {len(times)}/{len(results)}")
    print(f"  Errors:         {len(errors)}")
    print(f"  Status codes:   {json.dumps(status_counts)}")
    print()

    if times:
        print(f"  Latency:")
        print(f"    Min:    {min(times)*1000:.1f}ms")
        print(f"    Avg:    {statistics.mean(times)*1000:.1f}ms")
        print(f"    Median: {statistics.median(times)*1000:.1f}ms")
        print(f"    p95:    {sorted(times)[int(len(times)*0.95)]*1000:.1f}ms")
        print(f"    p99:    {sorted(times)[int(len(times)*0.99)]*1000:.1f}ms")
        print(f"    Max:    {max(times)*1000:.1f}ms")
        print()

    print(f"  Per-endpoint latency (avg):")
    for path, t in sorted(endpoint_times.items(), key=lambda x: statistics.mean(x[1]), reverse=True):
        avg = statistics.mean(t) * 1000
        cnt = len(t)
        print(f"    {path:45s} {avg:7.1f}ms  ({cnt} reqs)")

    print(f"\n{'='*60}")

    # Return pass/fail
    error_rate = len(errors) / len(results)
    avg_latency = statistics.mean(times) if times else 999

    if error_rate > 0.05:
        print(f"  FAIL: Error rate {error_rate*100:.1f}% exceeds 5% threshold")
        return False
    if avg_latency > 2.0:
        print(f"  FAIL: Avg latency {avg_latency*1000:.0f}ms exceeds 2000ms threshold")
        return False
    print(f"  PASS: Error rate {error_rate*100:.1f}%, Avg latency {avg_latency*1000:.0f}ms")
    return True

if __name__ == "__main__":
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    conc = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    ok = asyncio.run(load_test(total, conc))
    sys.exit(0 if ok else 1)
