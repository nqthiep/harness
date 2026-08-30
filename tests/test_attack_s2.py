"""S-2: một grant đã hết hạn vẫn cho tool chạy sau khi resume.

Đây là test ĐỎ được viết TRƯỚC code, tái hiện phát hiện chặn phát hành sắc nhất của
vòng review đối kháng (design/review-security.md S-2).

Lỗ hổng nằm trong PHÉP CHỨNG MINH, không nằm trong graph. `unguarded_paths()` duyệt DFS
từ START và chứng minh mọi đường tới `tools` đều qua `policy`. Nhưng resume nạp checkpoint
và chạy tiếp TỪ NODE BẤT KỲ: một run dừng sau `approve` sẽ vào thẳng `tools`, `policy` bị
nhảy qua, và `_pending` đã checkpoint mang sẵn verdict ALLOW. Không ai tra lại sổ.

Bất biến thay thế I-1 (design/04-runtime-durability.md §3.5):

    Gate là TIỀN ĐIỀU KIỆN TẠI CHỖ TIÊU THỤ, không phải một cạnh trong graph.

Hai test dưới đây kiểm đúng một điều đó, ở hai độ cao khác nhau.
"""
import sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent

RAN: list = []


@tool(effect="danger")
def refund(ma: str) -> str:
    """Hoàn tiền — tool nguy hiểm nhất trong bộ này."""
    RAN.append(ma)
    return "đã hoàn"


T0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


class GrantHetHan(unittest.TestCase):
    """Một grant có `expires_at` phải hết hiệu lực ở CHỖ DÙNG, không phải chỗ cấp."""

    def setUp(self):
        RAN.clear()

    def test_so_quyet_dinh_tu_choi_grant_da_het_han(self):
        """Tầng sổ: `lookup()` sau `expires_at` không được trả ALLOW."""
        from harness.policy.decision import Actor, Decision, DecisionLog, Scope
        from harness.policy import Verdict

        log = DecisionLog()
        log.record(Decision(
            id="dec-1", verdict=Verdict.ALLOW,
            scope=Scope(tool="refund", args={"ma": "DH-1"}),
            actor=Actor.human("an@example.com", via="terminal"),
            decided_at=T0, expires_at=T0 + timedelta(hours=1), run_id="r-1"))

        con_han = log.lookup("refund", {"ma": "DH-1"}, run_id="r-1",
                             now=T0 + timedelta(minutes=30))
        self.assertIs(con_han, Verdict.ALLOW, "trong hạn thì phải cho")

        het_han = log.lookup("refund", {"ma": "DH-1"}, run_id="r-1",
                             now=T0 + timedelta(hours=3))
        self.assertIsNot(het_han, Verdict.ALLOW,
                         "grant hết hạn 2 giờ trước mà vẫn ALLOW — đây là S-2")

    def test_thu_hoi_thang_grant_con_han(self):
        """Append-only: thu hồi là ghi thêm DENY, và DENY phải thắng (max())."""
        from harness.policy.decision import Actor, Decision, DecisionLog, Scope
        from harness.policy import Verdict

        log = DecisionLog()
        sc = Scope(tool="refund", args={"ma": "DH-1"})
        log.record(Decision(id="d1", verdict=Verdict.ALLOW, scope=sc,
                            actor=Actor.human("an@example.com", via="terminal"),
                            decided_at=T0, expires_at=T0 + timedelta(hours=8), run_id="r-1"))
        log.record(Decision(id="d2", verdict=Verdict.DENY, scope=sc,
                            actor=Actor.operator("sre"),
                            decided_at=T0 + timedelta(minutes=5), expires_at=None,
                            run_id="r-1"))
        self.assertIs(log.lookup("refund", {"ma": "DH-1"}, run_id="r-1",
                                 now=T0 + timedelta(hours=1)), Verdict.DENY,
                      "thu hồi bằng DENY không thắng được grant còn hạn")

    def test_run_tools_tu_kiem_lai_khong_tin_pending(self):
        """I-1 tại chỗ tiêu thụ: `_pending` mang ALLOW nhưng sổ không có grant sống
        ⇒ node `tools` PHẢI từ chối.

        Đây chính là trạng thái mà một resume vào giữa graph dựng ra.
        """
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund],
                                budget="$5", checkpointer=MemorySaver())
        state = {
            "messages": [HumanMessage("hoàn tiền đi")],
            "step": 1,
            "_pending": [{
                "tool": "refund",
                "call": {"id": "call-1", "name": "refund", "args": {"ma": "DH-1"}},
                "verdict": 0,                     # ALLOW — do checkpoint cũ mang lại
                "reason": "đã duyệt ở lượt trước",
            }],
        }
        rt.run_tools(state)
        self.assertEqual(RAN, [],
                         "tool danger chạy chỉ vì _pending nói ALLOW — không ai tra lại sổ")


