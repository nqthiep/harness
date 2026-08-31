"""S-29: tái dùng một grant còn sống không ghi gì thêm vào sổ `Decision` — câu "ai cho
phép" trả lời được, câu "chuyện gì đã xảy ra dưới quyền đó" thì không.

Kiểm trên `lg/runtime.py::_regate` — nơi DUY NHẤT trong `src/harness/` đọc lại
`DecisionLog.lookup()` để tái dùng một grant đã cấp (backend cổ điển không dùng
`DecisionLog` — mỗi `atry_run()` gọi `approve()` mới mỗi lần, không có khái niệm "grant
sống qua nhiều lần thực thi"). Phạm vi thật hẹp hơn văn bản gốc của S-29 mô tả ("TTL 1
giờ phủ N lần chạy khác nhau") — không có cơ chế nào trong code hôm nay cấp một `Decision`
với `scope.call_id=None` (không ràng buộc theo call_id) hay `expires_at` xa hơn một lượt;
mọi `Decision` được ghi, kể cả từ `approval_gate`, đều khoá `scope.call_id` vào đúng MỘT
lời gọi cụ thể. Cơ chế "tái dùng" thật sự đang có là I-1's recheck-lúc-tiêu-thụ (S-2): cùng
MỘT call_id được duyệt ở `approval_gate` rồi được `_regate` tra lại lúc thực thi thật —
khoảng cách đó có thể là NGAY LẬP TỨC (cùng lượt) hoặc CÁCH XA HÀNG GIỜ (resume sau khi
dừng). Chính khoảng cách đó là chỗ audit trail cũ để lộ khoảng trống: sổ chỉ có một hàng
cho lúc DUYỆT, không có hàng nào cho lúc THỰC THI THẬT.
"""
import sys, unittest
sys.path.insert(0, "src")

from datetime import datetime, timezone

from harness import tool
from harness.lg import build_agent
from harness.lg.runtime import _Ctx
from harness.policy.decision import Actor, Decision, Scope
from harness.policy.base import Verdict


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


def _now() -> "datetime":
    return datetime.now(timezone.utc)


class TaiDungGrantGhiVaoSo(unittest.TestCase):
    def _rt(self):
        from fake_chat import FakeChat
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[wipe], budget="$5")
        return rt

    def test_regate_tai_dung_grant_ghi_them_mot_decision(self):
        rt = self._rt()
        call_id = "c1"
        run_id = "t-1"
        # `approval_gate` đã duyệt call này trước đó — mô phỏng bằng ghi thẳng vào sổ.
        rt._decisions.record(Decision(
            id=f"dec-{call_id}", verdict=Verdict.ALLOW,
            scope=Scope(tool="wipe", args={"x": 1}, call_id=call_id),
            actor=Actor.human("nqthiep", via="cli"), decided_at=_now(),
            expires_at=None, run_id=run_id, reason="approved"))
        before = len(rt._decisions.all())
        self.assertEqual(before, 1)

        state = {"messages": [], "step": 1, "run_id": run_id}
        p = {"tool": "wipe", "call": {"id": call_id, "name": "wipe", "args": {"x": 1}}}
        gate = rt._regate(p, state)

        self.assertEqual(gate.verdict, Verdict.ALLOW)
        after = len(rt._decisions.all())
        self.assertGreater(after, before,
                           "tái dùng grant còn sống không ghi thêm gì vào sổ Decision — "
                           "N lần thực thi dưới cùng một grant chỉ để lại đúng một hàng")

    def test_hai_lan_regate_lien_tiep_ghi_hai_lan(self):
        """Mô phỏng resume: cùng một call được `_regate` tra lại HAI LẦN (vd. một lần lúc
        chạy thường, một lần sau khi resume từ checkpoint) — cả hai lần đều phải để lại
        dấu vết, không chỉ lần đầu."""
        rt = self._rt()
        call_id = "c1"
        run_id = "t-2"
        rt._decisions.record(Decision(
            id=f"dec-{call_id}", verdict=Verdict.ALLOW,
            scope=Scope(tool="wipe", args={"x": 1}, call_id=call_id),
            actor=Actor.human("nqthiep", via="cli"), decided_at=_now(),
            expires_at=None, run_id=run_id, reason="approved"))
        state = {"messages": [], "step": 1, "run_id": run_id}
        p = {"tool": "wipe", "call": {"id": call_id, "name": "wipe", "args": {"x": 1}}}

        rt._regate(p, state)
        n1 = len(rt._decisions.all())
        rt._regate(p, state)
        n2 = len(rt._decisions.all())
        self.assertGreater(n2, n1,
                           "lần tra lại thứ hai (mô phỏng resume) không để lại dấu vết mới")


class MutationXacNhanLoadBearing(unittest.TestCase):
    def test_khong_co_ban_va_thi_tai_dung_khong_ghi_gi(self):
        import types

        rt = TaiDungGrantGhiVaoSo()._rt()
        call_id = "c1"
        run_id = "t-3"
        rt._decisions.record(Decision(
            id=f"dec-{call_id}", verdict=Verdict.ALLOW,
            scope=Scope(tool="wipe", args={"x": 1}, call_id=call_id),
            actor=Actor.human("nqthiep", via="cli"), decided_at=_now(),
            expires_at=None, run_id=run_id, reason="approved"))
        before = len(rt._decisions.all())

        # Mô phỏng CHÍNH XÁC hành vi trước bản vá: `_regate` trả ALLOW khi tìm thấy grant,
        # KHÔNG ghi gì thêm vào sổ.
        def old_regate(self, p, state):
            from harness.policy.base import Ruling, ToolCall
            spec = self._tools.get(p["tool"])
            call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec)
            ctx = _Ctx(label=self._effective_label(state), safety=self._safety(state))
            r = self._engine_for(run_id).decide(call, ctx)
            if r.verdict is not Verdict.ASK:
                return r
            from harness.lg.runtime import _now as rt_now
            v = self._decisions.lookup(p["tool"], p["call"].get("args", {}),
                                       run_id=run_id, now=rt_now(), call_id=p["call"]["id"])
            if v is Verdict.ALLOW:
                return Ruling(Verdict.ALLOW, "grant còn sống trong sổ", "decision-log")
            return Ruling(Verdict.DENY, "không có grant", "decision-log")

        rt._regate = types.MethodType(old_regate, rt)
        state = {"messages": [], "step": 1, "run_id": run_id}
        p = {"tool": "wipe", "call": {"id": call_id, "name": "wipe", "args": {"x": 1}}}
        rt._regate(p, state)
        after = len(rt._decisions.all())
        self.assertEqual(after, before,
                         "mutation phải cho sổ KHÔNG đổi kích thước — nếu test này fail "
                         "nghĩa là bản vá không còn load-bearing")


if __name__ == "__main__":
    unittest.main()
