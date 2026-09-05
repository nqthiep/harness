"""M10/T-10.2 (docs/17-research-alignment.md): golden set + pass rate với khoảng tin
cậy, tổng cost/token — không bao giờ một con số trần trụi (S-06, cùng luật T-8.4).
"""
import asyncio
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.eval.golden import GoldenCase, run_golden_set
from harness.eval.trajectory import Trajectory
from harness.errors import BudgetExceeded
from harness.models import pricing
from harness.models.fake import FakeModel


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


class PricedFakeModel:
    """`FakeModel`, nhưng định giá như model thật. `total_budget=`'s tests (G-14) cần
    cost khác 0 mỗi case để quan sát được ledger tổng thực sự cạn — `FakeModel.price()`
    luôn trả `pricing.price("fake")`, mọi mức giá đều `Decimal(0)`."""
    def __init__(self, script):
        self._inner = FakeModel(script)
    def price(self, m): return pricing.price("claude-haiku-4-5")
    def max_output(self, m): return self._inner.max_output(m)
    async def count_input_tokens(self, r): return await self._inner.count_input_tokens(r)
    async def complete(self, request, *, on_delta=None):
        return await self._inner.complete(request, on_delta=on_delta)


class ChayMotBoGolden(unittest.TestCase):
    def test_tat_ca_pass(self):
        agent = Agent(name="A", job="j", tools=[look], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("d")]))
        cases = [GoldenCase("c1", "hi", contract=Trajectory(must_call=frozenset({"look"})))]
        report = asyncio.run(run_golden_set(agent, cases))
        self.assertEqual(report.n, 1)
        self.assertEqual(report.n_passed, 1)
        self.assertEqual(report.pass_rate, 1.0)

    def test_mot_case_fail_khong_lam_hong_case_khac(self):
        agent = Agent(name="A", job="j", tools=[look], budget="$5",
                      provider=FakeModel([
                          FakeModel.tool_call("look", {"x": 1}), FakeModel.text("d1"),
                          FakeModel.text("d2"),
                      ]))
        cases = [
            GoldenCase("ok", "a", contract=Trajectory(must_call=frozenset({"look"}))),
            GoldenCase("bad", "b", contract=Trajectory(must_call=frozenset({"look"}))),
        ]
        report = asyncio.run(run_golden_set(agent, cases))
        self.assertEqual(report.n, 2)
        self.assertEqual(report.n_passed, 1)
        self.assertEqual(report.results[0].case, "ok")
        self.assertTrue(report.results[0].passed)
        self.assertEqual(report.results[1].case, "bad")
        self.assertFalse(report.results[1].passed)

    def test_case_khong_co_contract_chi_can_result_ok(self):
        agent = Agent(name="A", job="j", budget="$5",
                      provider=FakeModel([FakeModel.text("d")]))
        report = asyncio.run(run_golden_set(agent, [GoldenCase("c1", "hi")]))
        self.assertTrue(report.results[0].passed)

    def test_mang_khoang_tin_cay_va_cost_token(self):
        agent = Agent(name="A", job="j", budget="$5",
                      provider=FakeModel([FakeModel.text("d")]))
        report = asyncio.run(run_golden_set(agent, [GoldenCase("c1", "hi")]))
        self.assertGreaterEqual(report.ci_high, report.pass_rate)
        self.assertLessEqual(report.ci_low, report.pass_rate)
        self.assertGreaterEqual(report.total_tokens, 0)
        self.assertGreaterEqual(report.total_cost_usd, 0.0)

    def test_khong_case_nao_thi_raise(self):
        agent = Agent(name="A", job="j", budget="$5", provider=FakeModel([]))
        with self.assertRaises(ValueError):
            asyncio.run(run_golden_set(agent, []))

    def test_agent_khong_bi_thay_doi(self):
        """`run_golden_set` phải KHÔNG mutate `Agent` truyền vào — dùng `with_()`,
        không gán trực tiếp `exporters` (Agent bất biến, ADR-004)."""
        agent = Agent(name="A", job="j", budget="$5", provider=FakeModel([FakeModel.text("d")]))
        before = agent.exporters
        asyncio.run(run_golden_set(agent, [GoldenCase("c1", "hi")]))
        self.assertEqual(agent.exporters, before)


