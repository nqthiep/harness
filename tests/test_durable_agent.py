"""`Agent(durable=True)` — one Agent, one set of methods, the LangGraph engine hidden
underneath (the "2 API interfaces" complaint this closes).  §10.5-adjacent: nothing
LangGraph- or LangChain-shaped may reach the caller, `durable=False` must be
byte-for-byte unchanged, and a durable run must actually survive a simulated process
restart — a fresh `Agent` instance, same checkpoint file, same `session_id=`.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import Agent, tool
from harness.errors import ConfigError, UnsafeToolSetError
from harness.models.fake import FakeModel
from harness.result import StopReason
import _paths


@tool(effect="read")
def look_up(order: str) -> dict:
    """Look up an order by id."""
    return {"status": "shipped"}


@tool(effect="danger")
def wipe(x: int) -> str:
    """Destroy something, irreversibly."""
    return "gone"


class DurableRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "chk.sqlite3")

    def test_a_tool_call_then_a_final_answer(self):
        script = [FakeModel.tool_call("look_up", {"order": "A1"}),
                  FakeModel.text("Order A1 has shipped.")]
        a = Agent(name="durable-1", job="support", provider=FakeModel(script),
                 tools=[look_up], durable=True, checkpoint=self.db,
                 allowed_hosts=None, session_id="s1")
        r = a.try_run("check order A1")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(r.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r.text, "Order A1 has shipped.")
        self.assertEqual(r.tools_run, ("look_up",))
        # Result.messages is the SAME native `{"role", "content"}` shape the classic
        # backend returns — no LangChain object anywhere in it.
        for m in r.messages:
            self.assertIsInstance(m, dict)
            self.assertIn(m["role"], ("user", "assistant"))

    def test_a_new_agent_instance_continues_the_same_conversation(self):
        """The actual durability claim: a FRESH Agent — a fresh Python object, standing
        in for a fresh process — reconnects to the same checkpoint file and session_id
        and sees the whole prior conversation, not just its own turn."""
        first = Agent(name="durable-2", job="support",
                      provider=FakeModel([FakeModel.text("hi, how can I help?")]),
                      durable=True, checkpoint=self.db, allowed_hosts=None,
                      session_id="cust-42")
        r1 = first.try_run("hello")
        self.assertTrue(r1.ok)

        second = Agent(name="durable-2", job="support",
                       provider=FakeModel([FakeModel.text("yes, still open")]),
                       durable=True, checkpoint=self.db, allowed_hosts=None,
                       session_id="cust-42")
        r2 = second.try_run("is my ticket still open?")
        self.assertTrue(r2.ok)
        # 2 messages from turn 1 (human + answer) + 2 from turn 2 = 4.
        self.assertEqual(len(r2.messages), 4)
        self.assertEqual(r2.messages[0]["content"], "hello")

    def test_a_different_session_id_is_a_different_conversation(self):
        agent = Agent(name="durable-3", job="support",
                      provider=FakeModel([FakeModel.text("a")]), durable=True,
                      checkpoint=self.db, allowed_hosts=None, session_id="one")
        agent.try_run("hi")
        other = agent.with_(provider=FakeModel([FakeModel.text("b")]), session_id="two")
        r = other.try_run("hi")
        self.assertEqual(len(r.messages), 2)          # no bleed from "one"'s history

    def test_durable_and_classic_agree_a_danger_tool_is_refused(self):
        """Parity, restated for the new surface (R-17): the safety rule doesn't relax
        just because the run happens to be durable."""
        a = Agent(name="durable-4", job="ops", provider=FakeModel(
            [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("done")]),
            tools=[wipe], durable=True, checkpoint=self.db, allowed_hosts=None)
        r = a.try_run("wipe it")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, (), "a danger tool ran with no approve= callback")

    def test_the_unsafe_pair_is_refused_at_construction_same_as_classic(self):
        @tool(effect="external")
        def fetch(url: str) -> str:
            """Read a page."""
            return "..."

        with self.assertRaises(UnsafeToolSetError):
            Agent(name="durable-5", job="x", provider=FakeModel([]),
                 tools=[fetch, wipe], durable=True, checkpoint=self.db)

    def test_default_checkpoint_file_is_auto_created(self):
        import os
        cwd = os.getcwd()
        os.chdir(self._tmp.name)
        try:
            a = Agent(name="Support Bot!", job="x",
                     provider=FakeModel([FakeModel.text("hi")]), durable=True,
                     allowed_hosts=None)
            a.try_run("hello")
            self.assertTrue((Path(".harness") / "checkpoints" / "support_bot.sqlite3")
                            .exists())
        finally:
            os.chdir(cwd)


class DurableGuards(unittest.TestCase):
    """Every place `durable=True` deliberately does NOT try to behave like the classic
    backend refuses loudly, with a next step — never a silent no-op (§45)."""

    def test_returns_parses_the_final_answer(self):
        """N-3, closed: `durable=True` used to refuse `returns=` outright at
        construction (`ConfigError`) — the durable engine never parsed a final answer
        against it, so `Result.value` would have silently stayed `None` forever.
        `lg/runtime.py::finish()` now runs the same `parse_returns()` the classic
        backend uses, before `run.finished` fires."""
        import dataclasses

        @dataclasses.dataclass
        class Order:
            id: str
            eta_days: int

        a = Agent(name="d", job="x", returns=Order,
                 provider=FakeModel([FakeModel.text('{"id": "o1", "eta_days": 3}')]),
                 durable=True, allowed_hosts=None)
        r = a.try_run("status?")
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(r.value, Order(id="o1", eta_days=3))

    def test_returns_a_bad_answer_is_a_result_not_a_raise(self):
        """The other half of N-2/N-3 parity: a final answer that doesn't fit `returns=`
        is a run OUTCOME (`stop_reason=ERROR`), never an unhandled raise out of
        `try_run()` — same contract the classic backend already holds (T-6.4)."""
        import dataclasses

        @dataclasses.dataclass
        class Order:
            id: str
            eta_days: int

        a = Agent(name="d", job="x", returns=Order,
                 provider=FakeModel([FakeModel.text("not json at all")]),
                 durable=True, allowed_hosts=None)
        try:
            r = a.try_run("status?")
        except Exception as exc:
            self.fail(f"a bad final answer crashed out of try_run(): "
                     f"{type(exc).__name__}: {exc}")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason, StopReason.ERROR)
        self.assertIsNone(r.value)
        self.assertIn("Order", r.detail)

    def test_chat_is_refused(self):
        a = Agent(name="d", job="x", provider=FakeModel([]), durable=True,
                 allowed_hosts=None)
        with self.assertRaises(ConfigError):
            a.chat()

    def test_resume_is_refused(self):
        a = Agent(name="d", job="x", provider=FakeModel([]), durable=True,
                 allowed_hosts=None)
        with self.assertRaises(ConfigError):
            a.resume("/tmp/does-not-matter.jsonl")

    def test_on_delta_is_refused(self):
        a = Agent(name="d", job="x", provider=FakeModel([FakeModel.text("hi")]),
                 durable=True, allowed_hosts=None)
        with self.assertRaises(ConfigError):
            a.try_run("hi", on_delta=lambda _s: None)

    def test_principal_is_refused_at_construction(self):
        """`principal=` (S-03) only threads through `RunContext` on the classic
        backend today — `build_agent()` has no parameter to hand it to. Silently
        accepting it under `durable=True` would let a caller believe a policy reading
        `ctx.principal` sees this value when it never would."""
        with self.assertRaises(ConfigError):
            Agent(name="d", job="x", provider=FakeModel([]), durable=True,
                 principal="user-42", allowed_hosts=None)

    def test_decisions_is_refused_at_construction(self):
        from harness.policy.decision import DecisionLog
        with self.assertRaises(ConfigError):
            Agent(name="d", job="x", provider=FakeModel([]), durable=True,
                 decisions=DecisionLog(), allowed_hosts=None)

    def test_with__preserves_durable_and_checkpoint(self):
        a = Agent(name="d", job="x", provider=FakeModel([]), durable=True,
                 checkpoint=":memory:")
        b = a.with_(name="e")
        self.assertTrue(b.durable)
        self.assertEqual(b.checkpoint, ":memory:")


class ClassicUnaffected(unittest.TestCase):
    """The other half of "one Agent, `durable=` doesn't change what already worked":
    `durable=False` (the default) must behave exactly as it did before this feature
    existed — same class, same slots, same methods, no new required argument.
    """

    def test_default_is_not_durable(self):
        a = Agent(name="c", job="x", provider=FakeModel([FakeModel.text("hi")]))
        self.assertFalse(a.durable)
        self.assertIsNone(a.checkpoint)
        r = a.run("hello")
        self.assertEqual(r.text, "hi")

    def test_chat_and_resume_still_work_when_not_durable(self):
        a = Agent(name="c", job="x",
                 provider=FakeModel([FakeModel.text("a"), FakeModel.text("b")]))
        chat = a.chat()
        r1 = chat.say("first")
        r2 = chat.say("second")
        self.assertTrue(r1.ok and r2.ok)


class DurableProcessExits(unittest.TestCase):
    """Regression test for the exact bug the per-call open/close design fixes: an
    `AsyncSqliteSaver`'s connection owns a non-daemon background thread, so an Agent
    that cached one for its whole lifetime made the interpreter hang on exit instead of
    returning — the first time anyone actually ran a plain script. `subprocess` with a
    hard timeout is what makes a hang a FAILING test instead of a wedged CI job.
    """

    def test_a_script_using_durable_true_exits_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.py"
            script.write_text(
                "import sys; sys.path.insert(0, %r)\n"
                "from harness import Agent\n"
                "from harness.models.fake import FakeModel\n"
                "a = Agent(name='p', job='x', durable=True, allowed_hosts=None,\n"
                "         provider=FakeModel([FakeModel.text('hi')]))\n"
                "print(a.run('hello').text)\n" % str(_paths.SRC)
            )
            r = subprocess.run([sys.executable, str(script)], cwd=tmp,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip(), "hi")


if __name__ == "__main__":
    unittest.main()
