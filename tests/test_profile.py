"""`Agent.with_profile()` / `profile.py::Profile` — a profile may extend an agent, never
loosen it. Each loosening case below is the negative half of a positive fact:
`_refuse_if_loosened` (agent.py) reads six knobs off `before`/`after` and refuses if any
moved in the unsafe direction; a test per knob is what proves the check actually looks at
that knob, not merely that the function exists and returns without error on the happy
path (the R-16 lesson this whole repository is built around: a control that is specified
and never executed is not a control).
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, ProfileLoosenedSafetyError, tool
from harness.errors import DuplicateToolError
from harness.policy.base import Ruling, ToolCall, Verdict


@tool(effect="read")
def ping() -> str:
    """ping"""
    return "pong"


@tool(effect="external")
def fetch(url: str) -> str:
    """fetch"""
    return url


@tool(effect="danger")
def deploy() -> str:
    """deploy"""
    return "deployed"


class _AddsAReadTool:
    name = "adds-read-tool"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(tools=[*agent.toolset, ping])


class _NoOpPolicy:
    name = "no-op"

    def check(self, call: ToolCall, ctx: object) -> Ruling:
        return Ruling(Verdict.ALLOW, "no-op", self.name)


class _AddsAPolicy:
    name = "adds-a-policy"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(policies=[*agent.policies, _NoOpPolicy()])


class _TightensSafety:
    name = "tightens-safety"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(safety="strict")


class WithProfileExtends(unittest.TestCase):
    def test_returns_a_new_agent_with_the_tool_added(self):
        base = Agent(name="A", job="hi")
        out = base.with_profile(_AddsAReadTool())
        self.assertIsNot(base, out)
        self.assertEqual(len(base.toolset), 0)
        self.assertEqual({t.name for t in out.toolset}, {"ping"})

    def test_a_policy_addition_is_allowed(self):
        base = Agent(name="A", job="hi")
        out = base.with_profile(_AddsAPolicy())
        self.assertEqual(len(out.policies), len(base.policies) + 1)

    def test_tightening_safety_is_allowed(self):
        base = Agent(name="A", job="hi", safety="standard")
        out = base.with_profile(_TightensSafety())
        self.assertEqual(out.safety, "strict")

    def test_reapplying_a_profile_that_adds_named_tools_hits_the_existing_duplicate_guard(self):
        """No special "already applied" bookkeeping exists for `Profile` — this is why:
        the ordinary tool-name guard `ToolSet` already runs on every `Agent(**base)`
        construction fires on a second application, for free."""
        base = Agent(name="A", job="hi")
        once = base.with_profile(_AddsAReadTool())
        with self.assertRaises(DuplicateToolError):
            once.with_profile(_AddsAReadTool())


class _Loosens(unittest.TestCase):
    """One profile per knob `_refuse_if_loosened` checks, each changing ONLY that knob —
    so a profile that fails here proves the check reads that specific field, not some
    other one that happened to also differ."""

    def _refused(self, base: Agent, profile) -> str:
        with self.assertRaises(ProfileLoosenedSafetyError) as ctx:
            base.with_profile(profile)
        return str(ctx.exception)

    def test_downgrading_safety(self):
        class P:
            name = "downgrades-safety"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(safety="standard")

        base = Agent(name="A", job="hi", safety="strict")
        msg = self._refused(base, P())
        self.assertIn("safety", msg)

    def test_expanding_accepts_tainted(self):
        class P:
            name = "expands-accepts-tainted"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(accepts_tainted=["deploy"])

        base = Agent(name="A", job="hi", tools=[fetch, deploy],
                     accepts_tainted=["deploy"])            # already granted -> untouched
        out = base.with_profile(P())                        # same set: not a loosening
        self.assertEqual({t.name for t in out.toolset}, {t.name for t in base.toolset})

        base_without_grant = Agent(name="A", job="hi")       # no tools, no prior grant
        msg = self._refused(base_without_grant, P())
        self.assertIn("accepts_tainted", msg)

    def test_widening_allowed_hosts(self):
        class P:
            name = "widens-allowed-hosts"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(allowed_hosts=["api.example.com", "evil.example.com"])

        base = Agent(name="A", job="hi", allowed_hosts=["api.example.com"])
        msg = self._refused(base, P())
        self.assertIn("allowed_hosts", msg)

    def test_removing_the_allowlist_entirely(self):
        class P:
            name = "removes-allowlist"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(allowed_hosts=None)       # None == unrestricted egress

        base = Agent(name="A", job="hi", allowed_hosts=["api.example.com"])
        msg = self._refused(base, P())
        self.assertIn("allowed_hosts", msg)

    def test_turning_off_require_approval_evidence(self):
        class P:
            name = "turns-off-approval-evidence"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(require_approval_evidence=False)

        base = Agent(name="A", job="hi", require_approval_evidence=True)
        msg = self._refused(base, P())
        self.assertIn("require_approval_evidence", msg)

    def test_raising_the_approval_fatigue_cap(self):
        class P:
            name = "raises-max-asks"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(max_asks_per_run=agent.max_asks_per_run * 10)

        base = Agent(name="A", job="hi", max_asks_per_run=5)
        msg = self._refused(base, P())
        self.assertIn("max_asks_per_run", msg)

    def test_replacing_the_approval_callback_with_none(self):
        class P:
            name = "drops-approve"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(approve=None)

        base = Agent(name="A", job="hi", approve=lambda call: True)
        msg = self._refused(base, P())
        self.assertIn("approve", msg)

    def test_dropping_an_existing_policy(self):
        class P:
            name = "drops-a-policy"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(policies=[])

        base = Agent(name="A", job="hi", policies=[_NoOpPolicy()])
        msg = self._refused(base, P())
        self.assertIn("policies", msg)


if __name__ == "__main__":
    unittest.main()
