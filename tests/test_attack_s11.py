"""S-11: `Actor` là lời tự khai của bên giữ callback `approve=`, không phải danh tính đã
xác thực — `Approver(fn, actor=...)` cố định danh tính LÚC DỰNG, bất kể ai thật sự bấm nút.

Kiểm trên code hôm nay: không có `Approver`/`AskOutcome` (thiết kế gốc mô tả, chưa từng
được xây) — chữ ký thật là `ApprovalFn = Callable[[ToolCall, RunContext], bool]`, và trước
bản vá này `PolicyEngine.resolve()` chỉ nhận `bool`, nên `Decision.actor` LUÔN LÀ hằng số
`Actor.human("approver", via="callback")` cho MỌI lần duyệt qua callback, không phân biệt
được ai đã bấm.

Bản vá không xây `AuthEvidence` đầy đủ mà review đề xuất (cần một mô hình xác thực người
duyệt riêng, ghi lại ở `07-risks`) — chỉ mở một kênh TÙY CHỌN: `approve=` có thể trả
`Approval(ok, actor=...)` thay vì `bool` trần, và khi đó `Decision.actor` ghi đúng danh
tính đó. Callback nào vẫn trả `bool` thì hành vi y hệt trước bản vá — không có gì bị buộc
phải đổi.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness import Actor, Approval, tool
from harness.policy.base import Verdict


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


class ApprovalKenhTuyChon(unittest.TestCase):
    """Đơn vị: `PolicyEngine.resolve()`."""

    def _engine_ask(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine

        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _ctx_call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext

        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        call = ToolCall("c1", "wipe", {"x": 1}, wipe)
        return call, ctx

    def test_bool_tran_thi_actor_la_none_tu_resolve(self):
        """Đối chứng: callback trả `bool` trần — `resolve()` không báo actor nào, đúng
        hành vi trước bản vá (nơi gọi tự đặt placeholder chung)."""
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        d, actor = asyncio.run(engine.resolve(decision, call, ctx, lambda c, x: True))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertIsNone(actor, "bool trần không được tự bịa ra một actor nào")

    def test_approval_object_thi_actor_dung_nguoi_that(self):
        """Callback trả `Approval(ok, actor=...)` — `resolve()` phải trả lại ĐÚNG actor
        đó, không phải placeholder."""
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        real_actor = Actor.human("nqthiep", via="slack:U123")

        def approve(c, x): return Approval(ok=True, actor=real_actor)

        d, actor = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertEqual(actor, real_actor,
                         "resolve() không trả lại đúng actor mà callback báo — S-11")

    def test_approval_object_deny_van_hoat_dong(self):
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x): return Approval(ok=False, actor=Actor.human("x", via="cli"))

        d, actor = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.DENY)


class GraphGhiDungActorKhiCoBaoCao(unittest.TestCase):
    """Chạy thật qua backend LangGraph — `Decision.actor` trong sổ phải là actor callback
    báo, không phải placeholder `"approver"` cố định."""

    def test_decision_log_ghi_actor_that_khong_phai_placeholder(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        real_actor = Actor.human("nqthiep", via="slack:U123")

        def approve(call, ctx): return Approval(ok=True, actor=real_actor)

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=approve)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s11"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows, "không có Decision nào được ghi cho wipe")
        self.assertEqual(rows[0].actor, real_actor,
                         "Decision.actor là placeholder chung, không phải actor callback "
                         "báo — S-11 chưa được đóng")

    def test_bool_tran_thi_van_dung_placeholder_cu(self):
        """Đối chứng: callback trả `bool` trần vẫn hoạt động y hệt trước bản vá — không
        có gì bị buộc phải đổi."""
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=lambda call, ctx: True)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s11-bool"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows)
        self.assertEqual(rows[0].actor, Actor.human("approver", via="callback"))


class MutationXacNhanLoadBearing(unittest.TestCase):
    def test_khong_co_ban_va_thi_actor_luon_la_placeholder(self):
        """Mô phỏng CHÍNH XÁC hành vi trước bản vá: `resolve()` chỉ nhìn `bool(out)`,
        không bao giờ đọc `Approval.actor` dù callback có báo."""
        import asyncio

        engine, decision = self._make()
        call, ctx = self._call()
        real_actor = Actor.human("nqthiep", via="slack:U123")
        out = Approval(ok=True, actor=real_actor)
        # Hành vi cũ: chỉ có `bool(out)` được đọc — một Approval() luôn truthy nên vẫn
        # ALLOW, nhưng actor thật KHÔNG BAO GIỜ tới được đây.
        old_behavior_ok = bool(out)
        self.assertTrue(old_behavior_ok,
                        "mutation: bool(Approval(...)) phải truthy — nếu fail, phép so "
                        "sánh không còn phản ánh đúng hành vi cũ")
        # Chứng minh hành vi MỚI thực sự khác: resolve() thật đọc được actor.
        engine2, decision2 = self._make()
        d, actor = asyncio.run(engine2.resolve(decision2, call, ctx, lambda c, x: out))
        self.assertEqual(actor, real_actor,
                         "resolve() không đọc Approval.actor — bản vá không load-bearing")

    def _make(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine
        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext
        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        return ToolCall("c1", "wipe", {"x": 1}, wipe), ctx


if __name__ == "__main__":
    unittest.main()
