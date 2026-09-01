"""DecisionLog wiring in `dispatch.py`/`run.py` — vòng lặp classic ghi mỗi ASK đã giải
quyết vào `policy/decision.py::DecisionLog`, cạnh (không thay thế) `execute_once`
(S-4/N-8) đã có sẵn cho idempotency.

Bốn điều bản nối này tự hứa, tương ứng bốn nhóm test dưới đây:

1. Một DENY do trần `max_asks_per_run` (S-25(b)) phải ghi `actor=Actor.policy("ask-cap")`
   — KHÔNG được rơi vào nhánh mặc định `Actor.human("approver", via="callback")`, vốn sẽ
   bịa ra rằng một người đã từ chối một lời gọi mà thật ra chính sách tự cắt, chưa từng hỏi
   ai (D-1: không actor nào tự khai bởi model/hệ thống mà không đúng sự thật).
2. Một ASK cùng `call_id`/tool/args đã có một hàng CÒN HẠN trong sổ thì không hỏi lại
   `approve=` callback lần hai — sổ trả lời thay.
3. `Agent(principal=...)` phải tới được `RunContext.principal` mà một `Policy.check()`
   đọc — không chỉ `tenant_id`.
4. `ToolCall.idempotency_key` một `Policy`/tool tự đọc được phải khớp
   `idempotency_key(run_id, call_id)` — ổn định qua các lần thử lại của MỘT lời gọi.
5. `DecisionLog(journal=...)` sống qua một "khởi động lại" mô phỏng: nạp lại từ đúng file,
   một `DecisionLog` MỚI (đại diện cho tiến trình mới) thấy đúng những hàng tiến trình cũ
   đã ghi.

Cố ý không kiểm tra ở đây: backend LangGraph (chưa nối `DecisionLog` theo đúng hình dạng
này — theo dõi riêng, R-17) và các luật `AuthEvidence`/`require_approval_evidence` đã có
test riêng ở `tests/test_attack_s11.py`.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.idempotency import idempotency_key
from harness.models.fake import FakeModel
from harness.policy.base import Ruling, Verdict
from harness.policy.decision import Actor, DecisionLog


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


class AskCapActorDungKhongPhaiHuman(unittest.TestCase):
    def test_deny_do_ask_cap_ghi_actor_policy_khong_phai_human(self):
        log = DecisionLog()
        script = [FakeModel.tool_call("wipe", {"x": i}, call_id=f"c{i}") for i in range(4)]
        script.append(FakeModel.text("xong"))
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5", decisions=log, approve=lambda c, x: True,
                      max_asks_per_run=3)
        agent.try_run("đi")
        denied = [d for d in log.all() if d.verdict is Verdict.DENY]
        self.assertTrue(denied, "không có DENY nào được ghi khi vượt trần ask-cap")
        self.assertEqual(denied[-1].actor, Actor.policy("ask-cap"),
                         "DENY do ask-cap phải gán actor.policy('ask-cap'), không phải "
                         "Actor.human giả định approve= đã thật sự được gọi cho lời gọi bị "
                         "chặn — đúng lỗi tự-khai-danh-tính D-1/S-11 tồn tại để chặn")


class DecisionLogReuseKhongHoiLaiHaiLan(unittest.TestCase):
    def test_ask_lap_lai_dung_call_id_khong_goi_approve_lan_hai(self):
        """Hai bước riêng biệt, cùng `call_id` mặc định của `FakeModel.tool_call`
        ("c1") — chữ ký y hệt hàng đã ghi ở bước trước. Sổ phải trả lời thay, không hỏi
        `approve=` callback lần thứ hai."""
        seen: list[int] = []

        def approve(c, x):
            seen.append(1)
            return True

        script = [FakeModel.tool_call("wipe", {"x": 1}),
                 FakeModel.tool_call("wipe", {"x": 1}),
                 FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5", approve=approve)
        r = agent.try_run("đi")
        self.assertEqual(len(seen), 1,
                         "một ASK cùng call_id đã bị hỏi lại thay vì dùng lại quyết định "
                         "đã ghi trong sổ")
        self.assertEqual(r.tools_run, ("wipe", "wipe"))

    def test_deny_da_ghi_cung_thu_hoi_ma_khong_hoi_lai(self):
        """Nửa còn lại của cùng luật: một DENY đã ghi cũng trả lời thay, không hỏi lại —
        `lookup()` hợp thành bằng `max()`, không cần một luật ưu tiên thứ hai."""
        seen: list[int] = []

        def deny(c, x):
            seen.append(1)
            return False

        script = [FakeModel.tool_call("wipe", {"x": 1}),
                 FakeModel.tool_call("wipe", {"x": 1}),
                 FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5", approve=deny)
        r = agent.try_run("đi")
        self.assertEqual(len(seen), 1)
        self.assertEqual(r.tools_run, (), "wipe không được chạy — cả hai lần đều DENY")


class PrincipalToiPolicyCheck(unittest.TestCase):
    def test_principal_toi_duoc_ctx_ma_policy_doc(self):
        seen_principal: list[str | None] = []

        class GhiLaiPrincipal:
            name = "ghi-principal"

            def check(self, call, ctx):
                seen_principal.append(ctx.principal)
                return Ruling(Verdict.ALLOW, "ok", self.name)

        @tool(effect="read")
        def noop() -> str:
            """Không làm gì."""
            return "ok"

        script = [FakeModel.tool_call("noop", {}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[noop],
                      budget="$5", principal="user-42", policies=[GhiLaiPrincipal()])
        agent.try_run("đi")
        self.assertIn("user-42", seen_principal)

    def test_khong_dat_thi_principal_la_none(self):
        seen_principal: list[str | None] = ["chưa gọi"]

        class GhiLaiPrincipal:
            name = "ghi-principal"

            def check(self, call, ctx):
                seen_principal[0] = ctx.principal
                return Ruling(Verdict.ALLOW, "ok", self.name)

        @tool(effect="read")
        def noop2() -> str:
            """Không làm gì."""
            return "ok"

        script = [FakeModel.tool_call("noop2", {}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[noop2],
                      budget="$5", policies=[GhiLaiPrincipal()])
        agent.try_run("đi")
        self.assertIsNone(seen_principal[0])


class IdempotencyKeyTrenToolCall(unittest.TestCase):
    def test_idempotency_key_khop_cong_thuc_chuan(self):
        seen_keys: list[str | None] = []

        class GhiLaiKey:
            name = "ghi-key"

            def check(self, call, ctx):
                seen_keys.append(call.idempotency_key)
                return Ruling(Verdict.ALLOW, "ok", self.name)

        @tool(effect="read")
        def noop3() -> str:
            """Không làm gì."""
            return "ok"

        script = [FakeModel.tool_call("noop3", {}, call_id="call-xyz"),
                 FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[noop3],
                      budget="$5", policies=[GhiLaiKey()])
        r = agent.try_run("đi")
        self.assertEqual(len(seen_keys), 1)
        self.assertEqual(seen_keys[0], idempotency_key(r.run_id, "call-xyz"))


class SoSongQuaKhoiDongLai(unittest.TestCase):
    """`journal=` — mô phỏng khởi động lại: đóng tiến trình (không giữ gì trong bộ nhớ
    ngoài file), dựng một `DecisionLog` MỚI trỏ cùng file, xác nhận nó thấy đúng những
    hàng tiến trình trước đã ghi."""

    def test_nap_lai_tu_file_sau_khoi_dong_lai_mo_phong(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = str(Path(tmp) / "approvals.jsonl")
            log1 = DecisionLog(journal=journal)
            script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
            agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                          budget="$5", decisions=log1, approve=lambda c, x: True)
            r1 = agent.try_run("đi")
            self.assertEqual(r1.tools_run, ("wipe",))
            rows_before = log1.all()
            self.assertTrue(rows_before)

            # "Khởi động lại": một DecisionLog hoàn toàn mới, không chia sẻ gì trong bộ
            # nhớ với log1 — chỉ có file chung.
            log2 = DecisionLog(journal=journal)
            self.assertEqual(len(log2.all()), len(rows_before))
            self.assertEqual(log2.all()[0].scope.tool, "wipe")
            self.assertEqual(log2.all()[0].verdict, Verdict.ALLOW)

    def test_file_moi_tao_quyen_0600(self):
        import os
        import stat

        with tempfile.TemporaryDirectory() as tmp:
            journal = str(Path(tmp) / "approvals.jsonl")
            log = DecisionLog(journal=journal)
            script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
            agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                          budget="$5", decisions=log, approve=lambda c, x: True)
            agent.try_run("đi")
            mode = stat.S_IMODE(os.stat(journal).st_mode)
            self.assertEqual(mode, 0o600,
                             "sổ phê duyệt chứa evidence/args thật, không được tạo với "
                             "quyền đọc cho cả máy")


class MutationDecisionLogWiringLoadBearing(unittest.TestCase):
    def test_khoi_phuc_khong_ghi_gi_thi_ask_cap_test_do(self):
        """Mutation: `DecisionLog.record()` không làm gì (mô phỏng CHƯA nối dây) — xác
        nhận `AskCapActorDungKhongPhaiHuman` thật sự phụ thuộc vào bản nối, không phải
        một cơ chế khác tình cờ giữ lại actor đúng."""
        log = DecisionLog()
        original = DecisionLog.record
        DecisionLog.record = lambda self, d: d          # không ghi thêm vào self._rows
        try:
            script = [FakeModel.tool_call("wipe", {"x": i}, call_id=f"c{i}")
                     for i in range(4)]
            script.append(FakeModel.text("xong"))
            agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                          budget="$5", decisions=log, approve=lambda c, x: True,
                          max_asks_per_run=3)
            agent.try_run("đi")
            self.assertEqual(log.all(), (),
                             "mutation phải làm sổ trống rỗng — nếu fail, phép so sánh "
                             "không còn phản ánh đúng hành vi 'chưa nối dây'")
        finally:
            DecisionLog.record = original


if __name__ == "__main__":
    unittest.main()
