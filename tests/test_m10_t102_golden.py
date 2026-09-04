"""M10/T-10.2 (docs/17-research-alignment.md): golden set + pass rate với khoảng tin
cậy, tổng cost/token — không bao giờ một con số trần trụi (S-06, cùng luật T-8.4).
"""
import asyncio
import unittest

from harness import Agent, tool
from harness.eval.golden import GoldenCase, run_golden_set
from harness.eval.trajectory import Trajectory
from harness.models.fake import FakeModel


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


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
