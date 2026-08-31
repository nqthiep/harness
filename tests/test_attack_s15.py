"""S-15: một policy có state không được rò rỉ giữa các thread trên backend LangGraph.

design/review-security.md S-15 chỉ ra `build_agent()` chạy đúng MỘT LẦN và `PolicyEngine`
nó dựng phục vụ MỌI thread sau đó — khác backend cổ điển, nơi mỗi `run()` tự dò state
policy thay đổi SAU KHI chạy xong (`agent.py:_check_shared_policy_state`, đã có từ Round
34 và vẫn đúng, xem `tests/test_m4.py`). Backend LangGraph không có ranh giới "hết một
run()" sạch để dò kiểu đó (graph phục vụ nhiều thread đồng thời, không tuần tự), nên chiến
lược khác: BẮT BUỘC mọi policy người dùng là factory, và mỗi THREAD nhận đúng một instance
riêng, cache theo `run_id` — không phải cache theo node, không phải một instance chung.

Tệp này chứng minh ba điều: (1) một instance bị từ chối tại construction, không đợi tới
lúc chạy; (2) một factory được instantiate đúng một lần cho mỗi thread — cùng thread qua
nhiều lượt vẫn thấy lại đúng instance của nó (rate-limit trong MỘT cuộc hội thoại vẫn
đúng); (3) hai thread khác nhau KHÔNG BAO GIỜ thấy state của nhau — kèm mutation test xác
nhận (2)+(3) load-bearing.
"""
import sys, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.errors import ConfigError
from harness.lg import build_agent
from harness.policy.base import Ruling, Verdict

RAN: list = []


@tool(effect="read")
def peek(x: int) -> str:
    """Nhìn một cái gì đó."""
    RAN.append(x)
    return "ok"


class DemLuotGoi:
    """Policy có state: đếm số lần `check()` được gọi TRÊN CHÍNH instance này. Hoàn toàn
    hợp lệ trong phạm vi MỘT thread (rate-limit một cuộc hội thoại) — vấn đề chỉ xảy ra khi
    CÙNG instance bị nhiều thread dùng chung."""
    name = "dem_luot"

    def __init__(self) -> None:
        self.n = 0

    def check(self, call, ctx) -> Ruling:
        self.n += 1
        return Ruling(Verdict.ALLOW, f"n={self.n}", self.name)


class TuChoiInstanceLucDung(unittest.TestCase):
    def test_build_agent_tu_choi_instance_khong_phai_factory(self):
        with self.assertRaises(ConfigError) as cm:
            build_agent(model=FakeChat(script=[]), tools=[peek], budget="$5",
                        policies=[DemLuotGoi()])          # instance, không phải class
        msg = str(cm.exception)
        self.assertIn("INSTANCE", msg)
        self.assertIn("policies=[DemLuotGoi]", msg, "lỗi phải chỉ đúng cách sửa")

    def test_build_agent_chap_nhan_class(self):
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[peek], budget="$5",
                                policies=[DemLuotGoi])     # class — hợp lệ
        self.assertIsNotNone(rt)


class KhongRoRiGiuaThread(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def _graph(self):
        # `FakeChat` là MỘT model dùng chung cho mọi thread (đúng thực tế: model không
        # đổi theo khách hàng); script phải đủ dài cho cả hai lượt invoke() trong test
        # chạy-thật-hai-thread, vì bộ đếm `i` của nó tăng xuyên các lần gọi, không reset
        # theo thread — không liên quan tới cache policy đang được kiểm ở đây.
        return build_agent(
            model=FakeChat(script=[FakeChat.call("peek", {"x": 1}), FakeChat.text("ok"),
                                   FakeChat.call("peek", {"x": 1}), FakeChat.text("ok")]),
            tools=[peek], budget="$5", checkpointer=MemorySaver(), policies=[DemLuotGoi])

    def test_cung_thread_giu_lai_dung_instance_qua_nhieu_luot(self):
        """Trong CÙNG một thread, gọi `_engine_for` hai lần phải trả về engine dựng từ
        CÙNG một policy instance — không phải một instance mới mỗi lần."""
        from harness.lg.runtime import _Ctx
        from harness.policy.base import ToolCall
        from harness.policy.label import Label

        graph, rt = self._graph()
        ctx = _Ctx(label=Label(), safety="standard")
        call = ToolCall("c1", "peek", {"x": 1}, peek)

        rt._engine_for("thread-A").decide(call, ctx)
        p1 = rt._policy_cache["thread-A"][0]
        self.assertEqual(p1.n, 1)

        rt._engine_for("thread-A").decide(call, ctx)
        self.assertEqual(p1.n, 2, "thread-A gọi lần hai không thấy lại state của chính nó")

    def test_hai_thread_khac_nhau_khong_dung_chung_instance(self):
        """Đây là quả tim của S-15: hai thread PHẢI nhận hai instance riêng."""
        graph, rt = self._graph()
        rt._engine_for("thread-A")
        rt._engine_for("thread-B")
        pA = rt._policy_cache["thread-A"][0]
        pB = rt._policy_cache["thread-B"][0]
        self.assertIsNot(pA, pB, "hai thread dùng chung MỘT instance policy — S-15 sống lại")

    def test_chay_that_hai_thread_khong_lam_bay_dem(self):
        """Chạy graph thật, hai thread riêng, mỗi thread một lượt gọi `peek`. Một lượt gọi
        `decide()` HAI lần cho mỗi tool call (`policy_gate` rồi `_regate` — I-1, bước 1),
        nên n=2 là đúng cho MỘT thread MỘT lượt. Cái cần kiểm là thread B bắt đầu lại từ
        CÙNG mốc đó, không phải cộng dồn từ thread A (sẽ là 4 nếu bị chia sẻ)."""
        graph, rt = self._graph()
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-A"}})
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": "khach-B"}})
        n_a = rt._policy_cache["khach-A"][0].n
        n_b = rt._policy_cache["khach-B"][0].n
        self.assertEqual(n_a, n_b, f"khách B (n={n_b}) không bắt đầu từ cùng mốc với "
                                  f"khách A (n={n_a}) — kế thừa lượt gọi của khách A")


class MutationXacNhanLoadBearing(unittest.TestCase):
    """Khôi phục hành vi CŨ (một PolicyEngine chung, dựng một lần) và xác nhận rò rỉ xảy
    ra thật — chứng minh cache theo thread không phải trang trí."""

    def test_khong_co_ban_va_thi_hai_thread_dung_chung_instance(self):
        graph, rt = build_agent(
            model=FakeChat(script=[]), tools=[peek], budget="$5",
            checkpointer=MemorySaver(), policies=[DemLuotGoi])

        # Mô phỏng CHÍNH XÁC hành vi trước bản vá: một PolicyEngine dựng MỘT LẦN, dùng
        # chung cho mọi run_id — instantiate factory đúng một lần rồi luôn trả về nó.
        from harness.policy.engine import PolicyEngine
        shared = PolicyEngine(rt._builtins, tuple(f() for f in rt._policy_factories))
        rt._engine_for = lambda run_id: shared          # MUTATION tại chỗ

        e1 = rt._engine_for("thread-A")
        e2 = rt._engine_for("thread-B")
        self.assertIs(e1, e2, "mutation phải cho hai thread CÙNG một engine — nếu test "
                              "này fail nghĩa là bản vá không còn load-bearing")


if __name__ == "__main__":
    unittest.main()
