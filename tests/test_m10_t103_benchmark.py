"""T-10.3 — benchmark hiệu năng, docs/17-research-alignment.md M10. Đóng Y-05."""
import asyncio
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.eval import LatencyReport, run_latency_benchmark, run_throughput_benchmark
from harness.models.fake import FakeModel


def _agent_factory():
    return Agent(name="T", job="j", model="fake", budget="$5",
                provider=FakeModel([FakeModel.text("ok")]))


class Percentiles(unittest.TestCase):
    def test_p50_p95_on_known_samples(self):
        report = LatencyReport(n=5, cold_start_ms=100.0, warm_ms=(10.0, 20.0, 30.0, 40.0))
        self.assertGreaterEqual(report.p95_ms, report.p50_ms)
        self.assertIn(report.p50_ms, report.warm_ms)

    def test_no_warm_samples_is_nan_not_a_crash(self):
        import math
        report = LatencyReport(n=1, cold_start_ms=50.0)
        self.assertTrue(math.isnan(report.p50_ms))
        str(report)                                     # không raise


class LatencyBenchmark(unittest.TestCase):
    def test_cold_start_separated_from_warm(self):
        async def go():
            return await run_latency_benchmark(_agent_factory, "hi", n=4)
        report = asyncio.run(go())
        self.assertEqual(report.n, 4)
        self.assertEqual(len(report.warm_ms), 3)
        self.assertGreaterEqual(report.cold_start_ms, 0.0)
        self.assertGreaterEqual(report.p50_ms, 0.0)

    def test_n_must_be_positive(self):
        async def go():
            with self.assertRaises(ValueError):
                await run_latency_benchmark(_agent_factory, "hi", n=0)
        asyncio.run(go())

    def test_single_sample_has_no_warm_percentiles(self):
        async def go():
            return await run_latency_benchmark(_agent_factory, "hi", n=1)
        report = asyncio.run(go())
        self.assertEqual(report.warm_ms, ())


class ThroughputBenchmark(unittest.TestCase):
    def test_runs_all_n_under_real_concurrency_limit(self):
        """Đo concurrency THẬT qua chính lời gọi tool — mỗi run gọi đúng một tool `mark`
        (ngủ một chút, đếm số lời gọi đang mở); nếu `run_throughput_benchmark`'s semaphore
        không thật, số lời gọi mở đồng thời sẽ vượt `concurrency`. `Agent` bị đóng băng
        (`@value`, slots) nên không monkeypatch được — đo bằng chính cơ chế tool có sẵn,
        không phá cấu trúc nội bộ.
        """
        active = 0
        peak = 0
        lock = asyncio.Lock()

        @tool(effect="read")
        async def mark() -> str:
            """Đánh dấu đang chạy, ngủ một chút, rồi bỏ đánh dấu."""
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1
            return "ok"

        def factory():
            return Agent(name="T", job="j", model="fake", tools=[mark], budget="$5",
                        provider=FakeModel([FakeModel.tool_call("mark", {}),
                                            FakeModel.text("ok")]))

        async def go():
            return await run_throughput_benchmark(factory, "hi", n=6, concurrency=2)

        report = asyncio.run(go())
        self.assertEqual(report.n_runs, 6)
        self.assertLessEqual(peak, 2, "concurrency=2 nhưng có lúc chạy hơn 2 run cùng lúc")
        self.assertGreater(report.runs_per_second, 0)

    def test_n_and_concurrency_must_be_positive(self):
        async def go():
            with self.assertRaises(ValueError):
                await run_throughput_benchmark(_agent_factory, "hi", n=0, concurrency=1)
            with self.assertRaises(ValueError):
                await run_throughput_benchmark(_agent_factory, "hi", n=1, concurrency=0)
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
