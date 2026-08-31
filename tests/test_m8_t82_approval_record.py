"""M8/T-8.2 (docs/17-research-alignment.md): `Approval` là một bản ghi đầy đủ.

Kiểm trước khi sửa (đúng kỷ luật cả phiên này theo) thấy `Decision`/`DecisionLog`
(`policy/decision.py`) đã có hầu hết trường `ApprovalRecord(decision_id, actor,
policy_version, decided_at, expires_at, verdict, reason)` đòi — xây từ S-11/S-29 sớm hơn
trong phiên này — CHỈ THIẾU `policy_version`. Sửa: thêm trường đó (`POLICY_ENGINE_VERSION`
hằng số, cùng khuôn `EVENT_SCHEMA_VERSION`), stamp ở cả hai chỗ `Decision` được ghi trong
`lg/runtime.py`.

"Failure" T-8.2 tự đặt ra ("Approval hết hạn không dùng lại được") và "Test" ("Cùng một
approval không mở khoá được lần chạy thứ hai") hoá ra ĐÃ ĐÚNG SẴN — `DecisionLog.lookup()`
đã lọc `d.run_id != run_id` (grant không bao giờ vượt qua ranh giới run) VÀ `d.live_at(now)`
(hết hạn thì bị lọc) từ trước bản vá này. Test dưới đây khoá lại cả hai claim đó, không
sửa gì thêm ở logic — chỉ `policy_version` là code mới thật sự.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")

from harness.policy.base import Verdict
from harness.policy.decision import (POLICY_ENGINE_VERSION, Actor, Decision, DecisionLog,
                                     Scope)


def _now():
    return datetime.now(timezone.utc)


class PolicyVersionDuocGhi(unittest.TestCase):
    def test_decision_mang_policy_version(self):
        d = Decision(id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="x", call_id="c1"),
                    actor=Actor.policy("test"), decided_at=_now(), expires_at=None,
                    run_id="r1", policy_version=POLICY_ENGINE_VERSION)
        self.assertEqual(d.policy_version, POLICY_ENGINE_VERSION)

    def test_khong_truyen_thi_mac_dinh_none(self):
        """Tương thích ngược — `Decision(...)` không biết `policy_version` vẫn dựng
        được (mọi construction cũ trong test suite đều không truyền trường này)."""
        d = Decision(id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="x", call_id="c1"),
                    actor=Actor.policy("test"), decided_at=_now(), expires_at=None,
                    run_id="r1")
        self.assertIsNone(d.policy_version)

    def test_lg_ghi_policy_version_that(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness import tool
        from harness.lg import build_agent
        from langchain_core.messages import HumanMessage

        @tool(effect="danger")
        def xoa(x: int) -> str:
            """Xoá."""
            return "đã xoá"

        graph, rt = build_agent(
            model=FakeChat(script=[FakeChat.call("xoa", {"x": 1}), FakeChat.text("xong")]),
            tools=[xoa], budget="$5", checkpointer=MemorySaver(),
            approve=lambda c, ctx: True)
        graph.invoke({"messages": [HumanMessage("xoá")]},
                     config={"configurable": {"thread_id": "t1"}})
        rows = rt._decisions.all()
        self.assertTrue(len(rows) >= 1)
        self.assertTrue(all(r.policy_version == POLICY_ENGINE_VERSION for r in rows),
                        "mọi Decision ghi từ một run thật phải có policy_version")


class ApprovalHetHanKhongDungLaiDuoc(unittest.TestCase):
    """T-8.2's "Failure": một `Decision` hết hạn không được `lookup()` trả ALLOW nữa."""

    def test_decision_het_han_bi_loc(self):
        log = DecisionLog()
        past = _now() - timedelta(hours=2)
        log.record(Decision(
            id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="wipe", call_id=None),
            actor=Actor.operator("op1"), decided_at=past, expires_at=past + timedelta(hours=1),
            run_id="r1"))
        v = log.lookup("wipe", {}, run_id="r1", now=_now(), call_id="c2")
        self.assertEqual(v, Verdict.ASK,
                         "Decision đã hết hạn — lookup() phải fail-closed về ASK, "
                         "không phải ALLOW theo grant cũ")

    def test_decision_con_han_van_dung_duoc(self):
        log = DecisionLog()
        now = _now()
        log.record(Decision(
            id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="wipe", call_id=None),
            actor=Actor.operator("op1"), decided_at=now, expires_at=now + timedelta(hours=1),
            run_id="r1"))
        v = log.lookup("wipe", {}, run_id="r1", now=now, call_id="c2")
        self.assertEqual(v, Verdict.ALLOW)


class CungApprovalKhongMoKhoaLanChayThuHai(unittest.TestCase):
    """T-8.2's "Test": cùng một `Decision` không được mở khoá một RUN khác."""

    def test_grant_khong_vuot_qua_ranh_gioi_run(self):
        log = DecisionLog()
        now = _now()
        log.record(Decision(
            id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="wipe", call_id=None),
            actor=Actor.operator("op1"), decided_at=now, expires_at=now + timedelta(hours=1),
            run_id="r1"))
        # Cùng tool, cùng args, NHƯNG run_id khác — "lần chạy thứ hai".
        v = log.lookup("wipe", {}, run_id="r2", now=now, call_id="c1")
        self.assertEqual(v, Verdict.ASK,
                         "grant của run r1 không được mở khoá tool ở run r2 — nếu ra "
                         "ALLOW, một approval đã \"rò\" qua ranh giới run")


class MutationDecisionLogCoTacDung(unittest.TestCase):
    def test_bo_check_run_id_thi_test_ranh_gioi_do(self):
        """Mutation: một `lookup` giả bỏ qua so khớp `run_id` — xác nhận test "không
        vượt ranh giới run" thật sự phụ thuộc vào nhánh đó."""
        def fake_lookup(rows, tool, args, *, run_id, now, call_id=None):
            worst = None
            for d in rows:
                if not d.live_at(now):            # bỏ check run_id — MUTATION
                    continue
                if not d.scope.matches(tool, args, call_id=call_id):
                    continue
                worst = d.verdict if worst is None else max(worst, d.verdict)
            return Verdict.ASK if worst is None else worst

        now = _now()
        d1 = Decision(id="d1", verdict=Verdict.ALLOW, scope=Scope(tool="wipe", call_id=None),
                      actor=Actor.operator("op1"), decided_at=now,
                      expires_at=now + timedelta(hours=1), run_id="r1")
        v = fake_lookup([d1], "wipe", {}, run_id="r2", now=now, call_id="c1")
        self.assertEqual(v, Verdict.ALLOW,
                         "với mutation này, run r2 ĐƯỢC mở khoá bằng grant của r1 — khác "
                         "hành vi thật (ASK), chứng minh test phụ thuộc đúng vào check "
                         "run_id thật trong DecisionLog.lookup()")


if __name__ == "__main__":
    unittest.main()
