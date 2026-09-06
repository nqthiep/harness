"""Stall detector cơ học — `src/harness/progress.py`, trên CẢ HAI backend.

Vì sao cần nó, nói bằng một con số: một agent lặp lại đúng một lời gọi tool cho đến khi
chạm trần 300 bước sẽ trả tiền cho 300 lượt gọi model để không đi tới đâu. Trần ngân sách
vẫn bắt được — nhưng bắt muộn nhất có thể, và `stop_reason` nó trả về (`step_limit`) mô tả
sai chuyện đã xảy ra.

Nhóm test ở đây tương ứng đúng bốn điều `progress.py` tự hứa:

1. Lặp lại thì dừng, và dừng bằng `StopReason.STALLED` chứ không phải một `ERROR` chung.
2. Một chữ ký MỚI đưa bộ đếm về 0 — vòng sửa-code-chạy-test bình thường không bị giết oan.
3. Hai backend hành xử giống nhau (R-17: hai bản cài của một luật luôn trôi khỏi nhau).
4. Không đụng tới các trần cũ: step limit và budget vẫn dừng đúng như trước
   (`tests/test_redteam.py::RT06`, `tests/test_walkthrough.py::rt06`,
   `tests/test_lg.py`, `tests/test_parity.py` đã sửa cho điều này).
"""
import unittest

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage

from harness import Agent, tool
from harness.lg import build_agent
from harness.models.fake import FakeModel
from harness.observe.events import EventKind
from harness.progress import STALL_AFTER, ProgressLedger, arguments_of, signature
from harness.result import StopReason


@tool(effect="read")
def look(path: str) -> str:
    """Đọc một file."""
    return "nội dung y hệt mỗi lần"


@tool(effect="write")
def edit(path: str, body: str) -> str:
    """Sửa một file."""
    return "đã ghi"


class SoCoHoc(unittest.TestCase):
    """Luật, tính trực tiếp trên `ProgressLedger` — không qua model, không qua backend."""

    def call(self, name, **args):
        return {"name": name, "input": args}

    def test_lap_lai_dung_nguong_thi_bao_dung(self):
        p = ProgressLedger(stall_after=3)
        self.assertIsNone(p.observe([self.call("look", path="a.py")]))
        self.assertIsNone(p.observe([self.call("look", path="a.py")]))
        self.assertIsNone(p.observe([self.call("look", path="a.py")]))
        reason = p.observe([self.call("look", path="a.py")])
        self.assertIsNotNone(reason)
        self.assertIn("3 bước", str(reason))

    def test_mot_chu_ky_moi_dua_bo_dem_ve_khong(self):
        """Vòng lặp coding thật: `edit` đổi nội dung mỗi vòng, `run_tests` lặp y hệt.
        Đây là kịch bản mà một detector viết ẩu sẽ giết oan."""
        p = ProgressLedger(stall_after=3)
        for i in range(20):
            r = p.observe([self.call("edit", path="a.py", body=f"v{i}"),
                           self.call("look", path="a.py")])
            self.assertIsNone(r, f"giết oan ở vòng {i}")
        self.assertEqual(p.stalled_steps, 0)


