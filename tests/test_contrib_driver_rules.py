"""What `contrib.Driver`'s four safety rules actually enforce — F16.

`contrib/driver.py`'s header used to say "Each is enforced here, not just documented".
Measured against the code, that was true of rules 1 and 4, half-true of rule 2 and false
of rule 3:

  rule 2  `_may_preempt()` reads `self.write_in_flight is not None and .busy`, and
          `write_in_flight` defaults to `None`. So the default
          `Driver(agent, sensors=[...])` answered `(True, '')` with a real write in
          flight — it cancels mid-write. `WriteInFlight.names_from(agent)` existed at
          `:288` and `Driver` never called it.
  rule 3  `names = {t.name for t in agent.toolset}; if names & PLAN_TOOLS: return` — it
          accepts an agent whose only "durable plan" is a tool named `list_tasks` that
          stores nothing.

These tests pin BOTH the mechanism that was added and the gap that remains, because a
test suite that only covered the good news would leave the same illusion the docstring
did.  In particular `test_the_default_driver_still_cancels_mid_write` asserts the DEFECT:
it is the one that must be deleted the day `require_write_guard` defaults to `True`.
"""
from __future__ import annotations

import asyncio
import unittest

from harness import Agent, ConfigError, Effect, tool
from harness.contrib.driver import PLAN_TOOLS, Driver, WriteInFlight
from harness.middleware import with_middleware
from harness.models.fake import FakeModel


@tool(effect=Effect.WRITE)
async def slow_write(path: str) -> str:
    """Write, slowly."""
    await asyncio.sleep(0.05)
    return "written"


@tool(effect=Effect.DANGER)
async def wipe(path: str) -> str:
    """Wipe it."""
    return "gone"


@tool(effect=Effect.READ)
def look() -> str:
    """Look around."""
    return "nothing much"


@tool(effect=Effect.READ)
def list_tasks() -> str:
    """A plan tool in name only — it stores nothing at all."""
    return "1. doing something long"


def _agent(tools=(look, list_tasks, slow_write), script=None):
    return Agent(name="a", job="j", tools=list(tools),
                 provider=FakeModel(script=script or [{"text": "ok"}]))


class _Call:
    kwargs: dict = {}
    result = "x"

    def __init__(self, name: str) -> None:
        self.name = name


class Rule2(unittest.TestCase):

    def test_the_default_driver_still_cancels_mid_write(self):
        """THE DEFECT, pinned so it cannot be lost.

        Delete this test on the day `require_write_guard` defaults to True — and if it
        starts failing before then, rule 2 became the default and this file should say so.
        """
        agent = _agent()
        driver = Driver(agent, sensors=[])
        self.assertIsNone(driver.write_in_flight)

        guard = WriteInFlight(WriteInFlight.names_from(agent))
        guard.before_tool(_Call("slow_write"))
        self.assertTrue(guard.busy, "the guard itself does see the write")
        self.assertEqual(driver._may_preempt(), (True, ""),
                         "the default Driver preempts straight through a write")

    def test_a_wired_guard_stops_the_preemption(self):
        agent = _agent()
        guard = WriteInFlight(WriteInFlight.names_from(agent))
        driver = Driver(with_middleware(agent, guard), write_in_flight=guard)
        guard.before_tool(_Call("slow_write"))
        self.assertEqual(driver._may_preempt(), (False, "a write is in flight"))

    def test_the_refusal_is_available_and_names_the_wiring(self):
        with self.assertRaises(ConfigError) as caught:
            Driver(_agent(), require_write_guard=True)
        message = str(caught.exception)
        self.assertIn("WriteInFlight.names_from(agent)", message)
        self.assertIn("with_middleware(agent, wif)", message)
        self.assertIn("write_in_flight=wif", message)
        self.assertIn("slow_write", message, "it should name this agent's write tools")

    def test_the_refusal_stays_quiet_when_there_is_nothing_to_guard(self):
        """An agent that cannot change the world cannot be cancelled mid-write.  A
        refusal here would be noise, and noise is how a refusal gets switched off."""
        driver = Driver(_agent(tools=(look, list_tasks)), require_write_guard=True)
        self.assertIsNone(driver.write_in_flight)

    def test_the_refusal_can_be_waived_on_purpose(self):
        driver = Driver(_agent(), require_write_guard=False)
        self.assertIsNone(driver.write_in_flight)

    def test_a_guard_that_misses_a_tool_is_refused_whether_or_not_the_flag_is_on(self):
        """Always checked, not behind the flag: this one is exact, so it costs nothing."""
        agent = _agent(tools=(look, list_tasks, slow_write, wipe))
        partial = WriteInFlight(["slow_write"])                 # forgot `wipe`
        with self.assertRaises(ConfigError) as caught:
            Driver(agent, write_in_flight=partial)
        self.assertIn("wipe", str(caught.exception))

    def test_a_guard_that_covers_everything_is_accepted(self):
        agent = _agent(tools=(look, list_tasks, slow_write, wipe))
        full = WriteInFlight(WriteInFlight.names_from(agent))
        self.assertIsNotNone(Driver(agent, write_in_flight=full).write_in_flight)


