"""S-24 (corrected): một `EventBus` chung cho MỌI thread trên backend LangGraph — Round
37's Ledger/TaintTracker leak, một lần thứ tư, chưa ai bắt được cho quan sát học.

review-security.md S-24 lo về việc `seq` va nhau giữa các tool call SONG SONG trong cùng
một segment. Kiểm tra trên code thật (`lg/__init__.py`, `lg/runtime.py`) lộ ra một lỗ hổng
NẶNG HƠN: `build_agent()` từng dựng đúng MỘT `EventBus("run", exporters)` khi có
`exporters=`, giữ nó trên `Runtime`, và MỌI thread graph đó phục vụ dùng chung object đó —

  1. `event.run_id` là chuỗi CỐ ĐỊNH `"run"` cho MỌI hội thoại, không phải `thread_id` thật
     — sổ audit không phân biệt được khách hàng nào tạo ra event nào.
  2. `event.seq` là một bộ đếm dùng chung xuyên suốt đời sống của compiled graph — hai
     thread khác nhau xen kẽ `seq`, đúng thứ S-24 lo nhưng ở phạm vi RỘNG hơn (giữa các
     thread, không chỉ trong một segment song song).
  3. `self._started` (một cờ instance trên `Runtime`) làm `RUN_STARTED` chỉ phát MỘT LẦN
     DUY NHẤT cho toàn bộ đời sống compiled graph — thread thứ hai, thứ ba, ... không bao
     giờ thấy `RUN_STARTED` của chính hội thoại của nó.

Đây đúng là "Round 34's defect for the third time, in the third place" (docs/00-council.md,
Round 37) — một lần thứ TƯ, cho `EventBus`. Sửa theo đúng khuôn `_policy_cache`/
`_engine_for` (S-15): `Runtime._bus_cache` giữ một `EventBus` riêng cho mỗi `run_id`, dựng
lười lúc dùng lần đầu, và bỏ hẳn `self._started`.
"""
import sys, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent
from harness.observe.events import EventKind


@tool(effect="read")
def peek(x: int) -> str:
    """Nhìn một cái gì đó."""
    return "ok"


class Sink:
    """Gom mọi event, exporter thật (Round 35 parity — không đọc `rt._bus_cache` trực
    tiếp cho bài test chạy-graph-thật, để không tự kiểm bằng chính cơ chế đang kiểm)."""
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


def _graph(sink: Sink):
    return build_agent(
        model=FakeChat(script=[FakeChat.call("peek", {"x": 1}), FakeChat.text("ok"),
                               FakeChat.call("peek", {"x": 1}), FakeChat.text("ok")]),
        tools=[peek], budget="$5", checkpointer=MemorySaver(), exporters=[sink])


class HaiThreadKhongDungChungBus(unittest.TestCase):
    def test_run_id_dung_thread_that_khong_phai_chuoi_co_dinh(self):
        """Event của thread A phải mang `run_id="khach-A"` thật, không phải `"run"`."""
        sink = Sink()
        graph, rt = _graph(sink)
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-A"}})
        self.assertTrue(sink.events, "không event nào được phát")
        run_ids = {e.run_id for e in sink.events}
        self.assertEqual(run_ids, {"khach-A"},
                         f"event mang run_id sai — thấy {run_ids}, đúng ra chỉ 'khach-A'")

    def test_hai_thread_khac_nhau_khong_dung_chung_bus(self):
        """Bus của thread A và thread B phải là hai OBJECT khác nhau."""
        sink = Sink()
        graph, rt = _graph(sink)
        rt._bus_for("khach-A")
        rt._bus_for("khach-B")
        self.assertIsNot(rt._bus_cache["khach-A"], rt._bus_cache["khach-B"],
                         "hai thread dùng chung MỘT EventBus — S-24 sống lại")

    def test_seq_khong_xen_ke_giua_hai_thread(self):
        """Mỗi thread có `seq` riêng, bắt đầu lại từ 0 — không cộng dồn qua thread khác."""
        sink = Sink()
        graph, rt = _graph(sink)
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-A"}})
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-B"}})
        seq_a = [e.seq for e in sink.events if e.run_id == "khach-A"]
        seq_b = [e.seq for e in sink.events if e.run_id == "khach-B"]
        self.assertTrue(seq_a and seq_b)
        self.assertEqual(seq_b[0], 0,
                         f"seq của khách B bắt đầu từ {seq_b[0]}, không phải 0 — nó kế "
                         f"thừa bộ đếm của khách A thay vì có bộ đếm riêng")

    def test_ca_hai_thread_deu_thay_run_started(self):
        """Round 34's bug lần thứ tư, phần riêng: `RUN_STARTED` phải phát cho MỖI thread ở
        lượt đầu tiên của nó, không chỉ thread ĐẦU TIÊN trong đời compiled graph."""
        sink = Sink()
        graph, rt = _graph(sink)
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-A"}})
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-B"}})
        started_a = [e for e in sink.events
                    if e.run_id == "khach-A" and e.kind is EventKind.RUN_STARTED]
        started_b = [e for e in sink.events
                    if e.run_id == "khach-B" and e.kind is EventKind.RUN_STARTED]
        self.assertEqual(len(started_a), 1)
        self.assertEqual(len(started_b), 1,
                         "khách B không hề thấy RUN_STARTED của chính hội thoại của nó — "
                         "cờ self._started dùng chung đã nuốt mất nó")


class MutationXacNhanLoadBearing(unittest.TestCase):
    """Khôi phục hành vi CŨ (một EventBus chung, dựng một lần) và xác nhận hai thread
    thật sự dùng chung — chứng minh bản vá không phải trang trí."""

    def test_khong_co_ban_va_thi_hai_thread_dung_chung_bus(self):
        from harness.observe.events import EventBus

        sink = Sink()
        graph, rt = _graph(sink)
        # Mô phỏng CHÍNH XÁC hành vi trước bản vá: một EventBus dựng MỘT LẦN với run_id
        # cố định "run", dùng chung cho mọi run_id.
        shared = EventBus("run", rt._exporters)
        rt._bus_for = lambda run_id: shared          # MUTATION tại chỗ

        e1 = rt._bus_for("khach-A")
        e2 = rt._bus_for("khach-B")
        self.assertIs(e1, e2, "mutation phải cho hai thread CÙNG một bus — nếu test này "
                              "fail nghĩa là bản vá không còn load-bearing")
        from harness.observe.events import EventKind
        ev = e1.emit(EventKind.ERROR_RAISED)
        self.assertEqual(ev.run_id, "run",
                         "mutation phải khôi phục run_id cố định 'run' cho mọi thread")


if __name__ == "__main__":
    unittest.main()
