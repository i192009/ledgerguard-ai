"""Authenticated, bounded read load. Measures actual latency; no claims are hardcoded."""

import argparse
import asyncio
import json
import statistics
import time

import httpx


async def run(url, count, concurrency):
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        login = await client.post(
            "/auth/login", json={"email": "analyst@ledgerguard.demo", "password": "SandboxDemo!2026"}
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        semaphore = asyncio.Semaphore(concurrency)

        async def request():
            async with semaphore:
                start = time.perf_counter()
                response = await client.get("/transactions?page_size=50", headers=headers)
                response.raise_for_status()
                return time.perf_counter() - start

        start = time.perf_counter()
        times = sorted(await asyncio.gather(*(request() for _ in range(count))))
        print(
            json.dumps(
                {
                    "requests": count,
                    "concurrency": concurrency,
                    "elapsed_seconds": time.perf_counter() - start,
                    "p50_ms": statistics.median(times) * 1000,
                    "p95_ms": times[min(int(len(times) * 0.95), len(times) - 1)] * 1000,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8088/api/v1")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.requests, args.concurrency))