class I1CoTacDungHaiChieu(unittest.TestCase):
    """Một gate chỉ chặn thì vô dụng. I-1 phải cho qua khi grant còn sống."""

    def setUp(self):
        RAN.clear()

    def _state(self):
        return {
            "messages": [HumanMessage("hoàn tiền")], "step": 1, "run_id": "r-1",
            "_pending": [{"tool": "refund",
                          "call": {"id": "c-1", "name": "refund", "args": {"ma": "DH-1"}},
                          "verdict": 0, "reason": "đã duyệt"}],
        }

    def test_grant_con_song_thi_tool_chay(self):
        from harness.policy.decision import Actor, Decision, Scope
        from harness.policy import Verdict
        from datetime import datetime, timezone

        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund],
                                budget="$5", checkpointer=MemorySaver())
        rt._decisions.record(Decision(
            id="d1", verdict=Verdict.ALLOW,
            scope=Scope(tool="refund", args={"ma": "DH-1"}, call_id="c-1"),
            actor=Actor.human("an@example.com", via="terminal"),
            decided_at=datetime.now(timezone.utc), expires_at=None, run_id="r-1"))
        rt.run_tools(self._state())
        self.assertEqual(RAN, ["DH-1"], "grant còn sống mà I-1 vẫn chặn — chặn nhầm")

    def test_thu_hoi_chan_duoc_giua_chung(self):
        """Thu hồi = ghi thêm một DENY. Không sửa hàng cũ, không xoá gì."""
        from harness.policy.decision import Actor, Decision, Scope
        from harness.policy import Verdict
        from datetime import datetime, timezone

        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund],
                                budget="$5", checkpointer=MemorySaver())
        sc = Scope(tool="refund", args={"ma": "DH-1"}, call_id="c-1")
        now = datetime.now(timezone.utc)
        rt._decisions.record(Decision(id="d1", verdict=Verdict.ALLOW, scope=sc,
                                      actor=Actor.human("an@example.com", via="terminal"),
                                      decided_at=now, expires_at=None, run_id="r-1"))
        rt._decisions.record(Decision(id="d2", verdict=Verdict.DENY, scope=sc,
                                      actor=Actor.operator("sre"),
                                      decided_at=now, expires_at=None, run_id="r-1",
                                      reason="sự cố đang mở"))
        rt.run_tools(self._state())
        self.assertEqual(RAN, [], "thu hồi không chặn được lời gọi")
        self.assertEqual(len(rt._decisions.all()), 2, "sổ phải chỉ ghi thêm, không sửa")

    def test_grant_cua_run_khac_khong_dung_duoc(self):
        """Grant keyed theo run_id — khách A không cấp quyền cho khách B."""
        from harness.policy.decision import Actor, Decision, Scope
        from harness.policy import Verdict
        from datetime import datetime, timezone

        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund],
                                budget="$5", checkpointer=MemorySaver())
        rt._decisions.record(Decision(
            id="d1", verdict=Verdict.ALLOW,
            scope=Scope(tool="refund", args={"ma": "DH-1"}, call_id="c-1"),
            actor=Actor.human("an@example.com", via="terminal"),
            decided_at=datetime.now(timezone.utc), expires_at=None, run_id="r-KHAC"))
        rt.run_tools(self._state())
        self.assertEqual(RAN, [], "grant của run khác lại cho phép run này chạy")


class VinhVienKhongBieuDienDuoc(unittest.TestCase):
    """`always_approve` của openai-agents ghi một grant sống hết đời context và không ai
    biết ai đã cấp. Ở đây một grant như thế không DỰNG được."""

    def test_allow_khong_han_dung_bi_tu_choi(self):
        from harness.policy.decision import Actor, Decision, ForeverAllow, Scope
        from harness.policy import Verdict

        with self.assertRaises(ForeverAllow) as cm:
            Decision(id="x", verdict=Verdict.ALLOW,
                     scope=Scope(tool="refund"),           # không args, không call_id
                     actor=Actor.human("an", via="terminal"),
                     decided_at=T0, expires_at=None, run_id="r-1")
        self.assertIn("expires_at", str(cm.exception), "lỗi phải nói cách sửa")

    def test_deny_thi_duoc_phep_vinh_vien(self):
        """Hạn chế là vĩnh viễn, cho phép thì không — bất đối xứng có chủ ý."""
        from harness.policy.decision import Actor, Decision, Scope
        from harness.policy import Verdict

        Decision(id="x", verdict=Verdict.DENY, scope=Scope(tool="refund"),
                 actor=Actor.operator("sre"), decided_at=T0, expires_at=None, run_id="r-1")


if __name__ == "__main__":
    unittest.main()
