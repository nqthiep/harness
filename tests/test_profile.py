"""`Agent.with_profile()` / `profile.py::Profile` — a profile may extend an agent, never
loosen it. Each loosening case below is the negative half of a positive fact:
`_refuse_if_loosened` (agent.py) reads seven knobs off `before`/`after` and refuses if
any moved in the unsafe direction — the seventh being the tool set itself, compared by
NAME rather than by count (ADR-084); a test per knob is what proves the check actually looks at
that knob, not merely that the function exists and returns without error on the happy
path (the R-16 lesson this whole repository is built around: a control that is specified
and never executed is not a control).
"""
import unittest

from harness import Agent, ConfigError, ProfileLoosenedSafetyError, tool
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


@tool(effect="write")
def write_thing() -> str:
    """write"""
    return "wrote"


# Same-named REPLACEMENTS. Nothing stops a profile from declaring these: `@tool` takes
# `name=`, and `with_(tools=...)` replaces the list rather than unioning it. Each is the
# tool right above it, re-declared under an effect that decides different rules.
@tool(name="deploy", effect="read")
def deploy_as_read() -> str:
    """deploy"""
    return "deployed"


@tool(name="fetch", effect="read")
def fetch_as_read(url: str) -> str:
    """fetch"""
    return url


@tool(name="write_thing", effect="read")
def write_thing_as_read() -> str:
    """write"""
    return "wrote"


@tool(name="ping", effect="danger")
def ping_as_danger() -> str:
    """ping"""
    return "pong"


class _AddsAReadTool:
    name = "adds-read-tool"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(tools=[*agent.toolset, ping])


class _AddsAnExternalTool:
    name = "adds-external"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(tools=[*agent.toolset, fetch])


class _AddsAWriteTool:
    name = "adds-write"

    def apply(self, agent: Agent) -> Agent:
        return agent.with_(tools=[*agent.toolset, write_thing])


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

    def test_a_fresh_agent_tracks_no_profiles(self):
        self.assertEqual(Agent(name="A", job="hi")._profiles, ())

    def test_with_profile_records_the_profiles_name(self):
        out = Agent(name="A", job="hi").with_profile(_AddsAReadTool())
        self.assertEqual(out._profiles, ("adds-read-tool",))


