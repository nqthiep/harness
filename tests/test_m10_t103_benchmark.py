"""M10/T-10.3 (docs/17-research-alignment.md): p50/p95 latency, throughput với
concurrency, cold start — đóng Y-05 ("performance chưa từng được đo, con số duy nhất
từng đo là import time").
"""
import asyncio
import os
import unittest

from harness.eval.benchmark import BenchmarkReport, benchmark, import_cold_start_ms


class Benchmark(unittest.TestCase):
    def test_tra_ve_dung_so_lan_va_percentile_co_thu_tu(self):
        async def fast():
            await asyncio.sleep(0)

        report = asyncio.run(benchmark(fast, n=10, concurrency=2))
        self.assertIsInstance(report, BenchmarkReport)
        self.assertEqual(report.n, 10)
        self.assertEqual(len(report.latencies_ms), 10)
        self.assertLessEqual(report.p50_ms, report.p95_ms)
        self.assertGreater(report.throughput_per_s, 0)

    def test_cold_start_tach_rieng_khoi_p50(self):
        calls = {"n": 0}

        async def slow_first():
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(0.05)      # lần đầu chậm hẳn — mô phỏng cold cache

        report = asyncio.run(benchmark(slow_first, n=6, concurrency=1))
        self.assertIsNotNone(report.cold_start_ms)
        self.assertGreater(report.cold_start_ms, report.warm_p50_ms)

    def test_n_1_khong_co_cold_start(self):
        async def one():
            pass

        report = asyncio.run(benchmark(one, n=1, concurrency=1))
        self.assertIsNone(report.cold_start_ms)
        self.assertIsNone(report.warm_p50_ms)

    def test_n_duoi_1_raise(self):
        async def noop():
            pass

        with self.assertRaises(ValueError):
            asyncio.run(benchmark(noop, n=0))

    def test_concurrency_bi_gioi_han_that(self):
        """Xác nhận `concurrency=` là một TRẦN THẬT, không phải trang trí — đo số lời
        gọi đang chạy CÙNG LÚC bằng một biến đếm, phải không bao giờ vượt trần."""
        peak = {"n": 0, "cur": 0}

        async def track():
            peak["cur"] += 1
            peak["n"] = max(peak["n"], peak["cur"])
            await asyncio.sleep(0.01)
            peak["cur"] -= 1

        asyncio.run(benchmark(track, n=20, concurrency=3))
        self.assertLessEqual(peak["n"], 3)

    def test_mutation_khong_gioi_han_concurrency_vuot_tran(self):
        """Mutation: mô phỏng chạy `asyncio.gather` không qua `Semaphore` — xác nhận
        không giới hạn thì đỉnh concurrency thật sự VƯỢT trần đã định."""
        peak = {"n": 0, "cur": 0}

        async def track():
            peak["cur"] += 1
            peak["n"] = max(peak["n"], peak["cur"])
            await asyncio.sleep(0.01)
            peak["cur"] -= 1

        async def unbounded():
            await asyncio.gather(*(track() for _ in range(20)))

        asyncio.run(unbounded())
        self.assertGreater(peak["n"], 3,
                           "mutation (bỏ Semaphore) phải cho đỉnh concurrency VƯỢT 3 — "
                           "nếu nó cũng không vượt, test này không còn phân biệt được "
                           "bản đúng và bản có lỗi")


class ColdStartImportThat(unittest.TestCase):
    def test_do_duoc_mot_so_duong(self):
        env = {**os.environ, "PYTHONPATH": "src"}
        ms = import_cold_start_ms(env=env)
        self.assertGreater(ms, 0)
        self.assertLess(ms, 5000)          # sanity — import không nên mất nhiều giây

    def test_that_bai_bao_loi_ro_rang(self):
        env = {**os.environ}
        env.pop("PYTHONPATH", None)        # KHÔNG trỏ tới src -> import harness lỗi
        with self.assertRaises(RuntimeError):
            import_cold_start_ms(env=env)


if __name__ == "__main__":
    unittest.main()
