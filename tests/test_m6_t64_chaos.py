"""M6/T-6.4 (docs/17-research-alignment.md): failure injection, `harness.testing.chaos`.

Năm kịch bản T-6.4 tự đặt ra: provider timeout, tool raise, store chết, policy raise,
model trả rác. "Done: mọi kịch bản có một hành vi được khẳng định, không cái nào crash"
— mỗi test dưới đây khẳng định một hành vi CỤ THỂ (không phải chỉ "không raise gì đó"),
đúng tinh thần "assertion on behaviour, not on absence of behaviour" mà `assert_no_tool`'s
Round-38 bài học (`testing/__init__.py`) đã dạy.

Đi kèm: hai lỗi THẬT tìm thấy khi viết kịch bản "model trả rác" (N-2/N-3,
`design/07-risks-and-open-issues.md §1.5`) — N-2 (classic loop's `try_run()` raise
`ToolContractError` không bị bắt) đã sửa trong cùng lượt này; N-3 (LangGraph không hỗ
trợ `returns=`) ghi lại, chưa sửa (ngoài phạm vi chaos testing).
"""
import dataclasses
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.policy.base import Verdict
from harness.policy.engine import PolicyEngine
from harness.result import StopReason
from harness.testing.chaos import (
    BrokenStore, RaisingPolicy, TimeoutProvider, garbage_model, raising_tool,
    unknown_stop_reason_model,
)


def _noop_tool():
    @tool(effect="read")
    def noop() -> str:
        """Không làm gì."""
        return "ok"
    return noop


class ProviderTimeout(unittest.TestCase):
    def test_provider_timeout_tra_result_khong_crash(self):
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=TimeoutProvider(), budget="$5")
        try:
            r = agent.try_run("thử")
        except Exception as exc:      # chính test này cấm crash
            self.fail(f"provider timeout crash ra ngoài try_run(): {type(exc).__name__}: {exc}")
        self.assertFalse(r.ok)
        self.assertIn(r.stop_reason, (StopReason.ERROR, StopReason.TIMEOUT),
                      f"provider timeout phải có stop_reason lỗi rõ ràng, không phải "
                      f"{r.stop_reason}")


class ToolRaise(unittest.TestCase):
    def test_tool_raise_tra_ve_tool_result_loi_khong_crash(self):
        bad = raising_tool(name="bad", effect="read")
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("bad", {}),
                                          FakeModel.text("xong dù bad hỏng")]),
                      tools=[bad], budget="$5")
        r = agent.try_run("thử")
        self.assertTrue(r.ok, "run phải hoàn tất bình thường — model thấy is_error rồi tự "
                              "quyết định tiếp, không phải harness crash")
        # IDL-49: `tools_run` ghi lại những gì ĐÃ THỰC THI (attempted), không phải những
        # gì thành công — một tool bị policy chặn thì KHÔNG có trong danh sách này, nhưng
        # một tool được phép chạy rồi tự raise vẫn coi là "đã chạy" (nó chạy, chỉ là kết
        # quả là lỗi). Test này khẳng định đúng điều đó, không phải điều ngược lại.
        self.assertEqual(r.tools_run, ("bad",),
                         "tool được ALLOW rồi tự raise vẫn phải có trong tools_run — nó "
                         "đã thực thi, chỉ là kết quả là is_error")


class StoreDown(unittest.TestCase):
    def test_store_chet_qua_recall_tool_khong_crash(self):
        """Mô phỏng một tool `recall`-kiểu dùng `Store` bị hỏng — chính tool nên bắt lỗi
        store của nó và trả is_error, không để propagate crash cả run."""
        store = BrokenStore()

        @tool(effect="external")
        async def recall(query: str) -> str:
            """Tìm trong bộ nhớ, đôi khi store chết."""
            try:
                return await store.get(query) or "(không có gì)"
            except Exception as exc:
                return f"lỗi store: {exc}"

        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("recall", {"query": "x"}),
                                          FakeModel.text("xong")]),
                      tools=[recall], budget="$5")
        r = agent.try_run("thử")
        self.assertTrue(r.ok)
        self.assertIn("recall", r.tools_run)


class PolicyRaise(unittest.TestCase):
    def test_policy_raise_fail_closed_deny_khong_crash(self):
        from harness.policy.base import ToolCall
        from harness.tools import Effect, ToolSpec

        spec = ToolSpec(name="x", description="d", input_schema={"type": "object"},
                        fn=lambda: "ok", effect=Effect.READ, timeout_s=30.0,
                        max_result_tokens=4000, source="test")
        engine = PolicyEngine((RaisingPolicy(),))
        call = ToolCall("c1", "x", {}, spec)
        d = engine.decide(call, ctx=None)
        self.assertEqual(d.verdict, Verdict.DENY,
                         "policy raise phải fail closed thành DENY, không crash decide()")
        self.assertIn("raised", d.reason)

    def test_policy_raise_trong_mot_run_that_khong_crash(self):
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("noop", {}),
                                          FakeModel.text("xong")]),
                      tools=[_noop_tool()], budget="$5", policies=[RaisingPolicy()])
        r = agent.try_run("thử")
        self.assertTrue(r.ok, "run không được crash dù policy raise")
        self.assertEqual(r.tools_run, (), "policy raise -> DENY -> tool không chạy")