class NganSachTongTheG14(unittest.TestCase):
    """G-14, design/review-architect.md: `total_budget=` là một trần TỔNG cho cả bộ
    case, độc lập với `agent.budget` của từng lần chạy riêng lẻ — trước bản vá này
    không hề có trần nào như vậy."""

    def test_khong_truyen_total_budget_hanh_vi_khong_doi(self):
        agent = Agent(name="A", job="j", model="claude-haiku-4-5", budget="$5",
                      provider=PricedFakeModel([FakeModel.text("d")] * 3))
        cases = [GoldenCase(f"c{i}", "hi") for i in range(3)]
        report = asyncio.run(run_golden_set(agent, cases))
        self.assertEqual(report.n, 3)
        self.assertFalse(report.budget_exhausted)

    def test_total_budget_can_dung_giua_bo_dung_som_va_bao_dung(self):
        agent = Agent(name="A", job="j", model="claude-haiku-4-5", budget="$5",
                      provider=PricedFakeModel([FakeModel.text("d")] * 3))
        cases = [GoldenCase(f"c{i}", "hi") for i in range(3)]
        # Mỗi case tốn $0.0002 (đo trực tiếp qua PricedFakeModel) — $0.00035 vừa đủ cho
        # ĐÚNG 2 case, không đủ cho case thứ 3.
        report = asyncio.run(run_golden_set(agent, cases, total_budget="$0.00035"))
        self.assertEqual(report.n, 2,
                         "phải dừng SỚM ở case thứ 2 — không chạy hết cả 3")
        self.assertTrue(report.budget_exhausted)
        self.assertIn("STOPPED EARLY", str(report))

    def test_khong_du_ngan_sach_cho_ca_case_dau_tien_raise(self):
        """total_budget="$0" nghĩa là không còn gì để chạy NGAY CẢ case đầu tiên —
        không có kết quả nào để báo cáo, nên raise thay vì chia 0/0 hay báo cáo giả."""
        agent = Agent(name="A", job="j", model="claude-haiku-4-5", budget="$5",
                      provider=PricedFakeModel([FakeModel.text("d")]))
        with self.assertRaises(BudgetExceeded):
            asyncio.run(run_golden_set(agent, [GoldenCase("c1", "hi")],
                                       total_budget="$0"))

    def test_total_budget_khong_gioi_han_van_chay_het(self):
        """`Budget(usd=None)` truyền tường minh qua `total_budget=` vẫn là escape hatch
        hợp lệ (cùng khuôn S-20) — không trần nào được áp, không case nào bị bỏ."""
        from harness.budget.ledger import Budget
        agent = Agent(name="A", job="j", model="claude-haiku-4-5", budget="$5",
                      provider=PricedFakeModel([FakeModel.text("d")] * 3))
        cases = [GoldenCase(f"c{i}", "hi") for i in range(3)]
        report = asyncio.run(run_golden_set(agent, cases, total_budget=Budget(usd=None)))
        self.assertEqual(report.n, 3)
        self.assertFalse(report.budget_exhausted)


class MutationXacNhanLoadBearing(unittest.TestCase):
    def test_mutation_khong_kiem_trajectory_chi_kiem_result_ok(self):
        """Mutation: `passed` chỉ đọc `result.ok`, bỏ qua `trajectory.ok` — một case
        HOÀN THÀNH nhưng VI PHẠM contract (gọi tool cấm) sẽ bị tính PASS nhầm."""
        agent = Agent(name="A", job="j", tools=[look], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("d")]))
        cases = [GoldenCase("c1", "hi", contract=Trajectory(must_not_call=frozenset({"look"})))]
        report = asyncio.run(run_golden_set(agent, cases))
        self.assertTrue(report.results[0].result.ok,
                        "run phải HOÀN THÀNH — case này minh hoạ đúng khoảng lệch giữa "
                        "'chạy xong' và 'đúng contract'")
        self.assertFalse(report.results[0].passed,
                         "mutation (chỉ kiểm result.ok) sẽ tính PASS nhầm case này — "
                         "bản đúng phải FAIL vì vi phạm must_not_call")


if __name__ == "__main__":
    unittest.main()
