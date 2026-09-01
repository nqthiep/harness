"""N-3 — design/07-risks-and-open-issues.md: the LangGraph/durable backend never
parsed a final answer against `returns=` — `build_agent()` had no such parameter at
all, and `Agent(durable=True, returns=...)` raised `ConfigError` at construction rather
than silently leaving `Result.value` at `None` forever (N-10's fix for the "silent"
half). This file covers what test_durable_agent.py's basic positive/negative cases
don't: that `run.finished` itself reports the corrected outcome (parity with the
classic backend's own T-6.4 fix — the whole reason `lg/runtime.py::finish()` validates
BEFORE emitting, not `agent.py::_state_to_result()` after the graph has already
returned), and that the raw `build_agent()` escape hatch gets the same parse.
"""
import dataclasses
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from harness import Agent
from harness.models.fake import FakeModel


@dataclasses.dataclass
class Order:
    id: str
    eta_days: int


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append((event.kind.value, dict(event.data)))

    def close(self): ...

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


class RunFinishedReportsTheCorrectedOutcome(unittest.TestCase):
    """`finish()` validates `returns=` BEFORE `run.finished` fires — not after, back in
    plain Python once the graph has already returned. If it validated after, this event
    would say `completed` for an answer `returns=` rejects, exactly the bug T-6.4 fixed
    for the classic backend (N-2)."""

    def test_run_finished_says_error_not_completed_for_a_bad_answer(self):
        rec = Recorder()
        a = Agent(name="d", job="x", returns=Order,
                 provider=FakeModel([FakeModel.text("not json")]),
                 durable=True, allowed_hosts=None, exporters=[rec])
        r = a.try_run("status?")

        self.assertFalse(r.ok)
        finished = rec.of("run.finished")
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["stop_reason"], "error",
            "run.finished must already report the corrected outcome, not a stale "
            "'completed' from before returns= was checked")

    def test_run_finished_says_completed_for_a_good_answer(self):
        rec = Recorder()
        a = Agent(name="d", job="x", returns=Order,
                 provider=FakeModel([FakeModel.text('{"id": "o1", "eta_days": 3}')]),
                 durable=True, allowed_hosts=None, exporters=[rec])
        r = a.try_run("status?")

        self.assertTrue(r.ok)
        finished = rec.of("run.finished")
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["stop_reason"], "completed")


class EscapeHatchAlsoParses(unittest.TestCase):
    """The raw `build_agent()` escape hatch (bypassing `Agent` entirely) gets the same
    validation — `returns=` is a `Runtime`-level concern (`finish()`), not something
    only `Agent`'s wrapper adds on top."""

    def test_build_agent_returns_parses_the_final_answer(self):
        from fake_chat import FakeChat

        from harness.lg import build_agent

        graph, _runtime = build_agent(
            model=FakeChat(script=[FakeChat.text('{"id": "o1", "eta_days": 3}')]),
            budget="$5", returns=Order)
        from langchain_core.messages import HumanMessage
        out = graph.invoke({"messages": [HumanMessage("status?")], "step": 0},
                           config={"configurable": {"thread_id": "t1"}})
        self.assertEqual(out.get("stop_reason"), "completed")


class ParsedValueReachesTheState(unittest.TestCase):
    """Bug thật, tìm thấy khi tự review lượt vá này: `finish()` đã GỌI `parse_returns`
    để validate, nhưng vứt luôn kết quả — `Agent(durable=True, returns=...)` không sao
    (`agent.py::_state_to_result` tự parse lại một lần nữa, ngoài checkpointed state),
    nhưng escape hatch `build_agent()` dùng trực tiếp thì mất hẳn khả năng lấy giá trị
    đã validate: `state.get("value")` luôn `None`, dù `AgentState` từng không hề khai
    field này."""

    def test_build_agent_escape_hatch_returns_the_parsed_value(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage

        from harness.lg import build_agent

        graph, _runtime = build_agent(
            model=FakeChat(script=[FakeChat.text('{"id": "o1", "eta_days": 3}')]),
            budget="$5", returns=Order)
        out = graph.invoke({"messages": [HumanMessage("status?")], "step": 0},
                           config={"configurable": {"thread_id": "t2"}})
        self.assertEqual(out.get("stop_reason"), "completed")
        self.assertEqual(out.get("value"), {"id": "o1", "eta_days": 3},
                         "parse_returns() đã validate xong nhưng finish() không ghi "
                         "lại vào state — escape hatch build_agent() không có cách "
                         "nào lấy được giá trị đã parse")

    def test_a_bad_answer_never_writes_a_stale_value(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage

        from harness.lg import build_agent

        graph, _runtime = build_agent(
            model=FakeChat(script=[FakeChat.text("not json")]),
            budget="$5", returns=Order)
        out = graph.invoke({"messages": [HumanMessage("status?")], "step": 0},
                           config={"configurable": {"thread_id": "t3"}})
        self.assertEqual(out.get("stop_reason"), "error")
        self.assertIsNone(out.get("value"))


if __name__ == "__main__":
    unittest.main()
