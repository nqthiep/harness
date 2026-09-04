"""S-28: sub-agent cần `ASK` không có đường tới node `approve` của cha — con được spawn từ
CHÍNH node `tools` của cha, node mà thiết kế nói không được dừng lâu.

review-security.md liệt ba khả năng, "không cái nào được viết ra": con `DENY` luôn (hạn chế
không nêu), cha dừng ở node `tools` (phá luật), hoặc con propagate `AWAITING_DECISION` lên
cha (cần cơ chế chưa có). Kiểm trên code hôm nay: có một khả năng THỨ TƯ, review không xét
tới — `PolicyEngine.resolve()`'s luật "không có approve=" đã tồn tại SẴN và áp dụng y hệt
cho con lẫn cha, nên một con không có `approve=` của riêng nó KHÔNG BAO GIỜ dừng chờ: tool
`danger` (hoặc bất kỳ gì dưới `safety="strict"`) tự động DENY; mọi thứ khác tự động ALLOW
kèm cảnh báo một lần. Không có "dừng lâu" nào để mà phá luật node `tools`. Không cần sửa
code — chỉ cần nói rõ (đúng như review đề nghị khi không có gì để sửa).
"""
import unittest

from harness import Agent, tool
from harness.models.fake import FakeModel

RAN: list = []


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    RAN.append(x)
    return "gone"


class ConKhongCoApproveKhongBaoGioTreo(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def test_con_khong_co_approve_thi_danger_tu_dong_deny_khong_treo(self):
        child = Agent(name="Con", job="xoá", model="claude-opus-5",
                     provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}),
                                         FakeModel.text("xong")]),
                     tools=[wipe], budget="$1")  # không có approve=
        child_tool = child.as_tool()

        parent = Agent(name="Cha", job="giao việc",
                       provider=FakeModel([FakeModel.tool_call("Con", {"task": "xoá đi"}),
                                           FakeModel.text("xong")]),
                       tools=[child_tool], budget="$5")
        r = parent.try_run("giao việc cho con")

        self.assertTrue(r.ok, f"cha không hoàn tất — có khả năng đã treo chờ approve: {r.detail}")
        self.assertEqual(RAN, [],
                         "wipe (danger) chạy dù con không có approve= — đáng lẽ phải DENY "
                         "tự động, không phải chạy hay treo chờ")

    def test_con_khong_co_approve_thi_write_tu_dong_allow_khong_treo(self):
        """Đối chứng: `write` (không phải danger) không có approve= vẫn ALLOW tự động ở
        safety mặc định — không treo, không bị chặn oan."""
        @tool(effect="write")
        def save_note(text: str) -> str:
            """Ghi chú."""
            RAN.append(text)
            return "đã ghi"

        child = Agent(name="Con2", job="ghi chú", model="claude-opus-5",
                     provider=FakeModel([FakeModel.tool_call("save_note", {"text": "hi"}),
                                         FakeModel.text("xong")]),
                     tools=[save_note], budget="$1")
        r = child.try_run("ghi lại")
        self.assertTrue(r.ok)
        self.assertEqual(RAN, ["hi"])


if __name__ == "__main__":
    unittest.main()
