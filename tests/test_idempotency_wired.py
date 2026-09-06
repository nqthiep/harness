"""T-6.1 nối vào backend LangGraph — `execute_once` cuối cùng có thể sống qua một lần
process chết thật, không chỉ qua một lần thử lại trong CÙNG một lượt gọi.

Bug cụ thể mà nó sửa, nói bằng một câu: LangGraph ghi checkpoint SAU KHI một node xong,
nên một lần chết giữa node `tools` khiến thread khi hồi phục chạy lại CẢ batch — kể cả
một `git_push` đã thành công. `thread_id` và `call_id` đều sống sót qua lần chết đó (call
id nằm trong AIMessage đã checkpoint TRƯỚC khi node tools chạy), nên khoá
`f"{run_id}:{call_id}"` ở đây có nghĩa — trong khi ở vòng lặp classic thì không, vì
`run_id` sinh mới mỗi `atry_run()` (xem docstring của `idempotency.py`).

Runtime này đã có SẴN một lớp bảo vệ trong-bộ-nhớ (`Runtime._idem_for()`, cache theo
thread trên chính đối tượng `Runtime`) — nó chống được "fn() thành công nhưng bước MÃ
HOÁ ngay sau đó lỗi, khiến lần thử lại gọi fn() lần hai", cho MỌI effect class, nhưng
KHÔNG sống qua một process mới (một `Runtime` mới dựng có cache trống). `idempotency_store=`
là lớp thứ hai, tuỳ chọn: khi caller đưa vào một `Store` thật (`SqliteStore`), NÓ thay
thế cache trong-bộ-nhớ cho đúng những lời gọi mà một double-effect không phát hiện được
là nguy hiểm — `write`/`danger` (`not retryable`) — và chỉ những lời gọi đó.

Hai điều được canh riêng ở đây vì chúng là quyết định, không phải hệ quả:

* `read`/`external` KHÔNG đi qua `idempotency_store`. Phát lại một `read` đã ghi sẽ trả về
  nội dung file TRƯỚC lúc chết — với agent code đó là lỗi đúng sai, không phải tính năng.
* Không có store thì `write`/`danger` vẫn được cache trong-bộ-nhớ theo thread như trước
  (không đổi hành vi), nhưng KHÔNG sống qua process mới — `mk()` dưới đây dựng một
  `Runtime` mới mỗi lần gọi, nên test "không có store" tự nhiên mô phỏng đúng điều đó.
"""
import asyncio
import os
import tempfile
import unittest

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage

from harness import tool
from harness.idempotency import idempotency_key
from harness.lg import build_agent
from harness.memory import InMemoryStore, SqliteStore

PUSHED: list = []
READ: list = []


@tool(effect="danger")
def git_push(branch: str) -> str:
    """Đẩy code lên remote — không hoàn tác được."""
    PUSHED.append(branch)
    return f"đã đẩy {branch} (lần {len(PUSHED)})"


@tool(effect="read")
def read_file(path: str) -> str:
    """Đọc một file."""
    READ.append(path)
    return f"nội dung lần {len(READ)}"


def mk(script, *, store=None, tools=(git_push,)):
    return build_agent(model=FakeChat(script=script), tools=list(tools),
                       budget="$5, 40 steps", accepts_tainted=["git_push"],
                       approve=lambda call, ctx: True, idempotency_store=store)


def go(graph, thread):
    return graph.invoke({"messages": [HumanMessage("đẩy đi")], "step": 0},
                        {"configurable": {"thread_id": thread}})


