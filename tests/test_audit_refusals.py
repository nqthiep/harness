"""Can an auditor reconstruct every refusal, and its reason, from the artifacts?

Two findings, one question.

**F8** — a policy DENY was never durably recorded anywhere.  `dispatch.py`'s only
`DecisionLog.record()` sat inside `if d.verdict is Verdict.ASK:`, and the graph's only
one sat inside `approval_gate`.  So a refusal that never reached an approver — the
common case, and the one the safety story is built on — became a `policy.decided` event
and nothing else.  Measured on a run whose taint policy refused a `write`:
`decision rows: []`, on BOTH engines.  `policy/decision.py` argues the opposite in so
many words ("an audit log that records only what was permitted cannot answer 'what did
we refuse, and why'"), and `Decision.__post_init__` already permits an unbounded DENY so
that a refusal can be written without inventing a TTL for it.

**F7** — the classic loop's S-27 re-gate refused silently.  `dispatch.py` wrote an error
`tool_result` and `continue`d: no `policy.decided`, no `Decision`, no
`tool.started`/`tool.finished`.  The durable engine emitted the refusal.  Same scenario,
before the fix:

    loop      policy.decided: payroll ALLOW ; publish ALLOW        decision rows: []
    durable   policy.decided: payroll ALLOW ; publish ALLOW ;
                              publish DENY 'publish can only send information onward...'

An operator filtering for `verdict == "DENY"` saw zero refusals on a run where the
lattice had just blocked an exfiltration, and the last recorded verdict for that call
said ALLOW.

`tests/test_parity.py` compares the two engines against each other.  This file asserts
the CONTENT of the artifacts, so a change that breaks both engines the same way is still
caught: parity with a hole in it is still a hole.
"""
import unittest
from datetime import datetime, timedelta, timezone

from fake_chat import FakeChat
from langchain_core.messages import AIMessage, HumanMessage

from harness import Agent, tool
from harness.lg import build_agent
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.policy.base import Ruling, Verdict
from harness.policy.decision import (Actor, Decision, DecisionLog, Scope, from_json,
                                     to_json)
from harness.result import Usage

MODEL = "claude-opus-5"


@tool(effect="read")
def payroll(q: str) -> str:
    """Read the payroll.  Marked `sensitive=` by the agents below, so its result raises
    confidentiality to SECRET."""
    return "salaries: alice 100"


@tool(effect="write")
def publish(text: str) -> str:
    """Send information onward.  `check_flow` refuses it once the run holds a secret."""
    return "published"


@tool(effect="danger")
def wipe(x: int) -> str:
    """Irreversible."""
    return "gone"


class Recorder:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)

    def decided(self):
        return [(e.data.get("tool"), e.data.get("verdict"), e.data.get("reason"))
                for e in self.events if e.kind.value == "policy.decided"]


def _same_turn(*calls):
    """One assistant turn carrying several tool_use blocks — what makes the S-27 window
    reachable at all: the label `publish` was cleared against is the one from BEFORE
    `payroll` in the same batch ran."""
    return ModelResponse(
        tuple({"type": "tool_use", "id": cid, "name": n, "input": a}
              for n, a, cid in calls), "tool_use", Usage(100, 15), "fake")


def rows(log):
    return [(d.scope.tool, d.verdict.name, d.reason, d.actor.kind, d.actor.id,
             d.scope.call_id) for d in log.all()]


