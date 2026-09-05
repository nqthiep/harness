"""M10/T-10.3 (docs/17-research-alignment.md): p50/p95 latency, throughput với
concurrency, cold start — đóng Y-05 ("performance chưa từng được đo, con số duy nhất
từng đo là import time").
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, "src")

from harness.errors import ConfigError
from harness.eval.benchmark import BenchmarkReport, benchmark, import_cold_start_ms
from harness.result import Money


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


class NganSachTongTheH3(unittest.TestCase):
    """H-3, design/review-architect-round3.md: G-14 gave `run_golden_set` an aggregate
    ceiling but not `benchmark()`, named in the same original finding. `total_budget=`
    needs `cost_of=` too, since `run_fn` is opaque -- nothing else can turn its return
    value into a price."""

    def test_khong_truyen_total_budget_hanh_vi_khong_doi(self):
        async def call():
            return Money("0.10")

        report = asyncio.run(benchmark(call, n=5, concurrency=1))
        self.assertEqual(report.n, 5)
        self.assertFalse(report.budget_exhausted)

    def test_total_budget_khong_co_cost_of_thi_raise(self):
        async def call():
            return Money("0.10")

        with self.assertRaises(ConfigError):
            asyncio.run(benchmark(call, n=5, total_budget="$1"))

    def test_total_budget_can_dung_giua_bo_dung_som(self):
        async def call():
            return Money("0.10")

        # Mỗi lần gọi tốn $0.10, concurrency=1 nên tuần tự. Cái được kiểm TRƯỚC mỗi lần
        # gọi là "còn > 0", không phải "đủ cho lần tới" (giống hệt run_golden_set) --
        # nên $0.35 vẫn cho phép lần gọi thứ 4 bắt đầu (remaining=$0.05 > 0 lúc đó), chỉ
        # dừng ở lần thứ 5.
        report = asyncio.run(benchmark(call, n=10, concurrency=1,
                                       total_budget="$0.35", cost_of=lambda r: r))
        self.assertEqual(report.n, 4,
                         "phải dừng SỚM sau đúng 4 lần gọi -- không chạy hết cả 10")
        self.assertTrue(report.budget_exhausted)
        self.assertEqual(len(report.latencies_ms), 4)

    def test_total_budget_qua_nho_cho_ca_lan_dau_van_tra_ve_bao_cao_rong(self):
        """Không giống `run_golden_set` (raise khi 0 case chạy), `benchmark()` là công
        cụ đo lường, không phải cổng đúng/sai -- trả về báo cáo suy biến (n=0), không
        raise, không chia 0 cho 0."""
        async def call():
            return Money("0.10")

        report = asyncio.run(benchmark(call, n=5, total_budget="$0",
                                       cost_of=lambda r: r))
        self.assertEqual(report.n, 0)
        self.assertTrue(report.budget_exhausted)
        self.assertEqual(report.p50_ms, 0.0)

    def test_total_budget_khong_gioi_han_van_chay_het(self):
        from harness.budget.ledger import Budget

        async def call():
            return Money("0.10")

        report = asyncio.run(benchmark(call, n=5, total_budget=Budget(usd=None),
                                       cost_of=lambda r: r))
        self.assertEqual(report.n, 5)
        self.assertFalse(report.budget_exhausted)


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
