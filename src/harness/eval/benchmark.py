"""T-10.3 — performance benchmark, docs/17-research-alignment.md M10 / Y-05.

"Performance has never been measured. 6% weight, and the only number ever measured is
import time." Closing that means two different things measured two different ways: a
per-call LATENCY/THROUGHPUT distribution (fast, deterministic, driven against whatever
`run_fn` the caller supplies — a `FakeModel`-backed agent in a test, a real one in a
real benchmark run) and process-level IMPORT COLD START (needs a fresh subprocess — a
module already imported in this process cannot be re-measured for cold start).
"""
from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
from typing import Awaitable, Callable, Mapping

from .._value import value


@value
class BenchmarkReport:
    n: int
    concurrency: int
    p50_ms: float
    p95_ms: float
    #: `n / total wall-clock time to run all `n` calls at `concurrency`` — not
    #: `1000/p50_ms`, which is only throughput at concurrency 1 and quietly assumes
    #: perfect parallel scaling otherwise (Y-05's own complaint: no concurrency test).
    throughput_per_s: float
    #: The first call's latency, separated out — `None` when `n < 2` (nothing to
    #: contrast it against). A cold cache (context assembly, provider client init on
    #: first use) can make call 1 measurably slower than the steady state; folding it
    #: into `p50`/`p95` would hide exactly the number Y-05 asks for.
    cold_start_ms: float | None
    warm_p50_ms: float | None
    latencies_ms: tuple[float, ...] = ()


def _percentile(sorted_values: list[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


async def benchmark(run_fn: Callable[[], Awaitable[object]], *, n: int = 20,
                    concurrency: int = 1) -> BenchmarkReport:
    """`run_fn`: a zero-arg async callable that performs ONE unit of work (typically
    `lambda: agent.atry_run(message)`) — decoupled from `Agent` construction so a caller
    benchmarking a specific scenario (a particular tool set, a particular message) does
    not have to route it through this module's API.

    Bounded concurrency, same shape as `Dispatcher._bounded` (NFR-09) — a benchmark that
    fires `n` calls with no cap is not measuring the harness, it is measuring however
    many connections the test double or provider happens to tolerate at once.
    """
    if n < 1:
        raise ValueError("benchmark needs n >= 1")
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = [0.0] * n

    async def _one(i: int) -> None:
        async with sem:
            t0 = time.perf_counter()
            await run_fn()
            latencies[i] = (time.perf_counter() - t0) * 1000

    wall0 = time.perf_counter()
    await asyncio.gather(*(_one(i) for i in range(n)))
    wall_s = time.perf_counter() - wall0

    sorted_lat = sorted(latencies)
    cold_start_ms = latencies[0] if n >= 2 else None
    warm_p50_ms = statistics.median(latencies[1:]) if n >= 2 else None
    return BenchmarkReport(
        n=n, concurrency=concurrency,
        p50_ms=_percentile(sorted_lat, 0.50), p95_ms=_percentile(sorted_lat, 0.95),
        throughput_per_s=n / wall_s if wall_s > 0 else float("inf"),
        cold_start_ms=cold_start_ms, warm_p50_ms=warm_p50_ms,
        latencies_ms=tuple(latencies))


def import_cold_start_ms(*, python: str | None = None, timeout_s: float = 10.0,
                         env: "Mapping[str, str] | None" = None) -> float:
    """Y-05's own named gap: "the only number ever measured is import time" — but never
    as a callable, reproducible measurement, only an ad hoc `time python -c` a person
    ran once and wrote into a comment. This is that number, made real: a FRESH
    subprocess (the only honest way to measure cold import — this process has already
    paid the cost `import harness` incurs, and cannot pay it twice) times `import
    harness` alone, nothing else.

    `env`: pass e.g. `{**os.environ, "PYTHONPATH": "src"}` when running against a
    checkout rather than an installed package (this repo's own test suite does exactly
    that — nothing here is `pip install`ed). Defaults to inheriting this process's
    environment, which is all a real, installed deployment ever needs.
    """
    code = "import time; t=time.perf_counter(); import harness; print((time.perf_counter()-t)*1000)"
    r = subprocess.run([python or sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=timeout_s, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"import harness failed in subprocess: {r.stderr}")
    return float(r.stdout.strip())