class NeTranhBangThamSoLa(unittest.TestCase):
    """G-6, đã sửa: một khoá KHÔNG khai (`nonce`) mà `look` không nhận trong chữ ký —
    trước bản vá, chữ ký hash trên MỌI khoá không bắt đầu bằng `_`, nên mỗi lời gọi khác
    nhau dù cùng ý định, và bộ đếm không bao giờ tăng. `dispatch.py` cũng bỏ khoá không
    khai trước khi gọi `spec.fn`, nên MỌI lời gọi kiểu này thật ra đều lỗi — đúng trạng
    thái bế tắc rõ nhất lại đọc thành "đang tiến triển"."""

    SCHEMA = {"look": frozenset({"path"})}   # đúng `input_schema["properties"]` của `look`

    def call(self, name, **args):
        return {"name": name, "input": args}

    def test_khong_co_schema_of_bi_ne_vinh_vien(self):
        p = ProgressLedger(stall_after=6)
        for i in range(50):
            r = p.observe([self.call("look", path="a.py", nonce=i)])   # KHÔNG có schema_of
        self.assertIsNone(r, "hành vi CŨ: không bao giờ dừng — dùng để đối chứng")
        self.assertEqual(p.stalled_steps, 0)

    def test_co_schema_of_thi_bi_bat_dung_luc(self):
        p = ProgressLedger(stall_after=6)
        r = None
        for i in range(50):
            r = p.observe([self.call("look", path="a.py", nonce=i)], self.SCHEMA)
            if r is not None:
                break
        self.assertIsNotNone(r, "50 bước cùng path, chỉ khác nonce lạ — vẫn phải bị bắt")
        self.assertEqual(p.stalled_steps, 6)

    def test_signature_truc_tiep_bo_khoa_khong_khai(self):
        s1 = signature("look", {"path": "a.py", "nonce": 1}, declared_keys=self.SCHEMA["look"])
        s2 = signature("look", {"path": "a.py", "nonce": 2}, declared_keys=self.SCHEMA["look"])
        self.assertEqual(s1, s2, "khoá không khai vẫn đổi được chữ ký")

    def test_khong_khai_declared_keys_thi_giu_hanh_vi_cu(self):
        """`declared_keys=None` (tool không xác định được) vẫn dùng mọi khoá — bỏ sót còn
        hơn giết nhầm, đúng hướng an toàn `MAX_TRACKED` đã chọn."""
        s1 = signature("look", {"path": "a.py", "nonce": 1})
        s2 = signature("look", {"path": "a.py", "nonce": 2})
        self.assertNotEqual(s1, s2)

    def test_buoc_khong_goi_tool_nao_khong_duoc_tinh(self):
        p = ProgressLedger(stall_after=2)
        p.observe([self.call("look", path="a.py")])
        for _ in range(5):
            self.assertIsNone(p.observe([]))
        self.assertEqual(p.stalled_steps, 0)

    def test_tham_so_gach_duoi_khong_tao_ra_tien_trien_gia(self):
        """`_x` bị lọc trước khi tool chạy (`dispatch.py`), nên hai lời gọi chỉ khác nhau
        ở đó LÀ một lời gọi. Nếu không lọc, ai cũng có cách trông bận mà đứng yên."""
        self.assertEqual(signature("look", {"path": "a.py", "_n": 1}),
                         signature("look", {"path": "a.py", "_n": 2}))
        p = ProgressLedger(stall_after=2)
        r = None
        for i in range(3):
            r = p.observe([self.call("look", path="a.py", _n=i)])
        self.assertIsNotNone(r)

    def test_chu_ky_khong_giu_nguyen_van_tham_so(self):
        """Một `edit` có thể mang cả nội dung file; state đã checkpoint không phải chỗ
        để giữ bản sao thứ hai của nó."""
        s = signature("edit", {"path": "a.py", "body": "MẬT KHẨU LÀ hunter2"})
        self.assertEqual(len(s), 16)
        self.assertNotIn("hunter2", s)

    def test_hai_backend_goi_ten_tham_so_khac_nhau_van_ra_mot_chu_ky(self):
        """Khối `tool_use` của Anthropic dùng `input`; `ToolCall` của LangChain dùng
        `args`. Nếu hai chỗ ra hai chữ ký thì detector chỉ hoạt động trên một backend."""
        self.assertEqual(arguments_of({"name": "look", "input": {"path": "a.py"}}),
                         arguments_of({"name": "look", "args": {"path": "a.py"}}))