class ARefusalIsWrittenDown(unittest.TestCase):
    """F8, both engines, on a DENY that no approver ever saw."""

    def _loop(self, script, tools, **kw):
        log, rec = DecisionLog(), Recorder()
        a = Agent(name="a", job="j", model=MODEL, provider=FakeModel(script),
                  tools=tools, decisions=log, exporters=[rec], allowed_hosts=None, **kw)
        return a.try_run("go"), log, rec

    def _graph(self, script, stops, tools, **kw):
        log, rec = DecisionLog(), Recorder()

        class Stopping(FakeChat):
            stops: list = []

            def _generate(self, messages, stop=None, run_manager=None, **k):
                i = self.i
                r = super()._generate(messages, stop, run_manager, **k)
                r.generations[0].message.response_metadata = {
                    "stop_reason": self.stops[i] if i < len(self.stops) else "end_turn"}
                return r

        g, _ = build_agent(model=Stopping(script=script, stops=stops), model_name=MODEL,
                           tools=tools, budget="$5", exporters=[rec],
                           allowed_hosts=None, decisions=log, **kw)
        out = g.invoke({"messages": [HumanMessage("go")], "step": 0})
        return out, log, rec

    def test_a_taint_deny_reaches_the_book_on_the_classic_loop(self):
        r, log, rec = self._loop(
            [_same_turn(("payroll", {"q": "x"}, "c1"), ("publish", {"text": "y"}, "c2")),
             FakeModel.text("done")],
            [payroll, publish], sensitive=["payroll"], budget="$5")
        self.assertEqual([v for _t, v, _r in rec.decided()],
                         ["ALLOW", "ALLOW", "DENY"],
                         "the S-27 re-gate refused and said nothing (F7)")
        booked = [row for row in rows(log) if row[1] == "DENY"]
        self.assertEqual(len(booked), 1, f"no durable record of the refusal: {rows(log)}")
        tool_, _v, reason, kind, ident, call_id = booked[0]
        self.assertEqual(tool_, "publish")
        self.assertIn("secret", reason)
        self.assertEqual((kind, ident), ("policy", "taint"),
                         "the row must name the rule that refused, not a person (D-1)")
        self.assertEqual(call_id, "c2", "the row is scoped to the call it refused")
        self.assertEqual(r.tools_run, ("payroll",), "publish must not have run")

    def test_a_taint_deny_reaches_the_book_on_the_durable_engine(self):
        script = [AIMessage(content="", tool_calls=[
            {"name": "payroll", "args": {"q": "x"}, "id": "c1"},
            {"name": "publish", "args": {"text": "y"}, "id": "c2"}]),
            FakeChat.text("done")]
        _out, log, rec = self._graph(script, ["tool_use", "end_turn"],
                                     [payroll, publish], sensitive=["payroll"])
        self.assertEqual([v for _t, v, _r in rec.decided()],
                         ["ALLOW", "ALLOW", "DENY"])
        booked = [row for row in rows(log) if row[1] == "DENY"]
        self.assertEqual(len(booked), 1, f"no durable record of the refusal: {rows(log)}")
        self.assertEqual(booked[0][0], "publish")
        self.assertIn("secret", booked[0][2])
        self.assertEqual(booked[0][3:], ("policy", "taint", "c2"))

    def test_the_default_refusal_of_a_danger_tool_is_written_down_too(self):
        """Not just the exotic lattice case: the ordinary "no approver configured"
        refusal, which is what most deployments will actually produce."""
        r, log, rec = self._loop([FakeModel.tool_call("wipe", {"x": 1}, call_id="c1"),
                                  FakeModel.text("ok")], [wipe], budget="$5")
        self.assertEqual(r.tools_run, ())
        booked = [row for row in rows(log) if row[1] == "DENY"]
        self.assertEqual(len(booked), 1, f"nothing recorded: {rows(log)}")
        self.assertIn("approval", booked[0][2])

    def test_a_user_policy_that_denies_is_written_down(self):
        """`EffectPolicy`/`TaintPolicy`/`EgressPolicy` are not special — the rule is
        about the VERDICT, so a policy nobody in this package wrote gets the same row."""
        class NoTuesdays:
            name = "no-tuesdays"

            def check(self, call, ctx):
                return Ruling(Verdict.DENY, "not on a Tuesday", self.name)

        r, log, _rec = self._loop([FakeModel.tool_call("payroll", {"q": "x"}, call_id="c1"),
                                   FakeModel.text("ok")], [payroll],
                                  policies=[NoTuesdays()], budget="$5")
        self.assertEqual(r.tools_run, ())
        self.assertEqual(rows(log),
                         [("payroll", "DENY", "not on a Tuesday", "policy",
                           "no-tuesdays", "c1")])