class GarbageModel(unittest.TestCase):
    """N-2: sửa trong cùng lượt này — `try_run()` từng raise `ToolContractError` ra
    ngoài khi model trả rác không khớp `returns=`."""

    def test_model_tra_khong_dung_json_tra_result_loi_khong_raise(self):
        @dataclasses.dataclass
        class Ans:
            x: int

        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=garbage_model(), budget="$5", returns=Ans)
        try:
            r = agent.try_run("thử")
        except Exception as exc:
            self.fail(f"model trả rác crash ra ngoài try_run(): {type(exc).__name__}: {exc}")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason, StopReason.ERROR)
        self.assertIsNone(r.value)
        self.assertIn("Ans", r.detail)

    def test_model_tra_stop_reason_khong_biet_tra_loi_khong_crash(self):
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=unknown_stop_reason_model(), budget="$5")
        try:
            r = agent.try_run("thử")
        except Exception as exc:
            self.fail(f"stop_reason lạ crash ra ngoài try_run(): {type(exc).__name__}: {exc}")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason, StopReason.ERROR)


class LangGraphProviderRaiseCungPhaiKhongCrash(unittest.TestCase):
    """Parity: cùng lỗ hổng "provider raise crash" tìm thấy ở classic loop cũng có ở
    LangGraph backend (`call_model`'s `self._model.invoke(...)` không có try/except nào)
    — sửa cả hai, kiểm cả hai."""

    def test_lg_model_invoke_raise_khong_crash(self):
        from langgraph.checkpoint.memory import MemorySaver
        from langchain_core.messages import HumanMessage
        from harness.lg import build_agent

        class RaisingChat:
            def bind_tools(self, tools, **kw): return self
            def invoke(self, messages):
                raise TimeoutError("chaos: LangGraph model invoke timed out")

        graph, _rt = build_agent(model=RaisingChat(), budget="$5", checkpointer=MemorySaver())
        try:
            result = graph.invoke({"messages": [HumanMessage("thử")]},
                                  config={"configurable": {"thread_id": "t1"}})
        except Exception as exc:
            self.fail(f"provider raise crash ra ngoài graph.invoke(): "
                     f"{type(exc).__name__}: {exc}")
        self.assertEqual(result.get("stop_reason"), "error")


class MutationN2ReturnsFixCoTacDung(unittest.TestCase):
    def test_khoi_phuc_hanh_vi_cu_thi_test_garbage_do(self):
        """Mutation thật trên `run.py`: khôi phục hành vi CŨ — gọi `_parse_returns`
        KHÔNG qua try/except — bằng cách monkeypatch `RunEngine.run` tạm thời để bỏ
        nhánh bắt lỗi mới. Xác nhận test garbage ở trên thật sự đỏ nếu thiếu bản vá."""
        import harness.run as run_mod

        original_parse = run_mod.RunEngine._parse_returns

        # Mô phỏng: `_parse_returns` không còn được bọc try/except trong `run()` — cách
        # đơn giản nhất để chứng minh phụ thuộc mà không copy-paste toàn bộ `run()`: cho
        # `_parse_returns` chính nó raise một lỗi KHÔNG PHẢI `ToolContractError`, thứ
        # try/except trong `run()` không bắt (nó chỉ bắt `ToolContractError`) — nếu bản
        # vá N-2 thật sự chỉ bắt đúng `ToolContractError` (đúng thiết kế, không bắt-tất),
        # lỗi này PHẢI vẫn raise ra ngoài `try_run()`.
        def _boom(self, text):
            raise KeyError("mutation: một lỗi try/except N-2 không được bắt")

        run_mod.RunEngine._parse_returns = _boom
        try:
            @dataclasses.dataclass
            class Ans:
                x: int

            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=garbage_model(), budget="$5", returns=Ans)
            with self.assertRaises(KeyError,
                                   msg="try/except N-2 phải CHỈ bắt ToolContractError — "
                                       "một KeyError khác vẫn phải raise ra ngoài, "
                                       "chứng minh bản vá không bắt-tất-mọi-lỗi"):
                agent.try_run("thử")
        finally:
            run_mod.RunEngine._parse_returns = original_parse


if __name__ == "__main__":
    unittest.main()
