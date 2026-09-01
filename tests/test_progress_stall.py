"""Stall detector cơ học — `src/harness/progress.py`, vòng lặp classic (`run.py`).

Vì sao cần nó, nói bằng một con số: một agent lặp lại đúng một lời gọi tool cho đến khi
chạm trần 300 bước sẽ trả tiền cho 300 lượt gọi model để không đi tới đâu. Trần ngân sách
vẫn bắt được — nhưng bắt muộn nhất có thể, và `stop_reason` nó trả về (`step_limit`) mô tả
sai chuyện đã xảy ra.

Nhóm test ở đây tương ứng đúng ba điều `progress.py` tự hứa, trên vòng lặp classic:

1. Lặp lại thì dừng, và dừng bằng `StopReason.STALLED` chứ không phải một `ERROR` chung.
2. Một chữ ký MỚI đưa bộ đếm về 0 — vòng sửa-code-chạy-test bình thường không bị giết oan.
3. Không đụng tới các trần cũ: step limit và budget vẫn dừng đúng như trước
   (`tests/test_redteam.py::RT06`, `tests/test_walkthrough.py::rt06` đã sửa cho điều này).

Cố ý không kiểm tra ở đây: đối chiếu hai backend (graph backend chưa nối `ProgressLedger`
— task theo dõi riêng, R-17) và checkpoint-qua-lượt trên graph backend.
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
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
        self.assertEqual(r.steps, STALL_AFTER)
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


if __name__ == "__main__":
    unittest.main()
