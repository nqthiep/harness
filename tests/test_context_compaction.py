"""Nén context THẬT — `context/window.py::compact`, và nó chạy trên cả hai backend.

Suốt hai milestone, `manage()` trả về `"compact_needed"` và không ai làm gì với nó ngoài
phát một sự kiện. Hệ quả cụ thể: một phiên dài xoá nội dung kết quả tool cho tới khi hết
chỗ để xoá, rồi đâm thẳng vào giới hạn context của provider và bị từ chối request — thất
bại ở đúng chỗ mà tính năng này tồn tại để tránh.

Nhóm cuối (`BackendGraphVaChuyenRuaTaint`) là nhóm quan trọng nhất trên backend graph: nén
mà làm rớt nhãn thì chính nó là đường rửa taint (S-19).
"""
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage

from harness import Agent, tool
from harness.context.window import CLEARED, KEEP_RECENT_STEPS, compact, manage
from harness.lg import build_agent
from harness.lg.runtime import _context_chars
from harness.models.fake import FakeModel
from harness.observe.events import EventKind
from harness.policy.label import Integrity
from harness.result import StopReason


def conversation(n: int, *, body: str = "x") -> list:
    msgs: list = [{"role": "user", "content": "nhiệm vụ gốc"}]
    for i in range(n):
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": f"c{i}", "name": "t",
                                  "input": {"i": i, "body": body}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": f"c{i}",
                                  "content": "kết quả"}]})
    return msgs


