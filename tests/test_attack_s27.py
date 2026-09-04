"""S-27: trong MỘT lượt model, một tool `read` nhạy cảm (nâng confidentiality) và một tool
`write` (sink PUBLIC) cùng được đề xuất — quyết định của `write` phải dùng nhãn SAU KHI
tool `read` chạy, không phải nhãn snapshot từ TRƯỚC KHI batch bắt đầu.

Kịch bản gốc của review-security.md S-27 dùng `fetch_url` (external, gây taint) +
`run_shell` (danger) — nhưng construction-time Poka-Yoke `_check_tool_set` (F9.1) đã chặn
đúng combo external+danger TRƯỚC KHI agent dựng được, trừ khi operator tự khai
`accepts_tainted` cho tool danger đó — và một khi đã khai, `check_flow` không còn gì để
chặn nữa (đúng route "operator nói nó an toàn"), nên kịch bản gốc không dựng lại được trên
code hôm nay.

Nhánh CÒN SỐNG của S-27: nhánh CONFIDENTIALITY của `check_flow` (SECRET + sink PUBLIC),
không bị `_check_tool_set` chạm tới (nó chỉ kiểm combo external+danger). `read_payroll`
(effect=read, `sensitive=True` — S-3 nguồn thứ hai) nâng confidentiality lên SECRET;
`send_report` (effect=write, sink PUBLIC theo effect) là tool bị chặn. Cả hai trong CÙNG
một lượt: `read_payroll` (parallel-safe) chạy TRƯỚC `send_report` (không parallel-safe, nên
serial) — nhưng quyết định ALLOW của `send_report` (write mặc định ALLOW, không cần
approve) đã bị khoá dựa trên nhãn PUBLIC từ TRƯỚC KHI batch chạy.

Kiểm cả hai backend — cơ chế khác nhau, cùng một lỗ hổng (xem `dispatch.py::_run_tools` và
`lg/runtime.py::_regate` cho chi tiết từng nơi).
"""
import unittest

from harness import Agent, tool
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.result import Usage

RAN: list = []


@tool(effect="read")
def read_payroll(who: str) -> str:
    """Đọc bảng lương nội bộ."""
    RAN.append(("read_payroll", who))
    return "5,000 USD"


@tool(effect="write")
def send_report(to: str) -> str:
    """Gửi báo cáo ra ngoài — sink PUBLIC theo effect."""
    RAN.append(("send_report", to))
    return "đã gửi"


def _multi_call() -> ModelResponse:
    return ModelResponse(
        ({"type": "tool_use", "id": "c1", "name": "read_payroll", "input": {"who": "A"}},
         {"type": "tool_use", "id": "c2", "name": "send_report", "input": {"to": "x@y.com"}}),
        "tool_use", Usage(100, 15), "fake")


class ChayThatQuaAgent(unittest.TestCase):
    """Backend cổ điển (`dispatch.py::_run_tools`)."""

    def setUp(self):
        RAN.clear()

    def test_send_report_bi_chan_du_da_duoc_quyet_dinh_truoc_do(self):
        script = [_multi_call(), FakeModel.text("xong")]
        agent = Agent(name="A", job="đọc lương rồi gửi báo cáo", model="claude-opus-5",
                      provider=FakeModel(script), tools=[read_payroll, send_report],
                      budget="$5", sensitive=["read_payroll"])
        agent.try_run("đi")
        self.assertIn(("read_payroll", "A"), RAN)
        self.assertNotIn(("send_report", "x@y.com"), RAN,
                         "send_report vẫn chạy dù read_payroll (cùng lượt) vừa nâng "
                         "confidentiality lên SECRET — quyết định của send_report dùng "
                         "nhãn TRƯỚC KHI batch chạy, S-27")


class ChayThatQuaGraph(unittest.TestCase):
    """Backend LangGraph (`lg/runtime.py::_regate`)."""

    def setUp(self):
        RAN.clear()

    def test_send_report_bi_chan_du_da_duoc_quyet_dinh_truoc_do(self):
        from fake_chat import FakeChat
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        # `FakeChat.call()` chỉ tạo MỘT tool_call mỗi message; cần HAI trong CÙNG một
        # message nên viết script trực tiếp bằng AIMessage ở đây.
        model = FakeChat(script=[
            AIMessage(content="", tool_calls=[
                {"name": "read_payroll", "args": {"who": "A"}, "id": "c1"},
                {"name": "send_report", "args": {"to": "x@y.com"}, "id": "c2"}]),
            FakeChat.text("xong"),
        ])
        graph, rt = build_agent(model=model, tools=[read_payroll, send_report],
                                budget="$5", checkpointer=MemorySaver(),
                                sensitive=["read_payroll"])
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s27"}})
        self.assertIn(("read_payroll", "A"), RAN)
        self.assertNotIn(("send_report", "x@y.com"), RAN,
                         "send_report vẫn chạy dù read_payroll (cùng lượt) vừa nâng "
                         "confidentiality — _regate tính nhãn từ state cũ, chưa thấy kết "
                         "quả read_payroll trong cùng batch")


class DoiChung(unittest.TestCase):
    """Không có gì nhạy cảm thì luồng bình thường, không bị chặn oan — cả hai backend."""

    def setUp(self):
        RAN.clear()

    def test_khong_sensitive_thi_khong_chan_co_dien(self):
        script = [_multi_call(), FakeModel.text("xong")]
        agent = Agent(name="A", job="đọc rồi gửi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[read_payroll, send_report],
                      budget="$5")
        agent.try_run("đi")
        self.assertIn(("send_report", "x@y.com"), RAN)


class MutationXacNhanLoadBearingCoDien(unittest.TestCase):
    def test_khong_co_ban_va_thi_check_flow_van_cho_allow_voi_nhan_cu(self):
        """Mô phỏng CHÍNH XÁC hành vi trước bản vá: quyết định `send_report` chỉ dùng
        nhãn PUBLIC từ đầu batch, không bao giờ đọc lại nhãn SAU khi read_payroll chạy."""
        from harness.policy.base import Verdict
        from harness.policy.builtin import check_flow
        from harness.policy.label import Confidentiality, Grants, Integrity, Label

        d_truoc = check_flow(Label(), send_report, Grants())
        self.assertEqual(d_truoc.verdict, Verdict.ALLOW,
                         "mutation phải cho ALLOW lúc đầu batch — nếu fail, phép so sánh sai")
        d_sau = check_flow(Label(Integrity.TRUSTED, Confidentiality.SECRET), send_report,
                           Grants())
        self.assertEqual(d_sau.verdict, Verdict.DENY,
                         "mutation phải cho DENY nếu tính lại đúng lúc — nếu fail, phép so "
                         "sánh không còn phản ánh lỗi cũ")


if __name__ == "__main__":
    unittest.main()
