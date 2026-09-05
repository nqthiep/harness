"""What happens at the two ends of a run: the exporters get closed, and a leaking policy
is reported without throwing away the run that revealed it.

Both were declared and neither happened. `Exporter.close()` is in the Protocol and in
docs/04; only `TranscriptWriter` — the exporter the library builds for itself — was ever
called. And the shared-policy-state guard raised a bare `ConfigError` *after* the model
had been called and the run had been billed, so the `Result` it discarded took the cost,
the text and the transcript pointer with it.
"""
import unittest

from harness import Agent, tool
from harness.errors import SharedPolicyStateError
from harness.models.fake import FakeModel
from harness.observe.events import EventKind
from harness.policy.base import Ruling, Verdict


@tool(effect="read")
def look(q: str) -> str:
    """Look something up."""
    return "found"


class Recorder:
    """A user-supplied exporter. The seam's whole contract is `emit` and `close`."""

    def __init__(self, boom: bool = False):
        self.kinds: list[str] = []
        self.closed = 0
        self._boom = boom

    def emit(self, event) -> None:
        self.kinds.append(event.kind.value)

    def close(self) -> None:
        self.closed += 1
        if self._boom:
            raise RuntimeError("this sink cannot flush")


class NoClose:
    """An exporter that never declared `close`. Closing must not require it."""

    def __init__(self):
        self.kinds: list[str] = []

    def emit(self, event) -> None:
        self.kinds.append(event.kind.value)


def agent(**kw):
    kw.setdefault("name", "p")
    kw.setdefault("job", "j")
    kw.setdefault("allowed_hosts", None)
    kw.setdefault("provider", FakeModel([FakeModel.text("done")]))
    return Agent(**kw)


class EveryExporterGetsClosed(unittest.TestCase):
    def test_a_user_exporter_is_closed_on_the_classic_backend(self):
        rec = Recorder()
        agent(exporters=[rec]).try_run("go")
        self.assertEqual(rec.closed, 1)
        self.assertIn("run.finished", rec.kinds)

    def test_a_user_exporter_is_closed_on_the_durable_backend(self):
        rec = Recorder()
        agent(exporters=[rec], durable=True, checkpoint=":memory:").try_run("go")
        self.assertEqual(rec.closed, 1)
        self.assertIn("run.finished", rec.kinds)

    def test_an_exporter_without_close_is_not_a_problem(self):
        plain = NoClose()
        agent(exporters=[plain]).try_run("go")
        self.assertIn("run.finished", plain.kinds)

    def test_one_sink_that_cannot_flush_does_not_cost_the_others_their_flush(self):
        """They are independent sinks and closing is how they flush. Letting the first
        failure abort the loop would turn one broken exporter into every exporter's lost
        buffer — the same isolation `EventBus.emit` already applies to emitting."""
        first, boom, last = Recorder(), Recorder(boom=True), Recorder()
        agent(exporters=[first, boom, last]).try_run("go")
        self.assertEqual((first.closed, boom.closed, last.closed), (1, 1, 1))


class ClassPolicy:
    """A policy whose counter lives on the CLASS — invisible to a `__dict__` snapshot.

    This is the widest version of the leak the guard exists to stop: not one Agent's runs
    bleeding into each other, but every Agent in the process sharing one counter.
    """
    name = "sneaky"
    seen = 0

    def check(self, call, ctx):
        type(self).seen += 1
        return Ruling(Verdict.ALLOW, "", self.name)


class SteadyPolicy:
    """Configuration, not state. Must never be reported."""
    name = "steady"
    limit = 5

    def __init__(self, hosts=("a",)):
        self.hosts = tuple(hosts)

    def check(self, call, ctx):
        return Ruling(Verdict.ALLOW, "", self.name)


class ALeakingPolicyIsReportedWithoutLosingTheRun(unittest.TestCase):
    def setUp(self):
        ClassPolicy.seen = 0

    def run_once(self, policy, rec=None):
        # A tool call, not just text: a policy is only consulted when there is a call to
        # rule on, so a text-only script leaves `check` unrun and the guard with nothing
        # to compare. (The first version of this test did exactly that and reported the
        # guard as broken.)
        script = [FakeModel.tool_call("look", {"q": "x"}), FakeModel.text("done")]
        return agent(provider=FakeModel(script),
                     tools=[look], policies=[policy],
                     exporters=[rec] if rec is not None else []).try_run("go")

    def test_state_kept_on_the_class_is_detected(self):
        p = ClassPolicy()
        with self.assertRaises(SharedPolicyStateError):
            self.run_once(p)

    def test_the_result_survives_the_report(self):
        """By the time this is detectable the model has been called and the run has been
        billed. Discarding the `Result` threw away the only record of what the leaking
        policy actually did."""
        rec = Recorder()
        with self.assertRaises(SharedPolicyStateError) as e:
            self.run_once(ClassPolicy(), rec)
        r = e.exception.partial
        self.assertIsNotNone(r, "the Result was discarded")
        self.assertEqual(r.text, "done")
        self.assertTrue(r.ok)

    def test_the_finding_reaches_the_exporters_and_not_only_the_caller(self):
        """An exception is not an audit record: a caller that swallows it leaves no trace
        that a policy leaked. The exporters get it either way."""
        rec = Recorder()
        with self.assertRaises(SharedPolicyStateError):
            self.run_once(ClassPolicy(), rec)
        self.assertIn(EventKind.ERROR_RAISED.value, rec.kinds)

    def test_configuration_that_does_not_change_is_not_reported(self):
        r = self.run_once(SteadyPolicy())
        self.assertTrue(r.ok)

    def test_passing_the_class_is_still_the_documented_way_out(self):
        """A factory gets a fresh instance per run, so there is nothing to carry — and
        the class counter here proves the policy really did run."""
        r = self.run_once(ClassPolicy)
        self.assertTrue(r.ok)
        self.assertGreater(ClassPolicy.seen, 0)


if __name__ == "__main__":
    unittest.main()
