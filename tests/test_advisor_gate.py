"""Advisor-consultation gate — a `RequireBeforePolicy` (policy/builtin.py) that DENIES a
tool's attempt until another tool ("consult the advisor") has already been called earlier
in the run. Distinct from ADR-006 (no automatic model routing): nothing here picks a
model, and nothing here GRANTS a `Decision` — it only ever restricts (P-2), same as every
other `Policy`. The advisor tool itself is read for its OPINION like any other tool
(`search`, `fetch`); the actual ALLOW for the gated tool still requires whatever
`approve=`/effect policy already required — design/00-foundation.md §4.2's invariant D-1
(`Actor` has no `Model` variant) is why an advisor can never itself be that grant.

`ctx.tools_called` — the mechanism this policy reads — carries tool NAMES only, never
arguments or results (IDL-15), populated from calls that already COMPLETED earlier in the
run: `dispatch.py::RunContext.tools_called` (classic, from `Dispatcher.ran`) and
`lg/runtime.py::Runtime._tools_called()` (durable, scanned from checkpointed
`state["messages"]`).
"""
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from harness.policy.base import ToolCall, Verdict
from harness.policy.builtin import RequireBeforePolicy


class _Spec:
    def __init__(self, name):
        self.name = name


class _Ctx:
    def __init__(self, tools_called=frozenset()):
        self.tools_called = tools_called


def _call(name):
    return ToolCall(id="c1", name=name, arguments={}, spec=_Spec(name))


class RequireBeforePolicyUnit(unittest.TestCase):
    def test_thieu_prerequisite_thi_deny(self):
        p = RequireBeforePolicy(tool="wipe", requires="consult_advisor")
        r = p.check(_call("wipe"), _Ctx(tools_called=frozenset()))
        self.assertEqual(r.verdict, Verdict.DENY)
        self.assertIn("consult_advisor", r.reason)

    def test_co_prerequisite_thi_khong_chan(self):
        p = RequireBeforePolicy(tool="wipe", requires="consult_advisor")
        r = p.check(_call("wipe"), _Ctx(tools_called=frozenset({"consult_advisor"})))
        self.assertEqual(r.verdict, Verdict.ALLOW)

    def test_tool_khac_khong_bi_anh_huong(self):
        """Chỉ đúng tool được cấu hình mới bị chặn — mọi tool khác đi qua trong suốt."""
        p = RequireBeforePolicy(tool="wipe", requires="consult_advisor")
        r = p.check(_call("look_up"), _Ctx(tools_called=frozenset()))
        self.assertEqual(r.verdict, Verdict.ALLOW)

    def test_khong_tools_called_tren_ctx_khong_crash(self):
        """Một `ctx` không có thuộc tính `tools_called` (ví dụ ctx tự dựng trong test
        khác, không qua RunContext/_Ctx) vẫn không crash — coi như rỗng."""
        p = RequireBeforePolicy(tool="wipe", requires="consult_advisor")
        class BareCtx: pass
        r = p.check(_call("wipe"), BareCtx())
        self.assertEqual(r.verdict, Verdict.DENY)

    def test_deny_thang_ca_khi_co_approve_callback_dong_y(self):
        """P-2: policy chỉ được RESTRICT — DENY của policy này phải thắng một
        `approve=` callback đồng ý, qua đúng phép compose max() PolicyEngine.decide()
        dùng cho các Policy khác (KHÔNG phải qua resolve() — DENY ở decide() thì
        không bao giờ tới approve= nữa)."""
        from harness.policy.engine import PolicyEngine

        gate = RequireBeforePolicy(tool="wipe", requires="consult_advisor")
        engine = PolicyEngine(builtins=(gate,))
        d = engine.decide(_call("wipe"), _Ctx(tools_called=frozenset()))
        self.assertEqual(d.verdict, Verdict.DENY)


