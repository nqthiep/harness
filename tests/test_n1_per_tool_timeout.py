"""N-1 (design/07-risks-and-open-issues.md): timeout PER-TOOL trên backend LangGraph.

Trước bản vá, `lg/runtime.py::_run_tools` gọi `spec.fn` không có `asyncio.timeout(...)`
nào bọc quanh — khác `dispatch.py::_invoke`, nơi `self._e._l.tool_timeout(spec.timeout_s)`
luôn được áp. Một tool `read` không có timeout riêng (một HTTP call treo, ví dụ) sẽ treo
cả node graph vô thời hạn: chỉ có wall-clock CẤP RUN mới chặn được, và nó chỉ kiểm ở ĐẦU
mỗi bước, không kiểm GIỮA một lời gọi tool đang chạy.

Test dưới đây dùng `asyncio.timeout()` thật của `_run_tools` để CẮT một `asyncio.sleep`
dài giữa chừng — không phải mock đồng hồ — nên nếu bản vá bị revert, test này TREO thay
vì đỏ (dấu hiệu rõ ràng hơn một assertion fail).
"""
import sys
import time
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage, ToolMessage

from harness import tool
from harness.lg import build_agent

CALLS: list = []


@tool(effect="write", timeout_s=0.05)
async def hang_write(x: int) -> str:
    """Treo lâu hơn nhiều so với timeout_s của chính nó."""
    import asyncio
    CALLS.append(time.monotonic())
    await asyncio.sleep(5.0)
    return "không bao giờ tới đây"


@tool(effect="read", timeout_s=0.05)
async def hang_read(x: int) -> str:
    """Cùng kịch bản nhưng `read` — retryable, nên sẽ thử lại rồi mới thất bại."""
    import asyncio
    CALLS.append(time.monotonic())
    await asyncio.sleep(5.0)
    return "không bao giờ tới đây"


class TreoBiCatDungHan(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_write_treo_thi_bi_cat_timeout_khong_treo_ca_node(self):
        chat = FakeChat(script=[FakeChat.call("hang_write", {"x": 1}, "c1"),
                                FakeChat.text("xong")])
        graph, _ = build_agent(model=chat, tools=[hang_write], budget="$5, 20 steps")
        t0 = time.monotonic()
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        elapsed = time.monotonic() - t0
        # Không hoàn toàn hỏng: tool treo trả về lỗi, run vẫn tiếp tục và kết thúc bình
        # thường (đúng như một tool bình thường raise Exception) — không kẹt vô thời hạn.
        self.assertEqual(out.get("stop_reason"), "completed")
        results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(results), 1)
        self.assertIn("timed out", results[0])
        # `write` chỉ có đúng MỘT lần thử — không tự retry một call chưa rõ tác dụng phụ.
        self.assertEqual(len(CALLS), 1)
        # Chứng minh thật: đã CẮT ở ~0.05s, không phải chờ hết 5s mới trả lỗi.
        self.assertLess(elapsed, 2.0, f"mất {elapsed:.2f}s — có vẻ không bị cắt thật")

    def test_read_treo_thi_thu_lai_roi_moi_that_bai_van_bi_cat_moi_lan(self):
        chat = FakeChat(script=[FakeChat.call("hang_read", {"x": 1}, "c1"),
                                FakeChat.text("xong")])
        graph, _ = build_agent(model=chat, tools=[hang_read], budget="$5, 20 steps")
        t0 = time.monotonic()
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        elapsed = time.monotonic() - t0
        results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
        self.assertIn("timed out", results[0])
        # `read` retryable → MAX_ATTEMPTS lần, mỗi lần cũng bị cắt ở ~0.05s, không phải
        # chờ hết 5s mỗi lần.
        self.assertEqual(len(CALLS), 3)
        self.assertLess(elapsed, 5.0, f"mất {elapsed:.2f}s — ít nhất một lần không bị cắt")

    def test_thong_diep_phan_biet_het_wall_clock_run_va_het_timeout_rieng_cua_tool(self):
        """Cùng phân nhánh thông điệp `dispatch.py::_invoke` đã có — timeout do NGÂN
        SÁCH CẢ RUN hết hạn phải nói khác với timeout do CHÍNH tool đó chậm."""
        @tool(effect="write", timeout_s=999)
        async def hang_run_out_of_wall_clock(x: int) -> str:
            """timeout_s riêng rất lớn — chỉ ngân sách wall-clock của cả run cắt được."""
            import asyncio
            await asyncio.sleep(5.0)
            return "không bao giờ tới đây"

        chat = FakeChat(script=[FakeChat.call("hang_run_out_of_wall_clock", {"x": 1}, "c1"),
                                FakeChat.text("xong")])
        graph, _ = build_agent(model=chat, tools=[hang_run_out_of_wall_clock],
                               budget="$5, 20 steps, 0.05s")
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
        self.assertIn("run wall-clock budget reached", results[0])


if __name__ == "__main__":
    unittest.main()
