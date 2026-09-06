"""`build_agent(policies=...)` refuses a bad factory at construction — F11.

`lg/__init__.py` used to check one thing: that no entry was a policy INSTANCE.  Measured
against `RequireBeforePolicy(tool=..., requires=...)`, a parameterised built-in the
library ships and `docs/06-safety.md:202` shows:

    instance                          ConfigError, saying to pass the CLASS instead
    bare class (that advice, exactly) TypeError: __init__() missing 2 required
                                      keyword-only arguments — raised from library
                                      internals, on the first tool call
    functools.partial(...)            works
    lambda: RequireBeforePolicy(...)  works

Four defects at once.  The advice was wrong for every parameterised policy shipped; the
resulting failure was a raw `TypeError`, not a `ConfigError`; it was LAZY, because
`Runtime._engine_for` builds factories on the first tool REQUEST, so a run that called no
tool finished normally and the misconfiguration was never seen; and the two spellings that
do work appeared in the docs and the tests but not in the message a caller actually hits.

The laziness is the part that matters most: it inverts `docs/02-architecture.md:311` —
"Configuration error — Raised at `Agent(...)` construction or at `@tool` import. Never at
run time."

These tests are written against the observable behaviour (which exception, from which
call), not against message substrings, except where the point IS the message — and there
the printed spelling is EXECUTED rather than matched, since a message that prints
something that does not run is exactly the bug being fixed.
"""
from __future__ import annotations

import functools
import re
import unittest

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import ConfigError, tool
from harness.lg import build_agent
from harness.policy.builtin import RequireBeforePolicy

_VIETNAMESE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]")


@tool(effect="read")
def consult_advisor(question: str) -> str:
    """Ask the advisor."""
    return "an opinion"


@tool(effect="danger")
def wipe(x: int) -> str:
    """Wipe it."""
    RAN.append("wipe")
    return "gone"


RAN: list[str] = []


def _build(policies, script=None):
    return build_agent(model=FakeChat(script=script or [FakeChat.text("done")]),
                       tools=[consult_advisor, wipe], budget="$5",
                       checkpointer=MemorySaver(), policies=policies,
                       approve=lambda c, x: True)


class BadFactoriesAreRefusedAtConstruction(unittest.TestCase):

    def test_an_instance_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            _build([RequireBeforePolicy(tool="wipe", requires="consult_advisor")])
        self.assertIn("RequireBeforePolicy", str(caught.exception))

    def test_a_bare_parameterised_class_is_refused_at_construction(self):
        """The regression that matters: the OLD message's own advice, followed exactly.

        Before this fix `build_agent()` accepted it and `TypeError` came out of
        `Runtime._engine_for` on the first tool call.
        """
        with self.assertRaises(ConfigError):
            _build([RequireBeforePolicy])

    def test_the_bare_class_no_longer_produces_a_bare_TypeError(self):
        try:
            _build([RequireBeforePolicy])
        except ConfigError:
            pass
        except TypeError:                                   # pragma: no cover
            self.fail("still a raw TypeError from library internals, not a ConfigError")

    def test_a_run_that_calls_no_tool_can_no_longer_hide_it(self):
        """The laziness. `_engine_for` runs on the first tool REQUEST, so this script —
        one text turn, no tool call — used to finish `stop=completed` with the broken
        factory never built. Construction now refuses before any run is possible."""
        with self.assertRaises(ConfigError):
            _build([RequireBeforePolicy], script=[FakeChat.text("no tools needed")])

    def test_a_factory_that_raises_is_a_config_error(self):
        class Boom:
            def __init__(self): raise RuntimeError("no config file")
            def check(self, call, ctx): ...                 # pragma: no cover

        with self.assertRaises(ConfigError) as caught:
            _build([Boom])
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_a_factory_that_does_not_build_a_policy_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            _build([lambda: 42])
        self.assertIn("check()", str(caught.exception))


class TheMessageIsUsable(unittest.TestCase):

    def _message(self, policy) -> str:
        with self.assertRaises(ConfigError) as caught:
            _build([policy])
        return str(caught.exception)

    def test_the_spellings_the_message_prints_actually_build_an_agent(self):
        """Execute what the message says to type. `...` is real Python (`Ellipsis`), so
        the printed line is runnable as printed — which is the whole claim being made."""
        for offender in (RequireBeforePolicy(tool="wipe", requires="consult_advisor"),
                         RequireBeforePolicy):
            printed = re.findall(r"policies=\[(.+?)\]\n", self._message(offender))
            self.assertEqual(len(printed), 2, f"expected two spellings, got {printed}")
            for spelling in printed:
                with self.subTest(spelling=spelling):
                    factory = eval(spelling, {"RequireBeforePolicy": RequireBeforePolicy,
                                              "functools": functools})
                    graph, _rt = _build([factory])          # must not raise
                    self.assertIsNotNone(graph)

    def test_the_message_never_recommends_the_bare_class_for_a_parameterised_policy(self):
        """The original defect in one line: it said `policies=[RequireBeforePolicy]`."""
        for offender in (RequireBeforePolicy(tool="wipe", requires="consult_advisor"),
                         RequireBeforePolicy):
            self.assertNotIn("policies=[RequireBeforePolicy]", self._message(offender))

    def test_the_messages_are_english(self):
        """9 of 108 raise sites in `src/` carried a Vietnamese message; this was one."""
        for offender in (RequireBeforePolicy(tool="wipe", requires="consult_advisor"),
                         RequireBeforePolicy, lambda: 42):
            found = _VIETNAMESE.findall(self._message(offender))
            self.assertEqual(found, [], f"Vietnamese left in the message: {found}")


class GoodFactoriesStillWork(unittest.TestCase):
    """The refusal must not have been bought by breaking the working spellings."""

    def setUp(self):
        RAN.clear()

    def _run(self, factory, thread):
        graph, _rt = _build([factory], script=[FakeChat.call("wipe", {"x": 1}),
                                               FakeChat.text("done")])
        graph.invoke({"messages": [HumanMessage("go")], "step": 0},
                     {"configurable": {"thread_id": thread}})

    def test_partial_still_builds_and_still_denies(self):
        self._run(functools.partial(RequireBeforePolicy, tool="wipe",
                                    requires="consult_advisor"), "t-partial")
        self.assertEqual(RAN, [], "the gate did not deny an un-advised `wipe`")

    def test_lambda_still_builds_and_still_denies(self):
        self._run(lambda: RequireBeforePolicy(tool="wipe", requires="consult_advisor"),
                  "t-lambda")
        self.assertEqual(RAN, [], "the gate did not deny an un-advised `wipe`")

    def test_a_stateless_class_with_no_arguments_is_still_accepted(self):
        class AllowAll:
            name = "allow-all"
            def check(self, call, ctx):
                from harness import Ruling, Verdict
                return Ruling(Verdict.ALLOW, "", self.name)

        graph, _rt = _build([AllowAll])
        self.assertIsNotNone(graph)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