class ClassicBackendEndToEnd(unittest.TestCase):
    """`Dispatcher.ran` → `RunContext.tools_called` — chạy thật qua `Agent`."""

    def test_goi_danger_truoc_khi_tu_van_thi_bi_chan(self):
        from harness import Agent, tool
        from harness.models.fake import FakeModel
        from harness.policy.builtin import RequireBeforePolicy

        RAN = []

        @tool(effect="read")
        def consult_advisor(question: str) -> str:
            """Hỏi ý kiến advisor trước một hành động nguy hiểm."""
            RAN.append("consult_advisor")
            return "ý kiến: cẩn thận"

        @tool(effect="danger")
        def wipe(x: int) -> str:
            """Xoá."""
            RAN.append("wipe")
            return "gone"

        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script),
                      tools=[consult_advisor, wipe], budget="$5",
                      policies=[RequireBeforePolicy(tool="wipe", requires="consult_advisor")],
                      approve=lambda c, x: True)   # cho qua nếu policy không chặn
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(RAN, [], "wipe không được chạy khi chưa tư vấn")
        self.assertEqual(r.tools_run, ())

    def test_tu_van_truoc_roi_moi_goi_danger_thi_qua(self):
        from harness import Agent, tool
        from harness.models.fake import FakeModel
        from harness.policy.builtin import RequireBeforePolicy

        RAN = []

        @tool(effect="read")
        def consult_advisor(question: str) -> str:
            """Hỏi ý kiến advisor trước một hành động nguy hiểm."""
            RAN.append("consult_advisor")
            return "ý kiến: ổn, làm đi"

        @tool(effect="danger")
        def wipe(x: int) -> str:
            """Xoá."""
            RAN.append("wipe")
            return "gone"

        # Hai bước tách biệt — model PHẢI thấy kết quả tư vấn (một lượt gọi model riêng)
        # trước khi được phép gọi wipe, đúng luồng turn-based thật.
        script = [FakeModel.tool_call("consult_advisor", {"question": "xoá được không?"}),
                 FakeModel.tool_call("wipe", {"x": 1}),
                 FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script),
                      tools=[consult_advisor, wipe], budget="$5",
                      policies=[RequireBeforePolicy(tool="wipe", requires="consult_advisor")],
                      approve=lambda c, x: True)
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(RAN, ["consult_advisor", "wipe"])
        self.assertEqual(r.tools_run, ("consult_advisor", "wipe"))

    def test_goi_ca_hai_trong_cung_mot_luot_van_bi_chan(self):
        """Cùng MỘT lượt model (cùng batch) gọi cả hai tool — advisor CHƯA trả lời (chưa
        có ToolMessage) tại thời điểm policy quyết định wipe, nên vẫn phải chặn. Đây là
        đúng ngữ nghĩa "tư vấn TRƯỚC", không phải "tư vấn CÙNG LÚC"."""
        from harness import Agent, tool
        from harness.models.base import ModelResponse
        from harness.models.fake import FakeModel
        from harness.policy.builtin import RequireBeforePolicy
        from harness.result import Usage

        RAN = []

        @tool(effect="read")
        def consult_advisor(question: str) -> str:
            """Hỏi ý kiến advisor."""
            RAN.append("consult_advisor")
            return "ý kiến"

        @tool(effect="danger")
        def wipe(x: int) -> str:
            """Xoá."""
            RAN.append("wipe")
            return "gone"

        both_in_one_turn = ModelResponse(
            ({"type": "tool_use", "id": "c1", "name": "consult_advisor",
              "input": {"question": "?"}},
             {"type": "tool_use", "id": "c2", "name": "wipe", "input": {"x": 1}}),
            "tool_use", Usage(100, 15), "fake")
        script = [both_in_one_turn, FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script),
                      tools=[consult_advisor, wipe], budget="$5",
                      policies=[RequireBeforePolicy(tool="wipe", requires="consult_advisor")],
                      approve=lambda c, x: True)
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertIn("consult_advisor", RAN)
        self.assertNotIn("wipe", RAN, "wipe cùng batch với consult_advisor vẫn phải bị chặn")


class DurableBackendEndToEnd(unittest.TestCase):
    """`Runtime._tools_called()` — chạy thật qua `build_agent()`."""

    def test_goi_danger_truoc_khi_tu_van_thi_bi_chan(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness import tool
        from harness.lg import build_agent
        from harness.policy.builtin import RequireBeforePolicy

        RAN = []

        @tool(effect="read")
        def consult_advisor(question: str) -> str:
            """Hỏi ý kiến advisor."""
            RAN.append("consult_advisor")
            return "ý kiến"

        @tool(effect="danger")
        def wipe(x: int) -> str:
            """Xoá."""
            RAN.append("wipe")
            return "gone"

        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}), FakeChat.text("xong")]),
            tools=[consult_advisor, wipe], budget="$5", checkpointer=MemorySaver(),
            policies=[lambda: RequireBeforePolicy(tool="wipe", requires="consult_advisor")],
            approve=lambda c, x: True)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-advisor-deny"}})
        self.assertEqual(RAN, [])

    def test_tu_van_truoc_roi_moi_goi_danger_thi_qua(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness import tool
        from harness.lg import build_agent
        from harness.policy.builtin import RequireBeforePolicy

        RAN = []

        @tool(effect="read")
        def consult_advisor(question: str) -> str:
            """Hỏi ý kiến advisor."""
            RAN.append("consult_advisor")
            return "ý kiến"

        @tool(effect="danger")
        def wipe(x: int) -> str:
            """Xoá."""
            RAN.append("wipe")
            return "gone"

        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.call("consult_advisor", {"question": "?"}),
                                   FakeChat.call("wipe", {"x": 1}), FakeChat.text("xong")]),
            tools=[consult_advisor, wipe], budget="$5", checkpointer=MemorySaver(),
            policies=[lambda: RequireBeforePolicy(tool="wipe", requires="consult_advisor")],
            approve=lambda c, x: True)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-advisor-allow"}})
        self.assertEqual(RAN, ["consult_advisor", "wipe"])


if __name__ == "__main__":
    unittest.main()
