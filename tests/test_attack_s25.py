"""S-25: (a) args do model kiểm soát hiển thị thẳng cho người duyệt, không escape/truncate
— UI injection qua terminal/Slack. (b) không có trần số lần ASK trong một run — approval
fatigue là một kênh model điều khiển được.

Kiểm trên code hôm nay: `PolicyEngine.resolve()` gọi thẳng `approve(call, ctx)` với
`call.arguments` — dict thô từ model, không qua escape/truncate nào (`secrets.py`'s
`redact()` chỉ xử lý `Secret`, không xử lý control character). Và không có cơ chế đếm/trần
nào cho số lần `ASK` trong một run trước bản vá này.
"""
import unittest

from harness import Agent, safe_for_display, tool
from harness.models.fake import FakeModel

RAN: list = []


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    RAN.append(x)
    return "gone"


class SafeForDisplay(unittest.TestCase):
    def test_control_char_khong_thoat_ra_nguyen_ven(self):
        s = safe_for_display("bình thường\x1b[2Kdòng bị xoá")
        self.assertNotIn("\x1b", s, "ANSI escape byte thật vẫn còn trong chuỗi hiển thị")
        self.assertIn("\\x1b", s, "escape sequence phải hiện dạng chữ, không thực thi")

    def test_gia_tri_dai_thay_bang_do_dai_va_digest(self):
        long_val = "x" * 500
        s = safe_for_display(long_val, max_len=200)
        self.assertNotIn("x" * 200, s, "giá trị dài vẫn hiện nguyên nội dung")
        self.assertIn("500", s)
        self.assertIn("digest=", s)

    def test_gia_tri_ngan_binh_thuong_khong_bi_dong_gi(self):
        self.assertEqual(safe_for_display("hello"), "hello")

    def test_gia_khong_phai_chuoi_duoc_json_hoa(self):
        s = safe_for_display({"path": "/tmp/x", "amount": 10})
        self.assertIn("/tmp/x", s)
        self.assertIn("10", s)


class TranSoLanAskCoDien(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def _script(self, n_calls: int):
        calls = [FakeModel.tool_call("wipe", {"x": i}, call_id=f"c{i}") for i in range(n_calls)]
        return calls + [FakeModel.text("xong")]

    def test_qua_tran_thi_bi_chan_tu_do_tro_di(self):
        agent = Agent(name="A", job="xoá liên tục", model="claude-opus-5",
                      provider=FakeModel(self._script(6)), tools=[wipe], budget="$5",
                      approve=lambda call, ctx: True, max_asks_per_run=3)
        agent.try_run("đi")
        self.assertEqual(len(RAN), 3,
                         f"chạy {len(RAN)} lần dù trần là 3 — approval fatigue cap, S-25(b)")

    def test_duoi_tran_thi_khong_chan_gi(self):
        agent = Agent(name="A", job="xoá vài lần", model="claude-opus-5",
                      provider=FakeModel(self._script(2)), tools=[wipe], budget="$5",
                      approve=lambda call, ctx: True, max_asks_per_run=5)
        agent.try_run("đi")
        self.assertEqual(len(RAN), 2)


class TranSoLanAskGraph(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def test_qua_tran_thi_bi_chan_tu_do_tro_di(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        script = [FakeChat.call("wipe", {"x": i}, cid=f"c{i}") for i in range(6)]
        script.append(FakeChat.text("xong"))
        graph, rt = build_agent(model=FakeChat(script=script), tools=[wipe], budget="$5",
                                checkpointer=MemorySaver(), approve=lambda call, ctx: True,
                                max_asks_per_run=3)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s25"}})
        self.assertEqual(len(RAN), 3,
                         f"chạy {len(RAN)} lần dù trần là 3 — approval fatigue cap, S-25(b)")

    def test_tran_duoc_tinh_lai_moi_luot_moi(self):
        """Lượt mới (human message mới) phải được cấp lại hạn mức mới, không cộng dồn
        mãi mãi qua các lượt của cùng một thread."""
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        script = ([FakeChat.call("wipe", {"x": i}, cid=f"a{i}") for i in range(3)]
                 + [FakeChat.text("xong")]
                 + [FakeChat.call("wipe", {"x": 100 + i}, cid=f"b{i}") for i in range(3)]
                 + [FakeChat.text("xong")])
        graph, rt = build_agent(model=FakeChat(script=script), tools=[wipe], budget="$5",
                                checkpointer=MemorySaver(), approve=lambda call, ctx: True,
                                max_asks_per_run=3)
        cfg = {"configurable": {"thread_id": "t-s25-reset"}}
        graph.invoke({"messages": [HumanMessage("đi 1")], "step": 0}, cfg)
        n_sau_luot_1 = len(RAN)
        graph.invoke({"messages": [HumanMessage("đi 2")]}, cfg)
        self.assertEqual(n_sau_luot_1, 3, "lượt 1 phải chạy đủ 3 (đúng trần)")
        self.assertEqual(len(RAN), 6,
                         f"chạy tổng {len(RAN)} sau lượt 2 — lượt 2 không được cấp lại "
                         f"hạn mức mới, trần cũ vẫn còn hiệu lực từ lượt 1")


class MutationS25b(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def test_khong_co_ban_va_thi_chay_het_ca_6(self):
        """Mô phỏng CHÍNH XÁC hành vi trước bản vá: không đếm, không chặn — mọi ASK đều
        đi thẳng qua `resolve()`."""
        agent = Agent(name="A", job="xoá", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {"x": i}, call_id=f"c{i}")
                                         for i in range(6)] + [FakeModel.text("xong")]),
                      tools=[wipe], budget="$5", approve=lambda call, ctx: True,
                      max_asks_per_run=3)
        # Khôi phục hành vi cũ: resolve() luôn được gọi thẳng, không có nhánh ask-cap chen
        # vào trước nó — mô phỏng bằng cách set trần cực lớn (tương đương "không có trần").
        object.__setattr__(agent, "max_asks_per_run", 10_000_000)
        agent.try_run("đi")
        self.assertEqual(len(RAN), 6,
                         "mutation (trần vô hiệu) phải cho chạy đủ 6 lần — nếu test này "
                         "fail nghĩa là cơ chế đếm/chặn không còn load-bearing")


if __name__ == "__main__":
    unittest.main()
