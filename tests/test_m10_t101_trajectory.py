"""T-10.1 — trajectory contract, docs/17-research-alignment.md M10 / S-05."""
import asyncio
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.testing import Trajectory


def _collect(agent):
    events = []

    class _C:
        def emit(self, e): events.append(e)
        def close(self): pass

    return agent.with_(exporters=tuple(agent.exporters) + (_C(),)), events


@tool(effect="write")
def save(x: int) -> str:
    """Lưu một số."""
    return "saved"


@tool(effect="danger")
def wipe() -> str:
    """Nguy hiểm."""
    return "wiped"


def _run(agent, message):
    return asyncio.run(agent.atry_run(message))


class MustCallMustNotCall(unittest.TestCase):
    def test_must_call_satisfied(self):
        a = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                  provider=FakeModel([FakeModel.tool_call("save", {"x": 1}),
                                      FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(must_call=frozenset({"save"})).check(r)
        self.assertTrue(report.ok, report)

    def test_must_call_violated(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(must_call=frozenset({"save"})).check(r)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "must_call")

    def test_must_not_call_violated(self):
        a = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                  provider=FakeModel([FakeModel.tool_call("save", {"x": 1}),
                                      FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(must_not_call=frozenset({"save"})).check(r)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "must_not_call")


class Ceilings(unittest.TestCase):
    def test_max_model_calls_violated(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(max_model_calls=0).check(r)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "max_model_calls")

    def test_max_tokens_violated(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(max_tokens=1).check(r)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "max_tokens")

    def test_max_cost_violated(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(max_cost_usd=-1.0).check(r)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "max_cost_usd")

    def test_ceilings_pass_when_within_bounds(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(max_model_calls=10, max_tokens=10_000,
                           max_cost_usd=999.0).check(r)
        self.assertTrue(report.ok, report)


class OutputSchema(unittest.TestCase):
    def test_violated_when_value_is_wrong_type(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(output_schema=str).check(r)          # result.value is None
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "output_schema")

    def test_passes_when_value_matches(self):
        a = Agent(name="T", job="j", model="fake", budget="$5",
                  provider=FakeModel([FakeModel.text("ok")]))
        r = _run(a, "go")
        report = Trajectory(output_schema=type(None)).check(r)
        self.assertTrue(report.ok, report)


class RequiresApproval(unittest.TestCase):
    def test_needs_events(self):
        a = Agent(name="T", job="j", model="fake", tools=[wipe], budget="$5",
                  approve=lambda call, ctx: True,
                  provider=FakeModel([FakeModel.tool_call("wipe", {}), FakeModel.text("ok")]))
        r = _run(a, "go")
        with self.assertRaises(ValueError):
            Trajectory(requires_approval=frozenset({"wipe"})).check(r)     # events=() thiếu

    def test_satisfied_when_asked(self):
        agent = Agent(name="T", job="j", model="fake", tools=[wipe], budget="$5",
                      approve=lambda call, ctx: True,
                      provider=FakeModel([FakeModel.tool_call("wipe", {}),
                                          FakeModel.text("ok")]))
        collected, events = _collect(agent)
        r = asyncio.run(collected.atry_run("go"))
        report = Trajectory(requires_approval=frozenset({"wipe"})).check(r, events)
        self.assertTrue(report.ok, report)

    def test_violated_when_never_asked(self):
        """`save` (write) không cần approval mặc định — gọi được mà không qua ASK nào."""
        agent = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("save", {"x": 1}),
                                          FakeModel.text("ok")]))
        collected, events = _collect(agent)
        r = asyncio.run(collected.atry_run("go"))
        report = Trajectory(requires_approval=frozenset({"save"})).check(r, events)
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "requires_approval")


class NoDuplicateSideEffects(unittest.TestCase):
    def test_needs_effect_of(self):
        agent = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("save", {"x": 1}),
                                          FakeModel.text("ok")]))
        collected, events = _collect(agent)
        r = asyncio.run(collected.atry_run("go"))
        with self.assertRaises(ValueError):
            Trajectory(no_duplicate_side_effects=True).check(r, events)     # effect_of thiếu

    def test_violated_across_two_steps_same_args(self):
        agent = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("save", {"x": 1}, call_id="c1"),
                                          FakeModel.tool_call("save", {"x": 1}, call_id="c2"),
                                          FakeModel.text("ok")]))
        collected, events = _collect(agent)
        r = asyncio.run(collected.atry_run("go"))
        report = Trajectory(no_duplicate_side_effects=True).check(
            r, events, effect_of={"save": "write"})
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].rule, "no_duplicate_side_effects")

    def test_passes_when_args_differ(self):
        agent = Agent(name="T", job="j", model="fake", tools=[save], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("save", {"x": 1}, call_id="c1"),
                                          FakeModel.tool_call("save", {"x": 2}, call_id="c2"),
                                          FakeModel.text("ok")]))
        collected, events = _collect(agent)
        r = asyncio.run(collected.atry_run("go"))
        report = Trajectory(no_duplicate_side_effects=True).check(
            r, events, effect_of={"save": "write"})
        self.assertTrue(report.ok, report)


if __name__ == "__main__":
    unittest.main()