class ChayLaiSauSuCoKhongGayHaiDoiTacDung(unittest.TestCase):
    def setUp(self):
        PUSHED.clear(); READ.clear()

    def test_cung_thread_va_cung_call_id_thi_tool_danger_chi_chay_mot_lan(self):
        """Mô phỏng đúng lần chết: cùng `thread_id`, cùng `call_id`, node `tools` chạy
        lại lần hai — với cùng một store, lần hai phải PHÁT LẠI chứ không đẩy lần nữa."""
        store = InMemoryStore()
        script = [FakeChat.call("git_push", {"branch": "main"}, "c1"), FakeChat.text("ok")]
        go(mk(script, store=store)[0], "task-42")
        self.assertEqual(PUSHED, ["main"])
        # Tiến trình mới, cùng thread, cùng call id, cùng store.
        go(mk(script, store=store)[0], "task-42")
        self.assertEqual(PUSHED, ["main"], "đã đẩy hai lần — double effect")

    def test_ket_qua_phat_lai_dung_bang_ket_qua_lan_dau(self):
        store = InMemoryStore()
        script = [FakeChat.call("git_push", {"branch": "main"}, "c1"), FakeChat.text("ok")]
        out1 = go(mk(script, store=store)[0], "t")
        out2 = go(mk(script, store=store)[0], "t")

        def tool_texts(out):
            from langchain_core.messages import ToolMessage
            return [m.content for m in out["messages"] if isinstance(m, ToolMessage)]

        self.assertEqual(tool_texts(out1), ["đã đẩy main (lần 1)"])
        self.assertEqual(tool_texts(out2), ["đã đẩy main (lần 1)"])

    def test_khong_co_store_thi_hanh_vi_y_nhu_truoc(self):
        script = [FakeChat.call("git_push", {"branch": "main"}, "c1"), FakeChat.text("ok")]
        go(mk(script)[0], "t")
        go(mk(script)[0], "t")
        self.assertEqual(PUSHED, ["main", "main"])

    def test_read_khong_bi_phat_lai_vi_no_phai_thay_hien_trang(self):
        """Quyết định có chủ ý, khác spec gốc của T-6.1: một `read` phát lại trả về nội
        dung TRƯỚC lúc chết — với agent code đó là lỗi, không phải bảo đảm."""
        store = InMemoryStore()
        script = [FakeChat.call("read_file", {"path": "a.py"}, "c1"), FakeChat.text("ok")]
        go(mk(script, store=store, tools=(read_file,))[0], "t")
        go(mk(script, store=store, tools=(read_file,))[0], "t")
        self.assertEqual(READ, ["a.py", "a.py"], "read bị phát lại — sẽ trả nội dung cũ")

    def test_call_id_khac_thi_van_la_mot_lan_day_moi(self):
        """Khoá là `run_id:call_id`. Hai lời gọi khác nhau không được đè lên nhau chỉ vì
        chúng cùng tên tool và cùng tham số."""
        store = InMemoryStore()
        go(mk([FakeChat.call("git_push", {"branch": "main"}, "c1"),
               FakeChat.call("git_push", {"branch": "main"}, "c2"),
               FakeChat.text("ok")], store=store)[0], "t")
        self.assertEqual(len(PUSHED), 2)

    def test_cung_call_id_o_hai_step_khac_nhau_khong_bi_coi_la_phat_lai(self):
        """Bug thật, tìm thấy khi tự review: khoá dùng để chống double-effect từng là
        `f"{run_id}:{call_id}"` — KHÔNG gộp `step`. `call_id` do model/`FakeModel` tự
        đặt, không đảm bảo duy nhất suốt cả thread (đúng cái `FakeModel.tool_call()`'s
        default `call_id="c1"` làm ở khắp nơi trong test suite) — nên một `call_id`
        LẶP LẠI ở step SAU, dù mang tham số hoàn toàn khác (một lời gọi THẬT SỰ mới),
        vẫn bị đọc nhầm thành "phát lại lần gọi ở step trước" và KHÔNG chạy. Script dưới
        đây gọi `git_push` hai lần, cùng `call_id="c1"`, khác `branch` — nếu bug còn đó,
        `PUSHED` chỉ có `["main"]`."""
        store = InMemoryStore()
        script = [FakeChat.call("git_push", {"branch": "main"}, "c1"),
                 FakeChat.call("git_push", {"branch": "other"}, "c1"),
                 FakeChat.text("ok")]
        go(mk(script, store=store)[0], "task-99")
        self.assertEqual(PUSHED, ["main", "other"],
                         "call_id trùng ở step khác bị coi nhầm là phát lại")

    def test_thread_khac_thi_khong_dung_chung_ban_ghi(self):
        store = InMemoryStore()
        script = [FakeChat.call("git_push", {"branch": "main"}, "c1"), FakeChat.text("ok")]
        go(mk(script, store=store)[0], "thread-A")
        go(mk(script, store=store)[0], "thread-B")
        self.assertEqual(len(PUSHED), 2, "bản ghi của thread này trả lời cho thread khác")

    def test_ban_ghi_song_qua_restart_that_voi_SqliteStore(self):
        """Cả điểm của việc này: `InMemoryStore` chứng minh cơ chế, `SqliteStore` chứng
        minh nó sống qua một tiến trình mới."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "idem.db")
            script = [FakeChat.call("git_push", {"branch": "main"}, "c1"),
                      FakeChat.text("ok")]
            s1 = SqliteStore(path)
            go(mk(script, store=s1)[0], "task-42")
            asyncio.run(s1.close())

            s2 = SqliteStore(path)          # "tiến trình mới"
            go(mk(script, store=s2)[0], "task-42")
            asyncio.run(s2.close())
            self.assertEqual(PUSHED, ["main"])


class KhoaIdempotency(unittest.TestCase):
    def test_khoa_co_dang_run_id_hai_cham_call_id(self):
        self.assertEqual(idempotency_key("task-42", "c1"), "task-42:c1")


if __name__ == "__main__":
    unittest.main()
