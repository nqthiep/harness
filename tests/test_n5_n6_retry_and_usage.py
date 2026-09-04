"""N-5 (provider-level retry) + N-6 (usage/latency_ms/duration_s in events) —
design/07-risks-and-open-issues.md. Built together: the retry wrapper measures the same
latency the event now reports, on both backends, through one shared `retry.py`.
"""
import tempfile
import unittest

from harness import Agent
from harness.errors import ProviderBadRequest, ProviderRateLimited
from harness.models.fake import FakeModel


class FlakyThenOk:
    """A `ModelProvider` that raises a transient error `fail_times` times, then
    delegates to a real (fake) one — the minimal fixture N-5's own retry loop needs."""
    name = "flaky"

    def __init__(self, script, *, fail_times, exc=None, retry_after_s=0.001):
        self._inner = FakeModel(script)
        self._n = 0
        self._fail = fail_times
        self._exc = exc or ProviderRateLimited
        self._retry_after_s = retry_after_s

    def price(self, m): return self._inner.price(m)
    def max_output(self, m): return self._inner.max_output(m)
    async def count_input_tokens(self, r): return await self._inner.count_input_tokens(r)

    async def complete(self, request, *, on_delta=None):
        self._n += 1
        if self._n <= self._fail:
            raise self._exc("transient", retry_after_s=self._retry_after_s)
        return await self._inner.complete(request, on_delta=on_delta)


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append((event.kind.value, dict(event.data)))

    def close(self): ...

    def of(self, kind):
        return [d for k, d in self.events if k == kind]

    def retry_attempts(self):
        """Real `_on_retry` events only — has an `attempt` key. The FINAL failure also
        gets `retryable=True` for a `ProviderRateLimited`/`ProviderTimeout`/
        `ProviderUnavailable` (its exception class is inherently transient-typed, per
        `run.py`'s/`lg/runtime.py`'s own outer `except` — a pre-N-5 convention this
        keeps) even when no retry actually happened; that event carries no `attempt`."""
        return [d for k, d in self.events if k == "error.raised"
               and d.get("retryable") is True and "attempt" in d]


class ProviderRetry(unittest.TestCase):
    """N-5."""

    def test_classic_retries_a_transient_error_and_succeeds(self):
        rec = Recorder()
        provider = FlakyThenOk([FakeModel.text("hi")], fail_times=2)
        a = Agent(name="p", job="x", provider=provider, exporters=[rec])
        r = a.try_run("go")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "hi")
        retries = rec.retry_attempts()
        self.assertEqual(len(retries), 2)
        self.assertEqual([d["attempt"] for d in retries], [1, 2])

    def test_durable_retries_a_transient_error_and_succeeds(self):
        rec = Recorder()
        provider = FlakyThenOk([FakeModel.text("hi")], fail_times=2)
        db = tempfile.mktemp(suffix=".sqlite3")
        a = Agent(name="p", job="x", provider=provider, durable=True, checkpoint=db,
                 allowed_hosts=None, exporters=[rec])
        r = a.try_run("go")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "hi")
        retries = rec.retry_attempts()
        self.assertEqual(len(retries), 2)

    def test_a_non_transient_error_is_never_retried(self):
        """ProviderBadRequest/ProviderAuthError are never in retry.RETRYABLE — retrying
        a malformed or unauthorized request wastes money on a failure that won't change
        (docs/02-architecture.md §7's failure-philosophy table)."""
        rec = Recorder()
        provider = FlakyThenOk([FakeModel.text("hi")], fail_times=1,
                               exc=ProviderBadRequest)
        a = Agent(name="p", job="x", provider=provider, exporters=[rec])
        r = a.try_run("go")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason.value, "error")
        self.assertEqual([d for d in rec.of("error.raised") if d.get("retryable")], [])

    def test_exhausting_max_attempts_still_fails_gracefully(self):
        """Always-failing provider: retried up to retry.MAX_ATTEMPTS - 1 times, then the
        final attempt's failure becomes a normal Result(ERROR) — never an unhandled
        exception out of try_run() (docs/03: "try_run never raises for run outcomes")."""
        from harness import retry as retry_module
        rec = Recorder()
        provider = FlakyThenOk([FakeModel.text("hi")], fail_times=999)
        a = Agent(name="p", job="x", provider=provider, exporters=[rec])
        r = a.try_run("go")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason.value, "error")
        retryable = rec.retry_attempts()
        self.assertEqual(len(retryable), retry_module.MAX_ATTEMPTS - 1)

    def test_retry_never_outlives_the_runs_wall_clock_budget(self):
        """A run whose wall-clock budget is already nearly exhausted must not retry past
        it — docs/10-observability-ops.md §3's own promise ("retries cannot outlive the
        budget")."""
        rec = Recorder()
        provider = FlakyThenOk([FakeModel.text("hi")], fail_times=1,
                               retry_after_s=10.0)          # far longer than the budget
        a = Agent(name="p", job="x", provider=provider, budget="$5, 0.05s",
                 exporters=[rec])
        r = a.try_run("go")
        self.assertFalse(r.ok)
        # No retry actually happened (the wait itself exceeds the remaining budget) —
        # the failure surfaces on the very first attempt.
        self.assertEqual(rec.retry_attempts(), [])


class EventFields(unittest.TestCase):
    """N-6."""

    def test_classic_model_response_and_run_finished_carry_usage_and_timing(self):
        rec = Recorder()
        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("hi")]),
                 exporters=[rec])
        r = a.try_run("go")
        self.assertTrue(r.ok)
        resp = rec.of("model.response")[0]
        for key in ("latency_ms", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens"):
            self.assertIn(key, resp)
        self.assertIsInstance(resp["latency_ms"], float)
        self.assertGreaterEqual(resp["latency_ms"], 0)
        fin = rec.of("run.finished")[0]
        for key in ("duration_s", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens"):
            self.assertIn(key, fin)
        self.assertGreaterEqual(fin["duration_s"], 0)
        self.assertEqual(fin["input_tokens"], resp["input_tokens"])

    def test_durable_model_response_and_run_finished_carry_usage_and_timing(self):
        rec = Recorder()
        db = tempfile.mktemp(suffix=".sqlite3")
        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("hi")]),
                 durable=True, checkpoint=db, allowed_hosts=None, exporters=[rec])
        r = a.try_run("go")
        self.assertTrue(r.ok)
        resp = rec.of("model.response")[0]
        for key in ("latency_ms", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens"):
            self.assertIn(key, resp)
        fin = rec.of("run.finished")[0]
        for key in ("duration_s", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens"):
            self.assertIn(key, fin)
        self.assertEqual(fin["input_tokens"], resp["input_tokens"])

    def test_durable_multi_turn_usage_resets_per_turn_not_per_thread(self):
        """`turn_usage` (`lg/state.py`) is reset each new turn — a second, unrelated
        `try_run()` on the same thread must not accumulate the first turn's tokens into
        its own `run.finished`."""
        rec1, rec2 = Recorder(), Recorder()
        db = tempfile.mktemp(suffix=".sqlite3")
        a1 = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("first")]),
                  durable=True, checkpoint=db, allowed_hosts=None, exporters=[rec1],
                  session_id="s1")
        a1.try_run("go")
        a2 = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("second")]),
                  durable=True, checkpoint=db, allowed_hosts=None, exporters=[rec2],
                  session_id="s1")
        a2.try_run("again")
        fin1 = rec1.of("run.finished")[0]
        fin2 = rec2.of("run.finished")[0]
        self.assertEqual(fin1["input_tokens"], fin2["input_tokens"])   # not doubled


if __name__ == "__main__":
    unittest.main()