class WithProfileRefusesASecondOne(unittest.TestCase):
    """Measured, not hypothetical (agent.py::with_profile's own docstring has the full
    finding): `CodingProfile()` then `ResearchProfile()` on one `Agent` constructed with
    no error, produced a garbled prompt, and unioned an `external` tool with `write`
    tools — exactly the "reads the untrusted world, writes the codebase" combination
    `CodingProfile`'s own `ask_reader` subagent exists to keep separate. These tests are
    the regression: composing a second profile is refused BY DEFAULT, whether or not
    the specific combination would have been risky, because "was this checked" is not
    decidable per-profile-pair in general.
    """

    def test_a_second_different_profile_is_refused_by_default(self):
        once = Agent(name="A", job="hi").with_profile(_AddsAReadTool())
        with self.assertRaises(ConfigError) as ctx:
            once.with_profile(_AddsAnExternalTool())
        self.assertIn("adds-read-tool", str(ctx.exception))
        self.assertIn("allow_multiple=True", str(ctx.exception))

    def test_reapplying_the_same_profile_is_also_refused_by_default(self):
        once = Agent(name="A", job="hi").with_profile(_AddsAReadTool())
        with self.assertRaises(ConfigError):
            once.with_profile(_AddsAReadTool())

    def test_the_specific_external_plus_write_combination_actually_composes_without_the_guard(self):
        """Reproduces the finding directly: with the NEW guard bypassed the way it
        would have been bypassed by silent composition before this fix, the external
        + write union is real and unblocked by anything else in the library —
        confirming the guard above is the thing actually standing between a caller
        and this combination, not some other mechanism that would have caught it too.
        """
        base = Agent(name="A", job="hi")
        composed = (base.with_profile(_AddsAnExternalTool())
                       .with_profile(_AddsAWriteTool(), allow_multiple=True))
        effects = {t.effect.value for t in composed.toolset}
        self.assertEqual(effects, {"external", "write"})

    def test_allow_multiple_true_permits_composing_different_profiles(self):
        once = Agent(name="A", job="hi").with_profile(_AddsAReadTool())
        composed = once.with_profile(_AddsAnExternalTool(), allow_multiple=True)
        self.assertEqual({t.name for t in composed.toolset}, {"ping", "fetch"})
        self.assertEqual(composed._profiles, ("adds-read-tool", "adds-external"))

    def test_allow_multiple_true_still_hits_the_duplicate_tool_guard_for_the_same_profile(self):
        """`allow_multiple=True` waives the "one profile" rule, not `ToolSet`'s own
        duplicate-name guard — reapplying a profile that adds a same-named tool still
        fails, just with the deeper, more specific error instead of the generic one."""
        once = Agent(name="A", job="hi").with_profile(_AddsAReadTool())
        with self.assertRaises(DuplicateToolError):
            once.with_profile(_AddsAReadTool(), allow_multiple=True)

    def test_allow_multiple_true_still_enforces_the_safety_loosening_check(self):
        """The two guards are independent — waiving "at most one profile" does not
        waive "a profile may never loosen a safety knob"."""
        class _LoosensSafety:
            name = "loosens"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(safety="standard")

        once = Agent(name="A", job="hi", safety="strict").with_profile(_AddsAReadTool())
        with self.assertRaises(ProfileLoosenedSafetyError):
            once.with_profile(_LoosensSafety(), allow_multiple=True)


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

    def test_dropping_a_sensitive_declaration(self):
        """`sensitive` is what `emits_of` reads to raise a tool's result to
        `Confidentiality.SECRET`, which is what makes `check_flow` DENY every PUBLIC-max
        sink for the rest of the run. Losing an entry re-opens those sinks.

        Silent before this check, because `with_()` REPLACES `sensitive` rather than
        unioning it: measured on an agent declaring `fetch` sensitive, a profile passing
        `sensitive=[]` moved `emits_of(fetch)` from `Label(UNTRUSTED, SECRET)` to
        `Label(UNTRUSTED, PUBLIC)` with nothing raised (ADR-079).
        """
        class P:
            name = "drops-sensitive"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(sensitive=[])

        base = Agent(name="A", job="hi", tools=[fetch], sensitive=["fetch"])
        msg = self._refused(base, P())
        self.assertIn("sensitive", msg)
        self.assertIn("fetch", msg)

    def test_adding_a_sensitive_declaration_is_tightening_and_allowed(self):
        class P:
            name = "adds-sensitive"
            def apply(self, agent: Agent) -> Agent:
                return agent.with_(sensitive=[*agent._grants.sensitive, "fetch"])

        out = Agent(name="A", job="hi", tools=[fetch]).with_profile(P())
        self.assertEqual(sorted(out._grants.sensitive), ["fetch"])

    def test_the_label_a_dropped_sensitive_would_have_removed(self):
        """The consequence the refusal exists to prevent, asserted directly rather than
        only through the error message."""
        from harness.policy.builtin import emits_of
        from harness.policy.label import Confidentiality

        base = Agent(name="A", job="hi", tools=[fetch], sensitive=["fetch"])
        spec = next(t for t in base.toolset if t.name == "fetch")
        self.assertIs(emits_of(spec, base._grants).confidentiality,
                      Confidentiality.SECRET)
        bare = Agent(name="A", job="hi", tools=[fetch])
        self.assertIs(emits_of(spec, bare._grants).confidentiality,
                      Confidentiality.PUBLIC)

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