class Rule3(unittest.TestCase):

    def test_an_agent_with_no_plan_tool_is_refused(self):
        with self.assertRaises(ConfigError):
            Driver(_agent(tools=(look, slow_write)))

    def test_a_plan_tool_that_stores_nothing_is_accepted(self):
        """THE GAP, pinned.  `list_tasks` above returns a hard-coded string; rule 3 is a
        match against three literals, so it passes.  The docstring now says exactly
        this — the test exists so the docstring cannot quietly stop being true."""
        self.assertIn("list_tasks", PLAN_TOOLS)
        driver = Driver(_agent(tools=(look, list_tasks, slow_write)))
        self.assertIsNotNone(driver)

    def test_the_check_is_a_name_match_and_nothing_else(self):
        """Any of the three names is enough, whatever the tool does."""
        def sham(name: str):
            src = (f'def {name}() -> str:\n'
                   f'    """Returns a constant. Stores nothing."""\n'
                   f'    return "[]"\n')
            ns: dict = {}
            exec(compile(src, "<sham>", "exec"), ns)
            return tool(effect=Effect.READ)(ns[name])

        for name in sorted(PLAN_TOOLS):
            with self.subTest(name=name):
                Driver(_agent(tools=(look, sham(name), slow_write)))

    def test_a_real_task_ledger_is_distinguishable_from_the_sham(self):
        """The stronger check the docstring says was measured and not adopted.

        Recorded as a test rather than as a claim: every real `TaskLedger` tool reaches a
        `Store` through its closure, and the sham `list_tasks` above reaches none.  This
        is what a persistence check WOULD rest on.
        """
        from harness.memory.inmemory import InMemoryStore
        from harness.tasks import TaskLedger

        def reaches_a_store(spec) -> bool:
            for cell in (spec.fn.__closure__ or ()):
                try:
                    held = cell.cell_contents
                except ValueError:                              # pragma: no cover
                    continue
                for candidate in (held, *(getattr(held, a, None)
                                          for a in vars(held) if hasattr(held, "__dict__"))):
                    if all(callable(getattr(candidate, m, None))
                           for m in ("get", "put", "delete", "search")):
                        return True
            return False

        real = TaskLedger(InMemoryStore()).tools()
        self.assertTrue(all(reaches_a_store(s) for s in real),
                        "every real TaskLedger tool should reach its Store")
        self.assertFalse(reaches_a_store(list_tasks),
                         "the sham should reach none — that is the distinction")


class TheDocstringMatchesTheCode(unittest.TestCase):
    """`contrib/__init__.py` promises contrib gets the same discipline core gets. The
    grade table in `driver.py`'s header is the claim; this is the check on it."""

    def test_the_header_grades_each_rule_instead_of_averaging_them(self):
        """It used to say "Each is enforced here, not just documented" of all four."""
        import harness.contrib.driver as mod
        self.assertIn("| rule | grade |", mod.__doc__ or "")

    def test_the_header_says_rule_2_is_off_by_default(self):
        import harness.contrib.driver as mod
        self.assertIn("OFF by default", mod.__doc__ or "")

    def test_the_header_calls_rule_3_a_heuristic(self):
        import harness.contrib.driver as mod
        self.assertIn("heuristic", (mod.__doc__ or "").lower())


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
