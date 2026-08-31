"""`DecisionLog` bền qua restart, và vòng lặp classic cuối cùng cũng CÓ một cuốn sổ.

Hai lỗ hổng, một gốc. `dispatch.py` từng ghi thẳng ra trong comment: "the classic loop has
no DecisionLog to record into … S-11's reported-actor channel has nowhere to land" — tức
là trên backend classic, ai vừa duyệt một `danger` tool được tính ra rồi ném đi. Còn trên
backend graph thì có sổ, nhưng sổ nằm trong RAM: tiến trình chết là mọi phê duyệt biến mất,
kể cả những phê duyệt vừa được một người thật bấm nút.

D-2 nói sổ này CHỈ GHI THÊM. Nên bản bền là một file JSONL nối thêm — cùng khuôn
`observe/transcript.py`, cùng lập luận ("An audit log you can edit is not an audit log") —
chứ không phải một `Store` key-value, nơi mỗi hàng mới bắt phải ghi đè cả danh sách.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from harness import Agent, Approval, DecisionLog, tool
from harness.models.fake import FakeModel
from harness.policy.base import Verdict
from harness.policy.decision import (DECISION_SCHEMA_VERSION, Actor, Decision, Scope,
                                     from_json, to_json)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)

ASKED: list = []


@tool(effect="danger")
def push(branch: str) -> str:
    """Đẩy code lên remote."""
    return "đã đẩy"


def a_row(*, id="d1", verdict=Verdict.ALLOW, tool_name="push", args=None,
          call_id="c1", expires=None, actor=None, run_id="r1"):
    return Decision(id=id, verdict=verdict,
                    scope=Scope(tool_name, args, None, call_id),
                    actor=actor or Actor.human("bob", via="slack", verified=True),
                    decided_at=NOW, expires_at=expires, run_id=run_id,
                    reason="duyệt tay", policy_version="1.0")


class HangSoDiVeDuocJson(unittest.TestCase):
    def test_khu_hoi_giu_nguyen_moi_truong(self):
        d = a_row(expires=NOW + timedelta(hours=1), call_id=None)
        back = from_json(to_json(d))
        self.assertEqual(back, d)

    def test_verdict_ghi_bang_TEN_khong_phai_so(self):
        """`Verdict` là `IntEnum`. Một file audit ghi `2` sẽ vô nghĩa nếu thứ tự lattice
        đổi; `"DENY"` thì không."""
        self.assertEqual(to_json(a_row(verdict=Verdict.DENY))["verdict"], "DENY")

    def test_schema_la_khac_thi_tu_choi_doc_chu_khong_doan(self):
        row = to_json(a_row())
        row["v"] = "999"
        with self.assertRaises(ValueError) as e:
            from_json(row)
        self.assertIn("999", str(e.exception))
        self.assertEqual(DECISION_SCHEMA_VERSION, "1")


class SoSongQuaRestart(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._d.name, "approvals.jsonl")

    def tearDown(self):
        self._d.cleanup()

    def test_ghi_o_tien_trinh_nay_doc_duoc_o_tien_trinh_sau(self):
        DecisionLog(journal=self.path).record(
            a_row(call_id=None, expires=NOW + timedelta(hours=1)))
        lai = DecisionLog(journal=self.path)
        self.assertEqual(len(lai.all()), 1)
        self.assertIs(lai.lookup("push", {"branch": "main"}, run_id="r1", now=NOW,
                                 call_id="bất kỳ"), Verdict.ALLOW)

    def test_chi_ghi_them_khong_ghi_de(self):
        log = DecisionLog(journal=self.path)
        log.record(a_row(id="d1"))
        first = open(self.path, encoding="utf-8").read()
        log.record(a_row(id="d2", call_id="c2"))
        after = open(self.path, encoding="utf-8").read()
        self.assertTrue(after.startswith(first), "hàng cũ bị viết lại — D-2 hỏng")
        self.assertEqual(len(after.strip().splitlines()), 2)

    def test_thu_hoi_la_ghi_them_mot_hang_DENY(self):
        """D-2: không sửa, không xoá. `lookup` hợp thành bằng max(), nên DENY thắng."""
        log = DecisionLog(journal=self.path)
        log.record(a_row(id="d1", call_id=None, expires=NOW + timedelta(hours=1)))
        log.record(a_row(id="d2", verdict=Verdict.DENY, call_id=None))
        lai = DecisionLog(journal=self.path)
        self.assertIs(lai.lookup("push", {}, run_id="r1", now=NOW, call_id="x"),
                      Verdict.DENY)

    def test_hang_hong_lam_do_chu_khong_bi_bo_qua_im_lang(self):
        """Bỏ qua một hàng không đọc được nghĩa là chạy tiếp với một sổ phê duyệt
        THIẾU — có thể thiếu đúng hàng DENY vừa thu hồi một quyền (IDL-30)."""
        DecisionLog(journal=self.path).record(a_row())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("{đây không phải JSON}\n")
        with self.assertRaises(ValueError) as e:
            DecisionLog(journal=self.path)
        self.assertIn("approvals.jsonl:2", str(e.exception))

    def test_file_sinh_ra_voi_quyen_0600(self):
        """`scope.args` ở đây là GIÁ TRỊ THẬT của tham số — bắt buộc, vì `Scope.matches`
        khoá grant theo đúng giá trị đó — nên file này nhạy cảm hơn transcript, vốn chỉ
        ghi digest (docs/05 §1)."""
        DecisionLog(journal=self.path).record(a_row(args={"branch": "main"}))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertIn("main", json.loads(open(self.path).readline())["scope"]["args"]["branch"])

    def test_so_trong_va_file_chua_ton_tai_thi_khong_sao(self):
        log = DecisionLog(journal=self.path)
        self.assertEqual(log.all(), ())
        self.assertFalse(os.path.exists(self.path))


class VongLapClassicCoSo(unittest.TestCase):
    def setUp(self):
        ASKED.clear()

    def mk(self, log=None, approve=None, budget="$5"):
        return Agent(name="Coder", job="j", tools=[push], budget=budget,
                     approve=approve, decisions=log,
                     provider=FakeModel([FakeModel.tool_call("push", {"branch": "main"}),
                                         FakeModel.text("xong")]))

    def test_mot_phe_duyet_duoc_ghi_lai_kem_dung_nguoi_da_duyet(self):
        """Đây là lỗ hổng S-11 mà `dispatch.py` tự ghi ra: actor được tính rồi ném đi."""
        log = DecisionLog()

        def approve(call, ctx):
            ASKED.append(call.name)
            return Approval(True, actor=Actor.human("bob", via="slack", verified=True))

        r = self.mk(log, approve).try_run("đẩy đi")
        self.assertTrue(r.ok)
        self.assertEqual(ASKED, ["push"])
        rows = log.all()
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0].verdict, Verdict.ALLOW)
        self.assertEqual(rows[0].actor, Actor.human("bob", via="slack", verified=True))
        self.assertEqual(rows[0].scope.tool, "push")
        self.assertEqual(dict(rows[0].scope.args), {"branch": "main"})
        self.assertEqual(rows[0].policy_version, "1.0")

    def test_tu_choi_cung_duoc_ghi_khong_chi_ghi_cai_duoc_cho_phep(self):
        log = DecisionLog()
        r = self.mk(log, approve=lambda call, ctx: False).try_run("đẩy đi")
        self.assertTrue(r.ok)                       # run vẫn kết thúc; tool thì không chạy
        self.assertIs(log.all()[0].verdict, Verdict.DENY)

    def test_khong_truyen_gi_thi_moi_run_mot_so_moi(self):
        """`Agent` là template đông cứng, dùng chung cho nhiều run song song: một cuốn sổ
        sống trên nó sẽ phình theo đời tiến trình và trộn hàng của mọi run."""
        a = self.mk(approve=lambda call, ctx: True)
        self.assertIsNone(a.decisions)
        self.assertTrue(a.try_run("đẩy đi").ok)     # không nổ, không rò state đi đâu

    def test_grant_con_song_tra_loi_thay_nguoi_khong_hoi_lai_lan_hai(self):
        """S-29, parity với `_regate` của backend graph — và đúng kịch bản thật: người
        trực bấm "duyệt, và duyệt luôn cho một giờ tới"."""
        log = DecisionLog()

        def approve(call, ctx):
            ASKED.append(call.name)
            now = datetime.now(timezone.utc)
            log.record(Decision(
                id="ca-truc", verdict=Verdict.ALLOW,
                scope=Scope("push", None, None, None),      # mọi lời gọi `push`
                actor=Actor.human("bob", via="slack", verified=True),
                decided_at=now, expires_at=now + timedelta(hours=1),
                run_id=ctx.run_id, reason="duyệt cho cả giờ tới"))
            return True

        agent = Agent(name="Coder", job="j", tools=[push], budget="$5", approve=approve,
                      decisions=log,
                      provider=FakeModel([FakeModel.tool_call("push", {"branch": "main"}),
                                          FakeModel.tool_call("push", {"branch": "dev"}),
                                          FakeModel.text("xong")]))
        r = agent.try_run("đẩy cả hai nhánh")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, ("push", "push"))
        self.assertEqual(ASKED, ["push"], "hỏi lại người dù sổ đã có grant còn sống")
        self.assertIs(log.all()[-1].verdict, Verdict.ALLOW)
        self.assertEqual(log.all()[-1].actor, Actor.policy("decision-log-reuse"))

    def test_grant_cua_run_khac_khong_tran_sang_run_nay(self):
        """`lookup` lọc theo `run_id`: một grant của cuộc chạy khác không được trả lời
        thay cho cuộc chạy này."""
        log = DecisionLog()
        now = datetime.now(timezone.utc)
        log.record(Decision(id="cua-run-khac", verdict=Verdict.ALLOW,
                            scope=Scope("push", None, None, None),
                            actor=Actor.operator("ops"), decided_at=now,
                            expires_at=now + timedelta(hours=1),
                            run_id="mot-run-hoan-toan-khac", reason="x"))
        self.mk(log, approve=lambda call, ctx: ASKED.append(call.name) or True).try_run("đi")
        self.assertEqual(ASKED, ["push"], "grant của run khác đã tràn sang run này")

    def test_so_trong_thi_hanh_vi_khong_doi_van_hoi_callback(self):
        """Fail-closed: không có hàng nào khớp ⇒ ASK ⇒ vẫn rơi xuống callback y như cũ."""
        log = DecisionLog()
        self.mk(log, approve=lambda call, ctx: ASKED.append(call.name) or True).try_run("đi")
        self.assertEqual(ASKED, ["push"])

    def test_so_ghi_ra_file_thi_doc_lai_duoc_o_tien_trinh_sau(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "approvals.jsonl")
            self.mk(DecisionLog(journal=path),
                    approve=lambda call, ctx: True).try_run("đẩy đi")
            lai = DecisionLog(journal=path)
            self.assertEqual(len(lai.all()), 1)
            self.assertIs(lai.all()[0].verdict, Verdict.ALLOW)

    def test_with_khong_lam_mat_so(self):
        """N-7: một trường có trên `Agent` mà thiếu trong base dict của `with_()` sẽ biến
        mất ở MỌI lần gọi, không chỉ lần chạm vào nó."""
        log = DecisionLog()
        self.assertIs(self.mk(log).with_(name="B").decisions, log)

    def test_tu_choi_boi_ask_cap_khong_duoc_ghi_thanh_nguoi_da_duyet(self):
        """Bug thật, tìm thấy khi tự review lại lượt vá DecisionLog: nhánh ask-cap từ
        chối MÀ KHÔNG BAO GIỜ gọi callback — nhưng trước bản vá này, `actor` bị bỏ
        `None` trên nhánh đó, và vì `self._e._a.approve is not None` (có cấu hình
        callback), sổ ghi `Actor.human("approver", via="callback")` như thể một NGƯỜI
        đã từ chối qua callback. Một audit sau này đọc hàng đó sẽ tin nhầm là đã có
        người được hỏi và đã từ chối — đúng lỗ hổng "self-declared identity" mà D-1/S-11
        tồn tại để ngăn, lần này do CHÍNH policy của harness tự gây ra."""
        log = DecisionLog()
        asked = []

        def approve(call, ctx):
            asked.append(call.name)
            return True

        agent = Agent(name="Coder", job="j", tools=[push], budget="$5",
                      approve=approve, decisions=log, max_asks_per_run=1,
                      provider=FakeModel([
                          FakeModel.tool_call("push", {"branch": "main"}, call_id="c1"),
                          FakeModel.tool_call("push", {"branch": "dev"}, call_id="c2"),
                          FakeModel.text("xong"),
                      ]))
        agent.try_run("đẩy hai nhánh")
        # ASK đầu tiên đi qua callback thật (dưới trần); ASK thứ hai vượt trần, bị
        # ask-cap từ chối KHÔNG hỏi callback.
        self.assertEqual(asked, ["push"])
        rows = log.all()
        self.assertEqual(len(rows), 2)
        capped = [r for r in rows if r.verdict is Verdict.DENY]
        self.assertEqual(len(capped), 1)
        self.assertEqual(capped[0].actor, Actor.policy("ask-cap"),
                         "từ chối bởi ask-cap bị ghi nhầm thành một người đã duyệt")
        self.assertNotEqual(capped[0].actor.kind, "human")


class BackendGraphNhanDuocSo(unittest.TestCase):
    def test_build_agent_truyen_duoc_decisions_xuong_runtime(self):
        """Tham số đã có trên `Runtime` từ S-29 nhưng `build_agent` không chuyền xuống,
        nên không caller nào chạm tới được: nó tồn tại mà không tới được."""
        from fake_chat import FakeChat
        from harness.lg import build_agent
        log = DecisionLog()
        _, rt = build_agent(model=FakeChat(script=[FakeChat.text("hi")]), tools=[push],
                            budget="$5", decisions=log)
        self.assertIs(rt._decisions, log)


if __name__ == "__main__":
    unittest.main()
