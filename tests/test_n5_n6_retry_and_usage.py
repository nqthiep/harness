"""N-5 (provider-level retry) + N-6 (usage/latency_ms/duration_s in events) —
design/07-risks-and-open-issues.md. Built together: the retry wrapper measures the same
latency the event now reports, on both backends, through one shared `retry.py`.
"""
import sys
import tempfile
import unittest

sys.path.insert(0, "src")

from harness import Agent
from harness.errors import ProviderBadRequest, ProviderRateLimited
from harness.models import pricing
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


class FlakyThenOkPriced(FlakyThenOk):
    """`FlakyThenOk`, but priced like a real model. `FakeModel.price()` always returns
    `pricing.price("fake")` — every rate `Decimal(0)` — so a `Ledger.settle()` against it
    can never move `spent` off zero, and G-8's fix (`Ledger.settle_worst_case()`) would
    look like a no-op through that fixture no matter whether it actually ran. This is
    the minimal change needed to observe it: same script/failure behaviour, a real
    (non-zero) `Price` so a settled worst-case estimate shows up in `Result.cost`."""
    def price(self, m): return pricing.price("claude-haiku-4-5")


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

    def test_classic_settles_worst_case_spend_on_retry_exhaustion(self):
        """G-8, design/review-architect.md: `reserve()` opens ONE `Reservation` for the
        whole `with_provider_retry` call, but before this fix `settle()` only ran on a
        SUCCESSFUL final attempt — every failed attempt is still a real vendor call (a
        rate-limited/timed-out request can still be billed), and on total exhaustion the
        reservation was simply abandoned in `Ledger._open`, understating spend by every
        attempt actually made. `Result.cost` comes straight from `Ledger.spent`
        (`run.py`'s `Result(text, stop, step, self._l.spent, ...)`), so a real settlement
        must show up there even though the run itself ends in `StopReason.ERROR`."""
        provider = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=999)
        a = Agent(name="p", job="x", provider=provider, model="claude-haiku-4-5")
        r = a.try_run("go")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason.value, "error")
        self.assertGreater(r.cost.decimal, 0)

    def test_durable_settles_worst_case_spend_on_retry_exhaustion(self):
        """Same as the classic-backend test above, ported to `lg/runtime.py`'s node
        failure branch — `call_model()`'s `except` clause needed BOTH the
        `settle_worst_case()` call and (since each node rebuilds its `Ledger` from
        checkpointed state) returning `"spent_usd"`/`"ledger"` in its failure dict, or the
        settlement never survives past that one node."""
        provider = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=999)
        db = tempfile.mktemp(suffix=".sqlite3")
        a = Agent(name="p", job="x", provider=provider, model="claude-haiku-4-5",
                 durable=True, checkpoint=db, allowed_hosts=None)
        r = a.try_run("go")
        self.assertFalse(r.ok)
        self.assertEqual(r.stop_reason.value, "error")
        self.assertGreater(r.cost.decimal, 0)

    def test_classic_bills_worst_case_for_attempts_that_failed_before_a_success_H1(self):
        """H-1, design/review-architect-round2.md: G-8 only settled a worst-case
        estimate on total retry EXHAUSTION (the run.py `except` branch above). The
        SUCCESS path (`self._l.settle_after_retries(...)`, budget/ledger.py) used to
        settle only the winning attempt's real usage — a call that failed twice then
        succeeded on the third real provider call was billed identically to one that
        succeeded immediately, even though `MAX_ATTEMPTS` exists precisely because
        retrying is expected to often succeed: the common case, not the edge case,
        was the one left unbilled."""
        provider0 = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=0)
        a0 = Agent(name="p", job="x", provider=provider0, model="claude-haiku-4-5")
        r0 = a0.try_run("go")

        provider2 = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=2)
        a2 = Agent(name="p", job="x", provider=provider2, model="claude-haiku-4-5")
        r2 = a2.try_run("go")

        self.assertTrue(r0.ok)
        self.assertTrue(r2.ok)
        self.assertGreater(
            r2.cost.decimal, r0.cost.decimal,
            "2 real timeouts before a successful call must cost MORE than a call that "
            "succeeded immediately -- before this fix, both billed identically (only "
            "the winning attempt's usage ever reached the Ledger)")

    def test_durable_bills_worst_case_for_attempts_that_failed_before_a_success_H1(self):
        """Same scenario, ported to the durable/LangGraph backend — `attempts` has to
        survive the `_generate()` -> `.invoke()` -> `call_model()` boundary via
        `response_metadata` (`lg/adapter.py::_to_aimessage`), not just exist inside
        `retry.py`'s own return value."""
        db0 = tempfile.mktemp(suffix=".sqlite3")
        provider0 = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=0)
        a0 = Agent(name="p", job="x", provider=provider0, model="claude-haiku-4-5",
                  durable=True, checkpoint=db0, allowed_hosts=None)
        r0 = a0.try_run("go")

        db2 = tempfile.mktemp(suffix=".sqlite3")
        provider2 = FlakyThenOkPriced([FakeModel.text("hi")], fail_times=2)
        a2 = Agent(name="p", job="x", provider=provider2, model="claude-haiku-4-5",
                  durable=True, checkpoint=db2, allowed_hosts=None)
        r2 = a2.try_run("go")

        self.assertTrue(r0.ok)
        self.assertTrue(r2.ok)
        self.assertGreater(r2.cost.decimal, r0.cost.decimal)

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
