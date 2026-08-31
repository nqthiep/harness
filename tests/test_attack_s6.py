"""S-6: `lookup()` chỉ được HẠ `ASK` xuống `ALLOW`, không bao giờ hạ `DENY`.

design/review-security.md S-6 chỉ ra `02-safety-engine.md` mô tả bước hợp thành giữa
`PolicyEngine.decide()` (tính lại mỗi lần) và `DecisionLog.lookup()` (sổ cũ) bằng văn xuôi
("grant còn hạn → dùng lại"), không bằng công thức — và một implementer đọc câu đó theo
nghĩa tự nhiên nhất sẽ để một grant cấp lúc context còn sạch tiếp tục thắng sau khi context
bị nhiễm (DENY từ taint).

Đọc lại `_regate()` (bước 1, `lg/runtime.py`) cho thấy code ĐÃ đúng — `if r.verdict is not
Verdict.ASK: return r` trả DENY ngay, không bao giờ chạm `lookup()`. Tệp này không sửa gì;
nó KHOÁ LẠI bất biến đó bằng cách xét đủ mọi tổ hợp hữu hạn, để một lần refactor sau này lỡ
đảo thứ tự hai bước thì test đỏ ngay, thay vì phải đợi review lần thứ hai bắt lại đúng lỗi
đã tìm được lần đầu.

Không gian đủ nhỏ để xét HẾT (3 verdict × 3 trạng thái sổ = 9 tổ hợp), không cần
hypothesis — enumerate hết còn chắc hơn random.
"""
import sys, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent
from harness.policy.base import Ruling, Verdict
from harness.policy.decision import Actor, Decision, Scope


@tool(effect="danger")
def refund(ma: str) -> str:
    """Hoàn tiền."""
    return "đã hoàn"


class _FakeEngine:
    """Đứng thay `PolicyEngine` thật, trả đúng verdict được yêu cầu — cô lập công thức hợp
    thành khỏi việc `EffectPolicy`/`TaintPolicy` tính ra verdict đó bằng cách nào."""
    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def decide(self, call, ctx) -> Ruling:
        return Ruling(self._verdict, "fake", "fake")


def _pending(call_id="c1"):
    return {"tool": "refund", "call": {"id": call_id, "name": "refund", "args": {"ma": "X"}},
            "verdict": 0, "reason": "(không dùng — _regate tính lại)"}


def _state(run_id="r-1"):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage("go")], "step": 1, "run_id": run_id}


class HopThanhDayDu(unittest.TestCase):
    """9 tổ hợp: verdict tươi ∈ {ALLOW, ASK, DENY} × trạng thái sổ ∈ {không grant,
    grant ALLOW còn sống, grant DENY (đã thu hồi)}."""

    def _rt(self, verdict: Verdict):
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund], budget="$5",
                                checkpointer=MemorySaver())
        rt._engine = _FakeEngine(verdict)
        return rt

    def _with_grant(self, rt, verdict: Verdict, run_id="r-1", call_id="c1"):
        from datetime import datetime, timezone
        rt._decisions.record(Decision(
            id="d1", verdict=verdict,
            scope=Scope(tool="refund", args={"ma": "X"}, call_id=call_id),
            actor=Actor.human("an", via="terminal") if verdict is Verdict.ALLOW
                  else Actor.operator("sre"),
            decided_at=datetime.now(timezone.utc), expires_at=None, run_id=run_id))

    # -- verdict tươi = ALLOW: sổ không được tham gia, kết quả luôn ALLOW ------------
    def test_allow_khong_grant(self):
        rt = self._rt(Verdict.ALLOW)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.ALLOW)

    def test_allow_co_grant_allow(self):
        rt = self._rt(Verdict.ALLOW)
        self._with_grant(rt, Verdict.ALLOW)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.ALLOW)

    def test_allow_co_grant_deny(self):
        """Grant DENY (thu hồi) trong sổ KHÔNG được hạ một ALLOW tươi — engine tươi
        không tra sổ khi verdict của nó đã là ALLOW."""
        rt = self._rt(Verdict.ALLOW)
        self._with_grant(rt, Verdict.DENY)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.ALLOW)

    # -- verdict tươi = DENY: sổ KHÔNG BAO GIỜ được tham gia — đây là tim của S-6 ----
    def test_deny_khong_grant(self):
        rt = self._rt(Verdict.DENY)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.DENY)

    def test_deny_co_grant_allow_van_deny(self):
        """CHÍNH XÁC kịch bản S-6: grant ALLOW cấp lúc context sạch, giờ policy tươi
        (taint) nói DENY. Grant KHÔNG được thắng."""
        rt = self._rt(Verdict.DENY)
        self._with_grant(rt, Verdict.ALLOW)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.DENY,
                      "S-6 sống lại: grant cũ thắng được một DENY tươi từ taint")

    def test_deny_co_grant_deny(self):
        rt = self._rt(Verdict.DENY)
        self._with_grant(rt, Verdict.DENY)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.DENY)

    # -- verdict tươi = ASK: đây là NHÁNH DUY NHẤT sổ được quyền quyết định ----------
    def test_ask_khong_grant_thi_deny(self):
        """Không có gì trong sổ ⇒ fail-closed, không phải ALLOW ngầm."""
        rt = self._rt(Verdict.ASK)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.DENY)

    def test_ask_co_grant_allow_thi_allow(self):
        """Đây là TOÀN BỘ lý do sổ tồn tại: hạ ASK xuống ALLOW."""
        rt = self._rt(Verdict.ASK)
        self._with_grant(rt, Verdict.ALLOW)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.ALLOW)

    def test_ask_co_grant_deny_thi_deny(self):
        rt = self._rt(Verdict.ASK)
        self._with_grant(rt, Verdict.DENY)
        self.assertIs(rt._regate(_pending(), _state()).verdict, Verdict.DENY)

    # -- công thức viết thành luật, không chỉ chín ca rời rạc ------------------------
    def test_cong_thuc_dung_cho_ca_9_to_hop(self):
        """Luật thật, viết thành code thay vì chín ca rời rạc ở trên:

            verdict tươi != ASK  ⇒  final == verdict tươi   (sổ KHÔNG được chạm tới)
            verdict tươi == ASK  ⇒  final == (grant nếu có, DENY nếu không — fail-closed)

        `final >= verdict` mà bản nháp đầu của test này viết SAI: với ASK, hạ xuống ALLOW
        chính là chức năng của sổ, không phải nới lỏng — ASK là 'chưa quyết', không phải
        một sàn không được đi xuống. Cái không được nới lỏng là ALLOW/DENY đã CHỐT, và đó
        là điều 9 test rời rạc ở trên đã xét đủ. Test này chỉ gộp chúng lại thành một luật
        kiểm được bằng phép toán, đúng tinh thần R-2."""
        for verdict in (Verdict.ALLOW, Verdict.ASK, Verdict.DENY):
            for grant in (None, Verdict.ALLOW, Verdict.DENY):
                rt = self._rt(verdict)
                if grant is not None:
                    self._with_grant(rt, grant)
                final = rt._regate(_pending(), _state()).verdict
                expected = verdict if verdict is not Verdict.ASK else (
                    grant if grant is not None else Verdict.DENY)
                self.assertIs(final, expected,
                              f"verdict={verdict.name} grant={grant} ⇒ final={final.name}, "
                              f"kỳ vọng {expected.name}")


if __name__ == "__main__":
    unittest.main()
