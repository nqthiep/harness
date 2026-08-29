"""§17 — các khoảng trống nghiên cứu chỉ ra, viết thành test.

Đây là các test **được phép đỏ**: chúng là định nghĩa "xong" của M6–M10, không phải hồi
quy. Chạy file này để biết kế hoạch đã đi tới đâu; nó in bảng trạng thái thay vì fail
build. Khi một mục chuyển sang xanh, chuyển test đó sang suite thường và xoá khỏi đây.

Vì sao viết trước khi xây: 41 vòng vừa qua cho thấy một khoảng trống chỉ được mô tả bằng
lời thì không ai biết nó đã đóng hay chưa.
"""
import asyncio
import sys
import unittest

sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from harness import Agent, tool
from harness.models.fake import FakeModel

KET_QUA: list[tuple[str, str, bool, str]] = []


def muc(ma: str, ten: str):
    def wrap(fn):
        def chay(self):
            try:
                fn(self)
                KET_QUA.append((ma, ten, True, ""))
            except AssertionError as e:
                KET_QUA.append((ma, ten, False, str(e).splitlines()[0][:60]))
        chay.__name__ = fn.__name__
        chay.__doc__ = fn.__doc__
        return chay
    return wrap


class KhoangTrong(unittest.TestCase):
    """Mỗi test khẳng định điều nghiên cứu đòi. Đỏ = chưa xây, không phải hỏng."""

    @muc("Y-01", "CancelledError propagate ra ngoài (T-6.2)")
    def test_cancellation_propagates(self):
        RAN: list = []

        @tool(effect="write")
        async def cham(x: int) -> str:
            """Chậm."""
            await asyncio.sleep(0.3); RAN.append(x); return "xong"

        async def main():
            a = Agent(name="T", job="j", model="fake", tools=[cham], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("cham", {"x": 1}),
                                          FakeModel.text("ok")]))
            t = asyncio.create_task(a.atry_run("go"))
            await asyncio.sleep(0.05)
            t.cancel()
            try:
                await t
                return False          # nuốt mất
            except asyncio.CancelledError:
                return True

        self.assertTrue(asyncio.run(main()),
                        "CancelledError bị nuốt: TaskGroup bao ngoài không thấy việc huỷ")

    @muc("Y-01b", "huỷ không để side effect chạy ngầm")
    def test_cancellation_leaks_no_side_effect(self):
        RAN: list = []

        @tool(effect="write")
        async def cham(x: int) -> str:
            """Chậm."""
            await asyncio.sleep(0.3); RAN.append(x); return "xong"

        async def main():
            a = Agent(name="T", job="j", model="fake", tools=[cham], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("cham", {"x": 1}),
                                          FakeModel.text("ok")]))
            t = asyncio.create_task(a.atry_run("go"))
            await asyncio.sleep(0.05); t.cancel()
            try: await t
            except asyncio.CancelledError: pass
            await asyncio.sleep(0.5)
            return RAN

        self.assertEqual(asyncio.run(main()), [], "tool vẫn chạy sau khi huỷ")

    @muc("S-01", "idempotency key trên tool call (T-6.1)")
    def test_tool_call_carries_an_idempotency_key(self):
        from harness.policy.base import ToolCall
        import dataclasses
        truong = {f.name for f in dataclasses.fields(ToolCall)}
        self.assertIn("idempotency_key", truong,
                      f"ToolCall chỉ có {sorted(truong)}")

    @muc("S-03", "principal / tenant / scopes trong ngữ cảnh (T-8.1)")
    def test_run_context_carries_a_principal(self):
        from harness.dispatch import RunContext
        co = {n for n in dir(RunContext) if not n.startswith("_")}
        self.assertTrue({"principal", "tenant_id"} <= co, f"RunContext có {sorted(co)}")

    @muc("Y-03", "event envelope có schema_version / trace_id / tenant_id (T-8.1)")
    def test_event_envelope_is_versioned_and_traceable(self):
        import dataclasses
        from harness.observe.events import Event
        truong = {f.name for f in dataclasses.fields(Event)}
        self.assertTrue({"schema_version", "trace_id"} <= truong,
                        f"Event có {sorted(truong)}")

    @muc("S-02", "approval là bản ghi, không phải boolean (T-8.2)")
    def test_approval_is_an_auditable_record(self):
        import harness
        self.assertTrue(hasattr(harness, "ApprovalRecord"),
                        "approve= vẫn trả về bool; không có decision_id/actor/expiry")

    @muc("Y-02", "egress mặc định CHẶN, không phải cho tất cả (T-7.2)")
    def test_egress_denies_by_default(self):
        a = Agent(name="T", job="j", model="fake", provider=FakeModel([]), budget="$1")
        self.assertEqual(a.allowed_hosts, (),
                         f"allowed_hosts mặc định = {a.allowed_hosts!r} → cho tất cả")

    @muc("S-04", "seam Sandbox tồn tại (T-7.3)")
    def test_a_sandbox_seam_exists(self):
        try:
            import harness.sandbox  # noqa: F401
        except ImportError:
            self.fail("không có harness.sandbox — lớp Isolation trống")

    @muc("S-09", "MCP làm tool boundary (T-9.1)")
    def test_mcp_tools_can_be_imported(self):
        try:
            import harness.mcp  # noqa: F401
        except ImportError:
            self.fail("không có harness.mcp")

    @muc("S-06", "cost per successful task (T-8.4)")
    def test_cost_per_successful_task_is_measurable(self):
        try:
            from harness.eval import cost_per_success  # noqa: F401
        except ImportError:
            self.fail("không có harness.eval.cost_per_success")

    @muc("S-05", "trajectory contract khai báo được (T-10.1)")
    def test_a_trajectory_contract_can_be_written(self):
        try:
            from harness.testing import Trajectory  # noqa: F401
        except ImportError:
            self.fail("không có harness.testing.Trajectory")


def main() -> int:
    unittest.main(module=__name__, exit=False, argv=[sys.argv[0]], verbosity=0)
    xong = sum(1 for *_, ok, _ in [(a, b, c, d) for a, b, c, d in KET_QUA] if ok)
    print(f"\n{'═' * 72}\n§17 — TIẾN ĐỘ KẾ HOẠCH\n{'═' * 72}")
    for ma, ten, ok, vi in sorted(KET_QUA):
        print(f"  {'✓' if ok else '·'} [{ma:<6}] {ten}")
        if not ok:
            print(f"           {vi}")
    print(f"\n  đã xong {xong}/{len(KET_QUA)} — phần còn lại là M6–M10 trong docs/17")
    return 0


if __name__ == "__main__":
    sys.exit(main())