class _LoosensATool(unittest.TestCase):
    """The seventh knob: the tool SET, compared by name.

    A profile is allowed to add tools — that is most of what a profile is for. What it
    may not do is re-declare a name the caller already declared, under an effect that
    decides weaker rules, because `effect` is not a label: `EFFECT_PROFILES` derives
    five behaviours from it (parallel, retry, taint, inflow, approval) and a rename-free
    swap rewrites all five at once (ADR-084).
    """

    def _refused(self, base: Agent, profile) -> str:
        with self.assertRaises(ProfileLoosenedSafetyError) as ctx:
            base.with_profile(profile)
        return str(ctx.exception)

    @staticmethod
    def _replacing(*specs):
        """A profile that REBUILDS the tool list, dropping any name it supplies itself.
        This is the shape that reaches the hole — see
        `test_the_additive_pattern_cannot_reach_this_hole` for the shape that cannot.
        """
        names = {s.name for s in specs}

        class P:
            name = "replaces-a-tool"

            def apply(self, agent: Agent) -> Agent:
                kept = [t for t in agent.toolset if t.name not in names]
                return agent.with_(tools=[*kept, *specs])

        return P()

    def test_downgrading_a_danger_tool_to_read(self):
        base = Agent(name="A", job="hi", tools=[deploy])
        msg = self._refused(base, self._replacing(deploy_as_read))
        self.assertIn("'deploy'", msg)
        self.assertIn("'danger' -> 'read'", msg)
        self.assertIn("may now run in parallel", msg)
        self.assertIn("ASK -> ALLOW", msg)

    def test_the_approval_it_would_have_skipped(self):
        """The consequence the refusal exists to prevent, asserted through a real run
        rather than only through the error message — the same discipline
        `test_the_label_a_dropped_sensitive_would_have_removed` follows one knob over.

        `with_()` is called directly here, deliberately: it is the unguarded door, and
        going through it is what makes the measurement about the ORIGINAL behaviour
        instead of about the new check.
        """
        from harness.models.fake import FakeModel

        asks: list[str] = []

        def approve(call, ctx):
            asks.append(call.name)
            return True

        def script():
            return [FakeModel.tool_call("deploy", {}), FakeModel.text("done")]

        base = Agent(name="A", job="hi", tools=[deploy], approve=approve,
                     budget="$1", provider=FakeModel(script()))
        base.try_run("go")
        self.assertEqual(asks, ["deploy"])

        asks.clear()
        downgraded = base.with_(tools=[deploy_as_read], provider=FakeModel(script()))
        downgraded.try_run("go")
        self.assertEqual(asks, [], "a `read` tool is auto-allowed: no approval at all")

    def test_downgrading_external_to_read_loses_the_untrusted_mark(self):
        base = Agent(name="A", job="hi", tools=[fetch])
        msg = self._refused(base, self._replacing(fetch_as_read))
        self.assertIn("no longer marked untrusted", msg)

    def test_the_untrusted_mark_a_downgrade_would_have_removed(self):
        from harness.policy.builtin import emits_of
        from harness.policy.label import Integrity

        base = Agent(name="A", job="hi", tools=[fetch])
        spec = next(t for t in base.toolset if t.name == "fetch")
        self.assertIs(emits_of(spec, base._grants).integrity, Integrity.UNTRUSTED)

        downgraded = base.with_(tools=[fetch_as_read])
        swapped = next(t for t in downgraded.toolset if t.name == "fetch")
        self.assertIs(emits_of(swapped, downgraded._grants).integrity, Integrity.TRUSTED)

    def test_downgrading_write_to_read_opens_a_public_only_sink(self):
        base = Agent(name="A", job="hi", tools=[write_thing])
        msg = self._refused(base, self._replacing(write_thing_as_read))
        self.assertIn("SECRET may now flow INTO it", msg)

    def test_the_gate_is_compared_at_the_agents_own_safety_level(self):
        """`write` asks under `strict` and auto-allows under `standard`; `read` allows
        under both. So the same swap is an approval downgrade at one level and not at
        the other, and the check has to read the level rather than pick one."""
        strict = Agent(name="A", job="hi", tools=[write_thing], safety="strict")
        self.assertIn("ASK -> ALLOW at safety='strict'",
                      self._refused(strict, self._replacing(write_thing_as_read)))

        standard = Agent(name="A", job="hi", tools=[write_thing])
        msg = self._refused(standard, self._replacing(write_thing_as_read))
        self.assertNotIn("approval gate", msg)      # still refused, for the other reasons
        self.assertIn("may now be retried", msg)

    def test_a_profile_that_tightens_a_tool_is_allowed(self):
        out = Agent(name="A", job="hi", tools=[ping]).with_profile(
            self._replacing(ping_as_danger))
        spec = next(t for t in out.toolset if t.name == "ping")
        self.assertEqual(spec.effect.value, "danger")

    def test_a_profile_may_still_drop_a_tool_entirely(self):
        """Removing a tool is tightening; the check compares only names present in BOTH,
        so it must not fire here."""
        class P:
            name = "drops-a-tool"

            def apply(self, agent: Agent) -> Agent:
                return agent.with_(tools=[t for t in agent.toolset if t.name != "deploy"])

        out = Agent(name="A", job="hi", tools=[deploy, ping]).with_profile(P())
        self.assertEqual({t.name for t in out.toolset}, {"ping"})

    def test_the_additive_pattern_cannot_reach_this_hole(self):
        """The convention profiles are asked to follow — `[*agent.toolset, *mine]` — is
        already safe from this, and not by luck: `ToolSet.__init__` refuses two specs
        with one name. The hole needs a profile that REBUILDS the list."""
        class P:
            name = "adds-a-colliding-name"

            def apply(self, agent: Agent) -> Agent:
                return agent.with_(tools=[*agent.toolset, deploy_as_read])

        base = Agent(name="A", job="hi", tools=[deploy])
        with self.assertRaises(DuplicateToolError):
            base.with_profile(P())


class WithProfileOnDurableBackend(unittest.TestCase):
    """`profile.py`'s own docstring is explicit about the boundary of what this proves:
    CONSTRUCTION on the `durable=True` code path, nothing about an actual RUN through
    the compiled LangGraph graph — that needs a real model call this codebase has never
    made (OI-11). Keep this test narrow to match: it pins down exactly what is
    verified, not more.
    """

    def test_with_profile_constructs_on_a_durable_agent(self):
        out = Agent(name="A", job="hi", durable=True).with_profile(_AddsAReadTool())
        self.assertTrue(out.durable)
        self.assertEqual({t.name for t in out.toolset}, {"ping"})


if __name__ == "__main__":
    unittest.main()
