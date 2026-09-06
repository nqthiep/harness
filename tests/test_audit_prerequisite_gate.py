"""`RequireBeforePolicy` was satisfied by the failure of the very thing it waits for.

`policy/builtin.py` documents `ctx.tools_called` as "populated from **completed** calls
earlier in the run." Both engines populated it from *attempted* ones — `dispatch.py`
extended `self.ran` before `asyncio.gather` and again before `_invoke`, and
`lg/runtime.py` appended to `called_now` in the declined, missing and errored branches
alike. Measured with an advisor that raises `RuntimeError` on every attempt:

    loop     tools_run=('advisor','deploy')   'RuntimeError: advisor is down'
    durable  tools_run=('deploy',)            'deployed to prod'

Both deployed to prod after the gate errored out. `tests/test_advisor_gate.py` only ever
exercised an advisor that worked, so nothing said so.

The fix needs two different questions kept apart, which is why it took a second list
rather than a filter on the first:

* **what SUCCEEDED** — the gate's question, and the only one that can mean "consulted".
* **what EXECUTED** — `Result.tools_run`'s question (IDL-49). A tool that ran and raised
  did execute, and a user asking `assert_tool_called` wants to know that.
"""
import unittest

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.policy.builtin import RequireBeforePolicy

STATE = {"advisor_works": False}


@tool(effect="read")
def advisor(q: str) -> str:
    """Consult the advisor before anything irreversible."""
    if not STATE["advisor_works"]:
        raise RuntimeError("advisor is down")
    return "cleared"


@tool(effect="danger")
def deploy(env: str) -> str:
    """Ship to production. Not reversible."""
    return "deployed to prod"


def run(*, durable: bool, advisor_works: bool):
    STATE["advisor_works"] = advisor_works
    policy = ((lambda: RequireBeforePolicy(tool="deploy", requires="advisor")) if durable
              else RequireBeforePolicy(tool="deploy", requires="advisor"))
    script = [FakeModel.tool_call("advisor", {"q": "ok?"}),
              FakeModel.tool_call("deploy", {"env": "prod"}, call_id="c2"),
              FakeModel.text("done")]
    a = Agent(name="p", job="j", tools=[advisor, deploy], allowed_hosts=None,
              accepts_tainted=["deploy"], policies=[policy],
              approve=lambda c, x: True, provider=FakeModel(script),
              **({"durable": True, "checkpoint": ":memory:"} if durable else {}))
    r = a.try_run("ship it")
    text = " | ".join(str(b.get("content", "")) for m in r.messages
                      if isinstance(m, dict) and isinstance(m.get("content"), list)
                      for b in m["content"] if isinstance(b, dict))
    return r, text


class AGateIsNotSatisfiedByItsPrerequisiteFailing(unittest.TestCase):
    def test_a_failed_advisor_does_not_open_the_gate_on_either_backend(self):
        for durable in (False, True):
            with self.subTest(backend="durable" if durable else "loop"):
                r, text = run(durable=durable, advisor_works=False)
                self.assertIn("advisor is down", text)
                self.assertNotIn("deployed to prod", text)
                self.assertIn("needs 'advisor'", text)

    def test_a_working_advisor_still_opens_it_on_either_backend(self):
        """The half `tests/test_advisor_gate.py` already had. Kept here so the fix cannot
        be mistaken for 'deny everything'."""
        for durable in (False, True):
            with self.subTest(backend="durable" if durable else "loop"):
                r, text = run(durable=durable, advisor_works=True)
                self.assertIn("cleared", text)
                self.assertIn("deployed to prod", text)

    def test_the_gates_question_and_tools_run_are_different_questions(self):
        """A tool that ran and raised counts as EXECUTED and not as COMPLETED.

        Collapsing the two would mean either a gate that opens on a failure (the defect)
        or a `Result.tools_run` that omits a tool the user watched fail, which is the
        opposite lie and breaks `harness.testing.assert_tool_called`.
        """
        r, _ = run(durable=False, advisor_works=False)
        self.assertIn("advisor", r.tools_run)


if __name__ == "__main__":
    unittest.main()
