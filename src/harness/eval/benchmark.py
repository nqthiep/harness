"""T-10.3 — benchmark hiệu năng, docs/17-research-alignment.md M10. Đóng Y-05.

p50/p95 latency, throughput với concurrency, cold start — ba con số nghiên cứu đòi mà
`docs/17 §1` tự chấm "chưa đo latency/throughput/concurrency lần nào" (Performance 2/5).
Đây là HẠ TẦNG đo — kết quả phụ thuộc model/tool/máy chạy thật, nên không có "con số đạt"
cứng nào ở đây, đúng luật §45 "chưa đủ evidence" áp cho benchmark: đo được là xong việc
của module này, phán xét con số đo được là việc của người vận hành.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from ..agent import Agent


@dataclass(frozen=True)
class LatencyReport:
    n: int
    cold_start_ms: float
    #: `()` khi `n == 1` — không có mẫu "warm" nào để tính percentile.
    warm_ms: tuple[float, ...] = ()

    @staticmethod
    def _percentile(sorted_ms: Sequence[float], p: float) -> float:
        if not sorted_ms:
            return math.nan
        idx = min(len(sorted_ms) - 1, max(0, round(p * (len(sorted_ms) - 1))))
        return sorted_ms[idx]

    @property
    def p50_ms(self) -> float:
        return self._percentile(sorted(self.warm_ms), 0.50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(sorted(self.warm_ms), 0.95)

    def __str__(self) -> str:
        warm = (f"p50={self.p50_ms:.1f}ms p95={self.p95_ms:.1f}ms (n={len(self.warm_ms)} warm)"
               if self.warm_ms else "(không có mẫu warm)")
        return f"cold_start={self.cold_start_ms:.1f}ms, {warm}"


async def run_latency_benchmark(
    agent_factory: Callable[[], "Agent"], message: str, *, n: int
) -> LatencyReport:
    """Tách CỐ Ý cold start (lần đầu) khỏi warm (n-1 lần sau) — chúng đo hai thứ khác
    nhau (dựng `Agent`/`ContextAssembler`/PrefixWatcher lần đầu vs. gọi lặp lại một agent
    đã dựng), gộp chung sẽ làm méo cả hai con số."""
    if n < 1:
        raise ValueError("run_latency_benchmark cần n >= 1")

    t0 = time.perf_counter()
    agent = agent_factory()
    await agent.atry_run(message)
    cold_ms = (time.perf_counter() - t0) * 1000

    warm: list[float] = []
    for _ in range(n - 1):
        t0 = time.perf_counter()
        await agent.atry_run(message)
        warm.append((time.perf_counter() - t0) * 1000)

    return LatencyReport(n, cold_ms, tuple(warm))


@dataclass(frozen=True)
class ThroughputReport:
    n_runs: int
    concurrency: int
    wall_clock_s: float

    @property
    def runs_per_second(self) -> float:
        return self.n_runs / self.wall_clock_s if self.wall_clock_s > 0 else math.inf

    def __str__(self) -> str:
        return (f"{self.n_runs} run qua {self.wall_clock_s:.3f}s tại concurrency="
               f"{self.concurrency} -> {self.runs_per_second:.2f} run/s")


async def run_throughput_benchmark(
    agent_factory: Callable[[], "Agent"], message: str, *, n: int, concurrency: int
) -> ThroughputReport:
    """`concurrency` THẬT — `asyncio.Semaphore`, không phải `n` task tự do đua nhau; đo
    throughput dưới một giới hạn đồng thời cụ thể, đúng câu hỏi "throughput với
    concurrency" T-10.3 đặt ra, không phải "throughput không giới hạn gì".
    """
    if n < 1 or concurrency < 1:
        raise ValueError("run_throughput_benchmark cần n >= 1 và concurrency >= 1")

    sem = asyncio.Semaphore(concurrency)

    async def _one() -> None:
        async with sem:
            await agent_factory().atry_run(message)

    t0 = time.perf_counter()
    await asyncio.gather(*(_one() for _ in range(n)))
    wall = time.perf_counter() - t0

    return ThroughputReport(n, concurrency, wall)