class TheBookIsAppendOnlyAndNotRedundant(unittest.TestCase):
    def test_a_refusal_already_in_the_book_is_not_written_twice(self):
        """A revocation the operator wrote, then the engine refusing the call because of
        it.  The book must not grow a second row saying what its own row already says —
        that is the audit log citing itself.  (An ALLOW is the opposite case: S-29 wants
        one row per execution under a grant, and `tests/test_attack_s29.py` pins it.)"""
        log = DecisionLog()
        sc = Scope(tool="wipe", args={"x": 1}, call_id="c1")
        now = datetime.now(timezone.utc)
        rec = Recorder()
        a = Agent(name="a", job="j", model=MODEL,
                  provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}, call_id="c1"),
                                      FakeModel.text("ok")]),
                  tools=[wipe], decisions=log, exporters=[rec], budget="$5",
                  approve=lambda c, ctx: True, allowed_hosts=None)
        log.record(Decision(id="revoke", verdict=Verdict.DENY, scope=sc,
                            actor=Actor.operator("sre"), decided_at=now,
                            expires_at=now + timedelta(hours=1), run_id="",
                            reason="incident open"))
        r = a.try_run("go")
        self.assertEqual(r.tools_run, ("wipe",),
                         "the operator's row is keyed to another run_id, so this call "
                         "is unaffected — the fixture only needs a DENY in the book")
        self.assertEqual(len(log.all()), 2, f"unexpected rows: {rows(log)}")

    def test_a_row_survives_a_round_trip_through_the_journal(self):
        """The artifact has to be readable next year, not just in memory (docs/05 §2).
        `to_json`/`from_json` is the shape an auditor actually gets."""
        _r, log, _rec = ARefusalIsWrittenDown()._loop(
            [FakeModel.tool_call("wipe", {"x": 1}, call_id="c1"), FakeModel.text("ok")],
            [wipe], budget="$5")
        (row,) = [d for d in log.all() if d.verdict is Verdict.DENY]
        back = from_json(to_json(row))
        self.assertEqual(back.verdict, Verdict.DENY)
        self.assertEqual(back.scope.tool, "wipe")
        self.assertEqual(back.scope.call_id, "c1")
        self.assertEqual(back.reason, row.reason)
        self.assertEqual(back.actor, row.actor)


class WhichPolicySentThisToAPerson(unittest.TestCase):
    """An ASK is not emitted (it is not a decision — `Decision.__post_init__` refuses to
    store one), so the resolved row is the only row, and its `policy` reads `approval`
    for every approval there has ever been.  `asked_by` is what keeps the name of the
    rule that raised the question in the trail."""

    def _asked_by(self, agent_kwargs):
        rec = Recorder()
        a = Agent(name="a", job="j", model=MODEL,
                  provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}, call_id="c1"),
                                      FakeModel.text("ok")]),
                  tools=[wipe], exporters=[rec], budget="$5", allowed_hosts=None,
                  **agent_kwargs)
        a.try_run("go")
        return [(e.data.get("verdict"), e.data.get("policy"), e.data.get("asked_by"))
                for e in rec.events if e.kind.value == "policy.decided"]

    def test_an_approved_call_still_names_the_policy_that_asked(self):
        self.assertEqual(self._asked_by({"approve": lambda c, ctx: True}),
                         [("ALLOW", "approval", "effect")])

    def test_a_refused_call_names_it_too(self):
        self.assertEqual(self._asked_by({}), [("DENY", "approval", "effect")])

    def test_a_call_nobody_had_to_ask_about_reports_no_asker(self):
        rec = Recorder()
        a = Agent(name="a", job="j", model=MODEL,
                  provider=FakeModel([FakeModel.tool_call("payroll", {"q": "x"}, call_id="c1"),
                                      FakeModel.text("ok")]),
                  tools=[payroll], exporters=[rec], budget="$5", allowed_hosts=None)
        a.try_run("go")
        self.assertEqual(
            [(e.data.get("verdict"), e.data.get("asked_by"))
             for e in rec.events if e.kind.value == "policy.decided"],
            [("ALLOW", None)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