class VongLapClassic(unittest.TestCase):
    def test_lap_lai_dung_o_stall_after_chu_khong_dot_het_ngan_sach(self):
        agent = Agent(name="Loop", job="j", tools=[look], budget="$5, 100 steps",
                      provider=FakeModel([FakeModel.tool_call("look", {"path": "a.py"})] * 50))
        r = agent.try_run("go")
        self.assertIs(r.stop_reason, StopReason.STALLED)
        self.assertFalse(r.ok)
        # STALL_AFTER + 1, và con số này chính là thứ đã chặn ADR-118 suốt một vòng.
        # `ProgressLedger` đếm số lần LẶP LẠI, nên lượt gọi model đầu tiên chưa có gì để
        # lặp: một run bị chặn ở lần lặp thứ STALL_AFTER đã gọi model STALL_AFTER + 1
        # lần. Kỳ vọng cũ (`== STALL_AFTER`) chỉ đúng với con trỏ `while` của vòng lặp,
        # tức đúng vì `Result.steps` đang ở SAI đơn vị. Đo được: cả hai backend đều phát
        # 7 sự kiện `model.request` cho kịch bản này, và giờ cả hai đều báo steps=7.
        self.assertEqual(r.steps, STALL_AFTER + 1)
        # Lý do phải đọc được, không phải một mã lỗi trơ.
        self.assertIn("không có lời gọi tool nào mới", r.detail)

    def test_su_kien_progress_stalled_duoc_phat(self):
        seen = []

        class Collector:
            def emit(self, e): seen.append(e.kind)
            def close(self): pass

        Agent(name="Loop", job="j", tools=[look], budget="$5, 100 steps",
              exporters=[Collector()],
              provider=FakeModel([FakeModel.tool_call("look", {"path": "a.py"})] * 50)
              ).try_run("go")
        self.assertIn(EventKind.PROGRESS_STALLED, seen)

    def test_cong_viec_that_khong_bi_giet_oan(self):
        script = []
        for i in range(12):
            script.append(FakeModel.tool_call("edit", {"path": "a.py", "body": f"v{i}"}))
            script.append(FakeModel.tool_call("look", {"path": "a.py"}))
        script.append(FakeModel.text("xong"))
        r = Agent(name="Work", job="j", tools=[look, edit], budget="$5, 100 steps",
                  provider=FakeModel(script)).try_run("go")
        self.assertIs(r.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r.text, "xong")


class NeTranhQuaAgentThat(unittest.TestCase):
    """G-6 nối dây thật qua `Agent`/`run.py`, không chỉ `ProgressLedger` đứng riêng:
    mỗi bước gọi `look` với một `nonce` lạ — `look(path)` không khai `nonce`, nên
    `dispatch.py` tự bỏ nó trước khi gọi `spec.fn`, và lời gọi vẫn THÀNH CÔNG (giống hệt
    lời gọi không có `nonce`) — nhưng trước bản vá, chữ ký khác nhau mỗi lần nên không
    bao giờ bị bắt bế tắc."""

    def test_them_mot_tham_so_la_khong_ne_duoc_bo_dem(self):
        script = [FakeModel.tool_call("look", {"path": "a.py", "nonce": i})
                 for i in range(50)]
        r = Agent(name="Loop", job="j", tools=[look], budget="$5, 100 steps",
                  provider=FakeModel(script)).try_run("go")
        self.assertIs(r.stop_reason, StopReason.STALLED,
                     "G-6: một khoá không khai vẫn né được bộ đếm")
        # STALL_AFTER + 1, cùng lý do đã ghi ở test đầu file: `Result.steps` giờ đếm
        # LƯỢT GỌI MODEL (ADR-118), còn `ProgressLedger` đếm số lần LẶP LẠI — lượt gọi
        # đầu tiên chưa có gì để lặp. Kỳ vọng cũ đúng với con trỏ `while`, tức đúng vì
        # `Result.steps` đang ở sai đơn vị.
        self.assertEqual(r.steps, STALL_AFTER + 1)


class BackendGraph(unittest.TestCase):
    def test_lap_lai_dung_tren_graph(self):
        chat = FakeChat(script=[FakeChat.call("look", {"path": "a.py"}, f"c{i}")
                                for i in range(50)])
        graph, _ = build_agent(model=chat, tools=[look], budget="$5, 100 steps")
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        self.assertEqual(out.get("stop_reason"), StopReason.STALLED.value)
        self.assertIn("không có lời gọi tool nào mới", out.get("detail", ""))

    def test_cong_viec_that_khong_bi_giet_oan_tren_graph(self):
        script = []
        for i in range(12):
            script.append(FakeChat.call("edit", {"path": "a.py", "body": f"v{i}"}, f"e{i}"))
            script.append(FakeChat.call("look", {"path": "a.py"}, f"l{i}"))
        script.append(FakeChat.text("xong"))
        graph, _ = build_agent(model=FakeChat(script=script), tools=[look, edit],
                               budget="$5, 100 steps")
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        self.assertEqual(out.get("stop_reason"), "completed")

    def test_bo_dem_nam_trong_state_da_checkpoint_chu_khong_tren_runtime(self):
        """IDL-47: một `Runtime` phục vụ mọi thread. Bộ đếm sống trên nó sẽ trộn tiến độ
        của cuộc hội thoại này vào cuộc hội thoại khác."""
        from harness.lg.state import AgentState
        self.assertIn("seen_calls", AgentState.__annotations__)
        self.assertIn("stalled_steps", AgentState.__annotations__)

        chat = FakeChat(script=[FakeChat.call("look", {"path": "a.py"}, f"c{i}")
                                for i in range(50)])
        graph, rt = build_agent(model=chat, tools=[look], budget="$5, 100 steps")
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        self.assertGreaterEqual(out.get("stalled_steps", 0), STALL_AFTER)
        self.assertFalse(hasattr(rt, "_progress"))


