"""M6/T-6.3 (docs/17-research-alignment.md): retry theo effect class.

`EFFECT_PROFILES[...].retryable` đã được suy ra từ Round 5 (ADR-003: read/external
`True`, write/danger `False`) nhưng trước bản vá này KHÔNG có gì đọc nó để thật sự retry
— chỉ dùng để dán nhãn `retryable=` lên một sự kiện `error.raised`, và ở
`Agent.aresume()` để quyết định tool nào KHÔNG được tự động chạy lại sau resume (khác
concept — đó là sau crash, đây là trong một lần gọi).

Sửa: `Dispatcher._invoke()` giờ thử lại tối đa `MAX_ATTEMPTS` lần cho tool `read`/
`external` khi `fn()` raise (trừ `CancelledError`, không bao giờ retry), có backoff tăng
dần; `write`/`danger` luôn đúng MỘT lần thử, không hơn — double-effect từ một retry vào
một side effect có kết quả CHƯA BIẾT là đúng lớp lỗi S-4/idempotency (T-6.1, chưa xây)
tồn tại để ngăn.
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.observe.events import EventKind

CALLS: list[str] = []


class Boom(RuntimeError):
    pass


def _make_flaky(effect: str, *, fail_times: int):
    """Trả một tool `effect` cho trước, raise `fail_times` lần đầu rồi thành công."""
    state = {"n": 0}

    if effect == "read":
        @tool(effect="read")
        def flaky(x: int) -> str:
            """Đọc một cái gì đó, đôi khi hỏng."""
            CALLS.append(f"read:{x}")
            state["n"] += 1
            if state["n"] <= fail_times:
                raise Boom(f"lần {state['n']} hỏng")
            return "ok"
        return flaky
    if effect == "external":
        @tool(effect="external")
        def flaky(x: int) -> str:
            """Lấy về từ internet, đôi khi hỏng."""
            CALLS.append(f"external:{x}")
            state["n"] += 1
            if state["n"] <= fail_times:
                raise Boom(f"lần {state['n']} hỏng")
            return "ok"
        return flaky
    if effect == "write":
        @tool(effect="write")
        def flaky(x: int) -> str:
            """Ghi một cái gì đó, đôi khi hỏng."""
            CALLS.append(f"write:{x}")
            state["n"] += 1
            if state["n"] <= fail_times:
                raise Boom(f"lần {state['n']} hỏng")
            return "ok"
        return flaky
    if effect == "danger":
        @tool(effect="danger")
        def flaky(x: int) -> str:
            """Làm một việc không thể huỷ, đôi khi hỏng."""
            CALLS.append(f"danger:{x}")
            state["n"] += 1
            if state["n"] <= fail_times:
                raise Boom(f"lần {state['n']} hỏng")
            return "ok"
        return flaky
    raise ValueError(effect)


class Sink:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


class RetryTheoEffectClass(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_read_hong_mot_lan_duoc_retry_thanh_cong(self):
        t = _make_flaky("read", fail_times=1)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[t], budget="$5")
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(CALLS, ["read:1", "read:1"],
                         "read hỏng 1 lần phải được gọi lại — retryable=True")

    def test_external_hong_mot_lan_duoc_retry_thanh_cong(self):
        t = _make_flaky("external", fail_times=1)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[t], budget="$5")
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(CALLS, ["external:1", "external:1"])

    def test_write_khong_bao_gio_duoc_retry(self):
        t = _make_flaky("write", fail_times=1)
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[t], budget="$5", exporters=[sink])
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)     # model vẫn được trả lỗi, tự quyết định tiếp
        self.assertEqual(CALLS, ["write:1"],
                         "write hỏng vẫn chỉ được thử ĐÚNG MỘT LẦN — retry là double-effect")
        starts = [e for e in sink.events if e.kind is EventKind.TOOL_STARTED]
        self.assertEqual(len(starts), 1, "chỉ một TOOL_STARTED — không có lượt thử thứ hai")

    def test_danger_khong_bao_gio_duoc_retry(self):
        t = _make_flaky("danger", fail_times=1)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[t], budget="$5",
                      approve=lambda call, ctx: True)
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(CALLS, ["danger:1"],
                         "danger hỏng vẫn chỉ được thử ĐÚNG MỘT LẦN")

    def test_het_so_lan_thu_van_tra_loi_that_bai_khong_treo(self):
        """Hỏng NHIỀU hơn `MAX_ATTEMPTS` lần → hết lượt retry, trả tool_result lỗi bình
        thường (không crash, không treo)."""
        from harness.dispatch import MAX_ATTEMPTS
        t = _make_flaky("read", fail_times=MAX_ATTEMPTS + 5)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[t], budget="$5")
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(len(CALLS), MAX_ATTEMPTS,
                         f"phải dừng đúng sau {MAX_ATTEMPTS} lần thử, không hơn")


class MutationRetryCoTacDung(unittest.TestCase):
    def test_max_attempts_1_lam_test_read_do(self):
        """Mutation thật: ép `MAX_ATTEMPTS = 1` (mô phỏng hành vi CŨ — đúng một lần thử
        cho mọi effect) — xác nhận `CALLS` không còn ĐÚNG HAI phần tử, chứng minh test
        `test_read_hong_mot_lan_duoc_retry_thanh_cong` thật sự phụ thuộc vào nhánh retry
        mới, không phải một side effect khác."""
        import harness.dispatch as dispatch_mod
        old = dispatch_mod.MAX_ATTEMPTS
        dispatch_mod.MAX_ATTEMPTS = 1
        try:
            CALLS.clear()
            t = _make_flaky("read", fail_times=1)
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.tool_call("flaky", {"x": 1}),
                                              FakeModel.text("xong")]),
                          tools=[t], budget="$5")
            agent.try_run("thử")
            self.assertEqual(CALLS, ["read:1"],
                             "với MAX_ATTEMPTS=1, read hỏng không được gọi lại — nếu vẫn "
                             "ra 2 phần tử, retry không thật sự phụ thuộc vào hằng số này")
        finally:
            dispatch_mod.MAX_ATTEMPTS = old


class LangGraphCungPhaiRetryDungLuat(unittest.TestCase):
    """Backend LangGraph có một implementation `_run_tools` RIÊNG, không dùng chung
    `dispatch.py::Dispatcher` — parity phải được sửa/kiểm ở CẢ HAI nơi (đúng bài học
    ADR-038/IDL-48: đây là lớp lỗi "sửa một backend, quên backend kia" đã lặp lại nhiều
    lần trong lịch sử dự án)."""

    def test_lg_read_hong_mot_lan_duoc_retry(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        state = {"n": 0}

        @tool(effect="read")
        def flaky(x: int) -> str:
            """Đọc, đôi khi hỏng."""
            state["n"] += 1
            if state["n"] == 1:
                raise Boom("hỏng lần đầu")
            return "ok"

        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.call("flaky", {"x": 1}), FakeChat.text("xong")]),
            tools=[flaky], budget="$5", checkpointer=MemorySaver())
        from langchain_core.messages import HumanMessage
        graph.invoke({"messages": [HumanMessage("thử")]},
                     config={"configurable": {"thread_id": "t1"}})
        self.assertEqual(state["n"], 2, "read hỏng 1 lần phải được LangGraph backend gọi lại")

    def test_lg_write_khong_bao_gio_duoc_retry(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        state = {"n": 0}

        @tool(effect="write")
        def flaky(x: int) -> str:
            """Ghi, đôi khi hỏng."""
            state["n"] += 1
            if state["n"] == 1:
                raise Boom("hỏng lần đầu")
            return "ok"

        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.call("flaky", {"x": 1}), FakeChat.text("xong")]),
            tools=[flaky], budget="$5", checkpointer=MemorySaver())
        from langchain_core.messages import HumanMessage
        graph.invoke({"messages": [HumanMessage("thử")]},
                     config={"configurable": {"thread_id": "t1"}})
        self.assertEqual(state["n"], 1, "write hỏng KHÔNG được LangGraph backend gọi lại")


if __name__ == "__main__":
    unittest.main()
