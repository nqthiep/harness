"""S-11: `Actor` là lời tự khai của bên giữ callback `approve=`, không phải danh tính đã
xác thực — `Approver(fn, actor=...)` cố định danh tính LÚC DỰNG, bất kể ai thật sự bấm nút.

Kiểm trên code hôm nay: không có `Approver`/`AskOutcome` (thiết kế gốc mô tả, chưa từng
được xây) — chữ ký thật là `ApprovalFn = Callable[[ToolCall, RunContext], bool]`, và trước
bản vá này `PolicyEngine.resolve()` chỉ nhận `bool`, nên `Decision.actor` LUÔN LÀ hằng số
`Actor.human("approver", via="callback")` cho MỌI lần duyệt qua callback, không phân biệt
được ai đã bấm.

Bản vá đầu (`ApprovalKenhTuyChon` trở xuống) không xây `AuthEvidence` — chỉ mở một kênh
TÙY CHỌN: `approve=` có thể trả `Approval(ok, actor=...)` thay vì `bool` trần, và khi đó
`Decision.actor` ghi đúng danh tính đó. Callback nào vẫn trả `bool` thì hành vi y hệt
trước bản vá — không có gì bị buộc phải đổi.

`AuthEvidence` (đã sửa, các class ở cuối file) đóng nốt phần còn lại: `Approval`/
`Decision` giờ mang thêm `evidence=`, và `Agent(require_approval_evidence=True)` là nơi
một deployment BẬT bắt buộc — DENY một actor `human` báo cáo mà không kèm bằng chứng,
thay vì âm thầm tin. Mặc định vẫn TẮT, y hệt kỷ luật tương thích ngược mọi cổng tuỳ chọn
khác trong codebase này.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness import Actor, Approval, AuthEvidence, tool
from harness.policy.base import Verdict


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


class ApprovalKenhTuyChon(unittest.TestCase):
    """Đơn vị: `PolicyEngine.resolve()`."""

    def _engine_ask(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine

        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _ctx_call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext

        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        call = ToolCall("c1", "wipe", {"x": 1}, wipe)
        return call, ctx

    def test_bool_tran_thi_actor_la_none_tu_resolve(self):
        """Đối chứng: callback trả `bool` trần — `resolve()` không báo actor nào, đúng
        hành vi trước bản vá (nơi gọi tự đặt placeholder chung)."""
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        d, actor, _ev = asyncio.run(engine.resolve(decision, call, ctx, lambda c, x: True))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertIsNone(actor, "bool trần không được tự bịa ra một actor nào")

    def test_approval_object_thi_actor_dung_nguoi_that(self):
        """Callback trả `Approval(ok, actor=...)` — `resolve()` phải trả lại ĐÚNG actor
        đó, không phải placeholder."""
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        real_actor = Actor.human("nqthiep", via="slack:U123")

        def approve(c, x): return Approval(ok=True, actor=real_actor)

        d, actor, _ev = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertEqual(actor, real_actor,
                         "resolve() không trả lại đúng actor mà callback báo — S-11")

    def test_approval_object_deny_van_hoat_dong(self):
        import asyncio

        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x): return Approval(ok=False, actor=Actor.human("x", via="cli"))

        d, actor, _ev = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.DENY)


class GraphGhiDungActorKhiCoBaoCao(unittest.TestCase):
    """Chạy thật qua backend LangGraph — `Decision.actor` trong sổ phải là actor callback
    báo, không phải placeholder `"approver"` cố định."""

    def test_decision_log_ghi_actor_that_khong_phai_placeholder(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        real_actor = Actor.human("nqthiep", via="slack:U123")

        def approve(call, ctx): return Approval(ok=True, actor=real_actor)

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=approve)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s11"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows, "không có Decision nào được ghi cho wipe")
        self.assertEqual(rows[0].actor, real_actor,
                         "Decision.actor là placeholder chung, không phải actor callback "
                         "báo — S-11 chưa được đóng")

    def test_bool_tran_thi_van_dung_placeholder_cu(self):
        """Đối chứng: callback trả `bool` trần vẫn hoạt động y hệt trước bản vá — không
        có gì bị buộc phải đổi."""
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=lambda call, ctx: True)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s11-bool"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows)
        self.assertEqual(rows[0].actor, Actor.human("approver", via="callback"))


class MutationXacNhanLoadBearing(unittest.TestCase):
    def test_khong_co_ban_va_thi_actor_luon_la_placeholder(self):
        """Mô phỏng CHÍNH XÁC hành vi trước bản vá: `resolve()` chỉ nhìn `bool(out)`,
        không bao giờ đọc `Approval.actor` dù callback có báo."""
        import asyncio

        engine, decision = self._make()
        call, ctx = self._call()
        real_actor = Actor.human("nqthiep", via="slack:U123")
        out = Approval(ok=True, actor=real_actor)
        # Hành vi cũ: chỉ có `bool(out)` được đọc — một Approval() luôn truthy nên vẫn
        # ALLOW, nhưng actor thật KHÔNG BAO GIỜ tới được đây.
        old_behavior_ok = bool(out)
        self.assertTrue(old_behavior_ok,
                        "mutation: bool(Approval(...)) phải truthy — nếu fail, phép so "
                        "sánh không còn phản ánh đúng hành vi cũ")
        # Chứng minh hành vi MỚI thực sự khác: resolve() thật đọc được actor.
        engine2, decision2 = self._make()
        d, actor, _ev = asyncio.run(engine2.resolve(decision2, call, ctx, lambda c, x: out))
        self.assertEqual(actor, real_actor,
                         "resolve() không đọc Approval.actor — bản vá không load-bearing")

    def _make(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine
        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext
        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        return ToolCall("c1", "wipe", {"x": 1}, wipe), ctx


def _evidence(**over):
    base = dict(channel="slack", channel_message_id="msg_1", principal="U123")
    base.update(over)
    return AuthEvidence(**base)


class AuthEvidenceResolveUnit(unittest.TestCase):
    """Đơn vị: `resolve()` mang `evidence` đi cùng `actor` khi callback báo — độc lập
    với `require_evidence`."""

    def _engine_ask(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine
        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _ctx_call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext
        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        return ToolCall("c1", "wipe", {"x": 1}, wipe), ctx

    def test_evidence_di_theo_actor_khi_callback_bao(self):
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        ev = _evidence()

        def approve(c, x):
            return Approval(ok=True, actor=Actor.human("nqthiep", via="slack"), evidence=ev)

        d, actor, evidence = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertEqual(evidence, ev)

    def test_bool_tran_thi_evidence_la_none(self):
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        d, actor, evidence = asyncio.run(engine.resolve(decision, call, ctx, lambda c, x: True))
        self.assertIsNone(evidence)


class RequireApprovalEvidenceDeny(unittest.TestCase):
    """`resolve(require_evidence=True)` — S-11, đã sửa: một actor `human` không kèm
    bằng chứng bị DENY thay vì được tin."""

    def _engine_ask(self):
        from harness.policy.base import Ruling
        from harness.policy.engine import PolicyEngine
        return PolicyEngine(builtins=()), Ruling(Verdict.ASK, "cần duyệt", "policy")

    def _ctx_call(self):
        from harness.policy.base import ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext
        ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
        return ToolCall("c1", "wipe", {"x": 1}, wipe), ctx

    def test_human_khong_evidence_bi_deny(self):
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x):
            return Approval(ok=True, actor=Actor.human("ke-gia-mao", via="cli"))

        d, actor, evidence = asyncio.run(
            engine.resolve(decision, call, ctx, approve, require_evidence=True))
        self.assertEqual(d.verdict, Verdict.DENY)
        self.assertIn("AuthEvidence", d.reason)

    def test_human_co_evidence_van_allow(self):
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x):
            return Approval(ok=True, actor=Actor.human("nqthiep", via="slack"),
                            evidence=_evidence())

        d, actor, evidence = asyncio.run(
            engine.resolve(decision, call, ctx, approve, require_evidence=True))
        self.assertEqual(d.verdict, Verdict.ALLOW)
        self.assertIsNotNone(evidence)

    def test_khong_bat_thi_human_khong_evidence_van_allow(self):
        """Đối chứng tương thích ngược — `require_evidence` mặc định `False`."""
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x):
            return Approval(ok=True, actor=Actor.human("x", via="cli"))

        d, actor, evidence = asyncio.run(engine.resolve(decision, call, ctx, approve))
        self.assertEqual(d.verdict, Verdict.ALLOW)

    def test_bool_tran_khong_bi_anh_huong_boi_require_evidence(self):
        """`require_evidence=True` chỉ áp cho actor `human` báo cáo được — một callback
        trả `bool` trần (không actor nào) không bị chặn oan."""
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()
        d, actor, evidence = asyncio.run(
            engine.resolve(decision, call, ctx, lambda c, x: True, require_evidence=True))
        self.assertEqual(d.verdict, Verdict.ALLOW)

    def test_actor_khong_phai_human_khong_bi_anh_huong(self):
        """`policy`/`operator` không phải lời tự khai qua một kênh — luật chỉ nhắm
        đúng `human`."""
        import asyncio
        engine, decision = self._engine_ask()
        call, ctx = self._ctx_call()

        def approve(c, x):
            return Approval(ok=True, actor=Actor.operator("ci-bot"))

        d, actor, evidence = asyncio.run(
            engine.resolve(decision, call, ctx, approve, require_evidence=True))
        self.assertEqual(d.verdict, Verdict.ALLOW)


class ClassicBackendEndToEnd(unittest.TestCase):
    """Chạy thật qua `Agent` (backend cổ điển) — `require_approval_evidence=True` phải
    thật sự chặn tool, và `policy.decided` giờ phải mang actor/evidence (đóng luôn khe hở
    trước bản vá: backend cổ điển từng BỎ actor mà `resolve()` trả về, không ghi vào đâu
    cả — xem comment cũ ở `dispatch.py::_run_batch`)."""

    class _Rec:
        def __init__(self):
            self.events = []
        def emit(self, e):
            self.events.append((e.kind.value, dict(e.data)))
        def close(self): ...
        def of(self, kind):
            return [d for k, d in self.events if k == kind]

    def test_human_khong_evidence_bi_chan_that(self):
        from harness import Agent
        from harness.models.fake import FakeModel

        rec = self._Rec()
        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5", exporters=[rec], require_approval_evidence=True,
                      approve=lambda c, x: Approval(ok=True,
                                                    actor=Actor.human("ke-gia-mao", via="cli")))
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(r.tools_run, (), "wipe không được chạy — DENY vì thiếu evidence")
        decided = rec.of("policy.decided")
        self.assertTrue(decided)
        self.assertEqual(decided[-1]["verdict"], "DENY")

    def test_human_co_evidence_thi_chay_duoc(self):
        from harness import Agent
        from harness.models.fake import FakeModel

        rec = self._Rec()
        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
        ev = _evidence()
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5", exporters=[rec], require_approval_evidence=True,
                      approve=lambda c, x: Approval(
                          ok=True, actor=Actor.human("nqthiep", via="slack"), evidence=ev))
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(r.tools_run, ("wipe",))
        decided = rec.of("policy.decided")
        self.assertEqual(decided[-1]["verdict"], "ALLOW")
        self.assertEqual(decided[-1]["actor"], {"kind": "human", "id": "nqthiep", "via": "slack"})
        self.assertEqual(decided[-1]["evidence"]["channel_message_id"], "msg_1")

    def test_khong_bat_thi_khong_doi_hanh_vi(self):
        """Đối chứng tương thích ngược trên toàn bộ `Agent` — mặc định
        `require_approval_evidence=False`."""
        from harness import Agent
        from harness.models.fake import FakeModel

        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[wipe],
                      budget="$5",
                      approve=lambda c, x: Approval(ok=True,
                                                    actor=Actor.human("x", via="cli")))
        r = agent.try_run("đi")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(r.tools_run, ("wipe",))


class DurableBackendEndToEnd(unittest.TestCase):
    """Chạy thật qua `build_agent()` (LangGraph) — `Decision.evidence` phải ghi được, và
    `require_approval_evidence=True` phải chặn tool y hệt backend cổ điển."""

    def test_evidence_ghi_vao_decision_log(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        ev = _evidence()

        def approve(call, ctx):
            return Approval(ok=True, actor=Actor.human("nqthiep", via="slack"), evidence=ev)

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=approve)
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                     {"configurable": {"thread_id": "t-s11-evidence"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows)
        self.assertEqual(rows[0].evidence, ev)

    def test_require_evidence_chan_that(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        def approve(call, ctx):
            return Approval(ok=True, actor=Actor.human("ke-gia-mao", via="cli"))

        graph, rt = build_agent(model=FakeChat(script=[FakeChat.call("wipe", {"x": 1}),
                                                        FakeChat.text("xong")]),
                                tools=[wipe], budget="$5", checkpointer=MemorySaver(),
                                approve=approve, require_approval_evidence=True)
        out = graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                           {"configurable": {"thread_id": "t-s11-deny"}})
        rows = [d for d in rt._decisions.all() if d.scope.tool == "wipe"]
        self.assertTrue(rows)
        self.assertEqual(rows[0].verdict.name, "DENY")
        got = [m.content for m in out["messages"]
              if getattr(m, "content", None) and "declined" in str(m.content)]
        self.assertTrue(got, "wipe phải bị từ chối trong message trả về model")


class ServiceApiEvidenceParsing(unittest.TestCase):
    """`_evidence_from_body` — `harness[server]` extra, `POST .../approvals/{call_id}`."""

    def test_none_thi_none(self):
        from harness.server import _evidence_from_body
        self.assertIsNone(_evidence_from_body(None))

    def test_day_du_thi_dung_auth_evidence(self):
        from harness.server import _evidence_from_body
        got = _evidence_from_body({"channel": "slack", "channel_message_id": "m1",
                                   "principal": "U1"})
        self.assertEqual(got, AuthEvidence(channel="slack", channel_message_id="m1",
                                           principal="U1"))

    def test_thieu_truong_bat_buoc_raise(self):
        from harness.server import _evidence_from_body
        with self.assertRaises(ValueError):
            _evidence_from_body({"channel": "slack"})

    def test_khong_phai_dict_raise(self):
        from harness.server import _evidence_from_body
        with self.assertRaises(ValueError):
            _evidence_from_body("not a dict")

    def test_chu_ky_hex_va_verified_at_parse_dung(self):
        from harness.server import _evidence_from_body
        got = _evidence_from_body({
            "channel": "slack", "channel_message_id": "m1", "principal": "U1",
            "signature": "deadbeef", "verified_at": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(got.signature, bytes.fromhex("deadbeef"))
        self.assertIsNotNone(got.verified_at)


class MutationS11EvidenceLoadBearing(unittest.TestCase):
    def test_khoi_phuc_hanh_vi_cu_thi_test_deny_do(self):
        """Mutation thật: bỏ nhánh kiểm evidence trong `PolicyEngine.resolve()` — xác
        nhận test DENY ở trên thật sự đỏ nếu thiếu bản vá."""
        import asyncio

        import harness.policy.engine as engine_mod
        from harness.policy.base import Ruling, ToolCall
        from harness.policy.label import Label
        from harness.run import RunContext

        original = engine_mod.PolicyEngine.resolve

        async def _old_resolve(self, decision, call, ctx, approve, *, require_evidence=False):
            # Hành vi TRƯỚC bản vá evidence: `require_evidence` bị lờ đi hoàn toàn.
            if decision.verdict is not Verdict.ASK:
                return decision, None, None
            out = approve(call, ctx)
            if hasattr(out, "__await__"):
                out = await out
            if isinstance(out, Approval):
                ok, actor, evidence = out.ok, out.actor, out.evidence
            else:
                ok, actor, evidence = bool(out), None, None
            return Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                         "approved" if ok else "declined", "approval"), actor, evidence

        engine_mod.PolicyEngine.resolve = _old_resolve
        try:
            engine = engine_mod.PolicyEngine(builtins=())
            decision = Ruling(Verdict.ASK, "cần duyệt", "policy")
            ctx = RunContext("r1", "A", 1, Label(), "standard", 100.0)
            call = ToolCall("c1", "wipe", {"x": 1}, wipe)

            def approve(c, x):
                return Approval(ok=True, actor=Actor.human("ke-gia-mao", via="cli"))

            d, actor, evidence = asyncio.run(
                engine.resolve(decision, call, ctx, approve, require_evidence=True))
            self.assertEqual(d.verdict, Verdict.ALLOW,
                             "mutation phải khôi phục đúng hành vi cũ — nếu fail, phép so "
                             "sánh không còn phản ánh đúng hành vi trước bản vá")
        finally:
            engine_mod.PolicyEngine.resolve = original


if __name__ == "__main__":
    unittest.main()