class BoDemResetMoiLuot(unittest.TestCase):
    """Bug thật, tìm thấy khi tự review lại lượt vá stall detector: `asks` được reset
    mỗi lượt hội thoại mới (`_is_new_turn`, S-25(b)) nhưng `stalled_steps`/`seen_calls`
    thì KHÔNG — đọc thẳng từ state đã checkpoint không qua ranh giới lượt nào cả. Một
    `Chat` nhiều lượt trên cùng một `thread_id` để lại `stalled_steps` gần chạm
    `STALL_AFTER` (hoặc `seen_calls` đầy chữ ký của lượt đó) khi lượt trước kết thúc, và
    lượt SAU thừa hưởng nguyên con số đó — một bước "kiểm tra lại" bình thường ở đầu lượt
    mới có thể trùng đúng chữ ký của lượt trước và bị dừng oan gần như ngay lập tức."""

    def test_luot_moi_khong_thua_huong_bo_dem_cua_luot_truoc(self):
        from langgraph.checkpoint.memory import MemorySaver

        # Lượt 1: 6 lời gọi `look(a.py)` giống hệt nhau — đưa stalled_steps lên 5, VẪN
        # DƯỚI STALL_AFTER(6) — rồi kết thúc lượt bằng một câu trả lời chữ.
        turn1 = [FakeChat.call("look", {"path": "a.py"}, f"a{i}") for i in range(6)]
        turn1.append(FakeChat.text("tạm nghỉ"))
        # Lượt 2: MỞ ĐẦU bằng đúng một lời gọi trùng chữ ký lượt 1 (một bước "xem lại"
        # hoàn toàn bình thường) — nếu bộ đếm không reset, bước này tự nó đã chạm trần.
        # Rồi làm việc THẬT (edit một file khác) và kết thúc bình thường.
        turn2 = [FakeChat.call("look", {"path": "a.py"}, "b0"),
                FakeChat.call("edit", {"path": "b.py", "body": "x"}, "b1"),
                FakeChat.text("xong")]

        graph, _ = build_agent(model=FakeChat(script=turn1 + turn2), tools=[look, edit],
                               budget="$5, 100 steps", checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-stall-reset"}}
        out1 = graph.invoke({"messages": [HumanMessage("đi 1")], "step": 0}, cfg)
        self.assertEqual(out1.get("stop_reason"), "completed",
                         "lượt 1 phải hoàn thành bình thường")
        self.assertEqual(out1.get("stalled_steps"), 5)

        out2 = graph.invoke({"messages": [HumanMessage("đi 2")]}, cfg)
        self.assertEqual(out2.get("stop_reason"), "completed",
                         "lượt 2 bị dừng STALLED oan ngay bước đầu — bộ đếm của lượt 1 "
                         "tràn sang lượt 2")


class HaiBackendGiongNhau(unittest.TestCase):
    def test_cung_mot_kich_ban_thi_cung_mot_stop_reason_va_cung_mot_cau(self):
        script_loop = [FakeModel.tool_call("look", {"path": "a.py"})] * 50
        r = Agent(name="Loop", job="j", tools=[look], budget="$5, 100 steps",
                  provider=FakeModel(script_loop)).try_run("go")
        chat = FakeChat(script=[FakeChat.call("look", {"path": "a.py"}, f"c{i}")
                                for i in range(50)])
        graph, _ = build_agent(model=chat, tools=[look], budget="$5, 100 steps")
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})

        self.assertEqual(r.stop_reason.value, out.get("stop_reason"))
        self.assertEqual(r.detail, out.get("detail"))


if __name__ == "__main__":
    unittest.main()