class BoNguyenBuoc(unittest.TestCase):
    def test_giu_nhiem_vu_goc_va_may_buoc_cuoi(self):
        out, dropped = compact(conversation(10))
        self.assertEqual(out[0]["content"], "nhiệm vụ gốc")
        self.assertEqual(len(out), 1 + 2 * KEEP_RECENT_STEPS)
        self.assertEqual(dropped, 20 - 2 * KEEP_RECENT_STEPS)

    def test_khong_bo_no_le_mot_tool_result(self):
        """I-3: một `tool_result` không có `tool_use` tương ứng là vi phạm giao thức, và
        provider từ chối cả cuộc hội thoại nếu nó được replay."""
        out, _ = compact(conversation(10))
        uses = {b["id"] for m in out if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_use"}
        results = {b["tool_use_id"] for m in out if isinstance(m["content"], list)
                  for b in m["content"] if b.get("type") == "tool_result"}
        self.assertEqual(results - uses, set(), "có tool_result mồ côi sau khi nén")

    def test_it_buoc_thi_khong_bo_gi(self):
        msgs = conversation(KEEP_RECENT_STEPS)
        out, dropped = compact(msgs)
        self.assertEqual(dropped, 0)
        self.assertEqual(out, msgs)

    def test_hoi_thoai_mot_message_thi_khong_bo_gi(self):
        out, dropped = compact([{"role": "user", "content": "chỉ có thế"}])
        self.assertEqual(dropped, 0)
        self.assertEqual(len(out), 1)


class ThangBacXoaRoiNen(unittest.TestCase):
    """`none` → `edited` → `compacted` → `compact_needed`."""

    def test_duoi_nguong_thi_khong_lam_gi(self):
        _, action = manage(conversation(10), used_tokens=10, context_window=100)
        self.assertEqual(action, "none")

    def test_giua_hai_nguong_thi_xoa_noi_dung(self):
        out, action = manage(conversation(10), used_tokens=70, context_window=100)
        self.assertEqual(action, "edited")
        self.assertEqual(len(out), len(conversation(10)), "xoá nội dung mà lại bỏ message")
        self.assertIn(CLEARED, str(out))

    def test_tren_nguong_nen_thi_bo_han_buoc_cu(self):
        out, action = manage(conversation(10), used_tokens=90, context_window=100)
        self.assertEqual(action, "compacted")
        self.assertLess(len(out), len(conversation(10)))

    def test_nen_KHONG_cho_toi_khi_het_cho_xoa(self):
        """Lỗi của bản đầu tiên, giữ lại trong tầm nhìn: mỗi bước lại làm đúng một kết
        quả tool cũ đi, nên nhánh xoá LUÔN có việc — và nhánh nén sẽ không bao giờ chạy
        nếu điều kiện của nó là 'xoá đã hết việc'."""
        msgs = conversation(10)                       # chưa có gì bị xoá nội dung
        _, action = manage(msgs, used_tokens=90, context_window=100)
        self.assertEqual(action, "compacted",
                         "nén bị chặn sau nhánh xoá — sẽ không bao giờ chạy trong thực tế")

    def test_khong_con_gi_de_bo_thi_noi_ra_chu_khong_im_lang(self):
        one = [{"role": "user", "content": "một message khổng lồ"}]
        _, action = manage(one, used_tokens=95, context_window=100)
        self.assertEqual(action, "compact_needed")


class VongLapClassic(unittest.TestCase):
    def test_phien_dai_ket_thuc_thay_vi_phinh_vo_han(self):
        @tool(effect="write")
        def write_source(path: str, body: str) -> str:
            """Ghi một file."""
            return "ok"

        rows = []

        class Collector:
            def emit(self, e):
                if e.kind is EventKind.CONTEXT_MANAGED:
                    rows.append(dict(e.data))
            def close(self): ...

        script = [FakeModel.tool_call("write_source",
                                      {"path": f"f{i}.py", "body": "B" * 40_000},
                                      call_id=f"c{i}") for i in range(40)]
        r = Agent(name="T", job="j", tools=[write_source], model="claude-haiku-4-5",
                 budget="$500, 60 steps", exporters=[Collector()],
                 provider=FakeModel(script + [FakeModel.text("xong")])).try_run("đi")

        self.assertIs(r.stop_reason, StopReason.COMPLETED)
        strategies = [x["strategy"] for x in rows]
        self.assertIn("edited", strategies)
        self.assertIn("compacted", strategies, "chỉ xoá nội dung, không bao giờ nén")
        # Điểm của cả việc này: số message KHÔNG lớn dần theo số bước.
        self.assertLess(len(r.messages), 2 * len(script))

    def test_khong_con_gi_de_bo_thi_dung_run_kem_ly_do_doc_duoc(self):
        @tool(effect="read")
        def look(i: int) -> str:
            """Nhìn."""
            return "K" * 400_000

        r = Agent(name="T", job="j", tools=[look], model="claude-haiku-4-5",
                 budget="$500, 60 steps",
                 provider=FakeModel([FakeModel.tool_call("look", {"i": 0})] * 5)
                 ).try_run("K" * 3_000_000)
        self.assertIs(r.stop_reason, StopReason.ERROR)
        # Câu này phải nói cho NGƯỜI biết phải làm gì, không phải một mã lỗi trơ.
        self.assertIn("nothing left to clear or drop", r.detail)
        self.assertIn("bigger window", r.detail)


class BackendGraphVaChuyenRuaTaint(unittest.TestCase):
    def _run(self):
        @tool(effect="external")
        def fetch(url: str, body: str) -> str:
            """Đọc một trang web — kết quả là UNTRUSTED."""
            return "kết quả ngắn"

        script = [FakeChat.call("fetch", {"url": f"http://x/{i}", "body": "B" * 40_000},
                                f"c{i}") for i in range(40)]
        graph, rt = build_agent(model=FakeChat(script=script + [FakeChat.text("xong")]),
                                tools=[fetch], model_name="claude-haiku-4-5",
                                budget="$500, 60 steps", allowed_hosts=None)
        return rt, graph.invoke({"messages": [HumanMessage("đi")], "step": 0})

    def test_context_duoc_do_ke_ca_THAM_SO_cua_tool_call(self):
        """Một `AIMessage` chỉ mang tool_calls có `content == ""`: tham số nằm ở
        `.tool_calls`. Phép đo cũ cộng đúng `len(str(m.content))`, nên nó bỏ sót ĐÚNG cái
        phần không bao giờ được xoá — và việc nén không bao giờ chạy."""
        from langchain_core.messages import AIMessage
        m = AIMessage(content="", tool_calls=[
            {"name": "edit_source", "args": {"body": "B" * 5_000}, "id": "c1"}])
        self.assertEqual(len(str(m.content)), 0)
        self.assertGreater(_context_chars([m]), 5_000)

    def test_nen_chay_va_so_message_bi_chan_tren(self):
        _, out = self._run()
        self.assertEqual(out.get("stop_reason"), "completed")
        self.assertLess(len(out["messages"]), 40)

    def test_nhiem_vu_goc_khong_bao_gio_bi_bo(self):
        _, out = self._run()
        self.assertIsInstance(out["messages"][0], HumanMessage)
        self.assertEqual(out["messages"][0].content, "đi")

    def test_nen_KHONG_rua_taint(self):
        """S-19, và là lý do bản nén ở backend này không xoá trắng. Nhãn hiệu dụng ở đây
        được TÍNH LẠI từ các message còn trong context (L-3), nên bỏ một `ToolMessage`
        UNTRUSTED khỏi state chính là hạ nhãn của cả run — bằng đúng thao tác mà việc dọn
        context tự gọi là dọn dẹp. Bia mộ mang `join` của mọi nhãn đã bỏ là thứ chặn nó."""
        rt, out = self._run()
        self.assertTrue(out.get("tainted"), "run mất trạng thái tainted sau khi nén")
        self.assertIs(rt._effective_label(out).integrity, Integrity.UNTRUSTED,
                      "nén đã rửa sạch taint — đúng lỗ hổng S-19")

    def test_co_dung_mot_bia_mo(self):
        _, out = self._run()
        tombs = [m for m in out["messages"]
                 if getattr(m, "id", "") == "harness-compaction-tombstone"]
        self.assertEqual(len(tombs), 1)
        self.assertEqual(tombs[0].content, CLEARED)


if __name__ == "__main__":
    unittest.main()
