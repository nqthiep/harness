"""N-9 (07-risks-and-open-issues.md, found while updating docs against
`tests/test_roadmap.py`'s pre-registered S-03): `Agent.tenant_id` existed since T-8.1
but was never threaded down into the object `Policy.check(call, ctx)` actually receives
— a multi-tenant deployment could STAMP events with a tenant but could not WRITE A
POLICY that decides differently per tenant. Fixed on both backends:
`dispatch.py::RunContext.tenant_id` and `lg/runtime.py::_Ctx.tenant_id`.
"""
import unittest

from harness import Agent, Ruling, Verdict, tool
from harness.lg import build_agent
from harness.models.fake import FakeModel


class TenantAwarePolicy:
    name = "tenant-gate"

    def check(self, call, ctx):
        if ctx.tenant_id != "acme":
            return Ruling(Verdict.DENY, f"tenant {ctx.tenant_id!r} not allowed", self.name)
        return Ruling(Verdict.ALLOW, "", self.name)


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


class ClassicBackend(unittest.TestCase):
    def test_policy_doc_dung_tenant_dung(self):
        agent = Agent(name="A", job="j", tools=[look], budget="$5", tenant_id="acme",
                      policies=[TenantAwarePolicy()],
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("d")]))
        r = agent.try_run("hi")
        self.assertEqual(r.tools_run, ("look",))

    def test_policy_tu_choi_tenant_khac(self):
        agent = Agent(name="A", job="j", tools=[look], budget="$5", tenant_id="other",
                      policies=[TenantAwarePolicy()],
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("d")]))
        r = agent.try_run("hi")
        self.assertEqual(r.tools_run, ())

    def test_mutation_khong_noi_tenant_id_ca_hai_tenant_giong_nhau(self):
        """Mutation: dựng `RunContext` KHÔNG truyền `tenant_id` (hành vi trước bản vá)
        — policy nhìn thấy `None` bất kể `Agent.tenant_id` là gì, nên hai tenant khác
        nhau nhận CÙNG một verdict — đúng lỗ hổng N-9 mô tả."""
        from harness.dispatch import RunContext
        from harness.policy.label import Label

        ctx_before_fix = RunContext("r1", "A", 0, Label(), "standard", 100.0)  # thiếu tenant_id
        policy = TenantAwarePolicy()
        d_acme = policy.check(None, ctx_before_fix)
        d_other = policy.check(None, ctx_before_fix)
        self.assertEqual(d_acme.verdict, d_other.verdict,
                         "mutation (RunContext không mang tenant_id) phải cho hai "
                         "'tenant' đọc ra CÙNG verdict (cả hai đều None) — nếu chúng "
                         "khác, test này không còn phân biệt được bản đúng và bản có lỗi")
        self.assertIsNone(ctx_before_fix.tenant_id)


class LangGraphBackend(unittest.TestCase):
    def _run(self, *, tenant_id):
        from fake_chat import FakeChat
        model = FakeChat(script=[FakeChat.call("look", {"x": 1}), FakeChat.text("d")])
        graph, _rt = build_agent(model=model, tools=[look], budget="$5",
                                 tenant_id=tenant_id,
                                 policies=[TenantAwarePolicy])   # class, not instance — S-15
        return graph.invoke({"messages": [{"role": "user", "content": "hi"}]},
                            config={"configurable": {"thread_id": f"t-{tenant_id}"}})

    def test_policy_doc_dung_tenant_dung(self):
        out = self._run(tenant_id="acme")
        denied = [m for m in out.get("messages", [])
                 if "denied" in str(getattr(m, "content", "")).lower()]
        self.assertEqual(denied, [])

    def test_policy_tu_choi_tenant_khac(self):
        out = self._run(tenant_id="other")
        denied = [m for m in out.get("messages", [])
                 if "denied" in str(getattr(m, "content", "")).lower()]
        self.assertEqual(len(denied), 1)


if __name__ == "__main__":
    unittest.main()
