"""S-4/N-8 — design/07-risks-and-open-issues.md: `execute_once` (T-6.1) had a real
caller at the RUN level (T-9.2's Service API) but was never wired into a single TOOL
call retried mid-run. Without it, a `read`/`external` tool whose fn() call actually
SUCCEEDED but whose json.dumps/truncate/taint-check step right after it then raised
(a non-serializable return, an unrelated local bug, or — same shape on the durable
backend — this exact node being invoked again for a call that already completed) would
silently re-run fn() a second time: the double-effect class S-4 exists to guard
against, at the single-call granularity dispatch-level retry can actually reach.
"""
import unittest

from harness import Agent
from harness.models.fake import FakeModel

RAN: list = []


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append((event.kind.value, dict(event.data)))

    def close(self): ...

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


class ClassicBackendDedup(unittest.TestCase):
    """dispatch.py::_invoke — a retryable tool succeeds once, but the code right after
    the call (here, `truncate`) raises on the FIRST attempt only, forcing a genuine
    dispatch-level retry of the same call."""

    def setUp(self):
        RAN.clear()

    def test_a_post_success_failure_retries_without_rerunning_the_tool(self):
        import harness.dispatch as dispatch_mod
        from harness import tool

        @tool(effect="external")
        def fetch(url: str) -> str:
            """Fetch a URL — side-effecting enough that running it twice would matter."""
            RAN.append(url)
            return f"fetched {url}"

        real_truncate = dispatch_mod.truncate
        calls = {"n": 0}

        def flaky_truncate(text, max_tokens):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated: a bug downstream of a successful fn() call")
            return real_truncate(text, max_tokens)

        script = [FakeModel.tool_call("fetch", {"url": "https://example.com"}),
                  FakeModel.text("done")]
        rec = Recorder()
        agent = Agent(name="a", job="x", provider=FakeModel(script), tools=[fetch],
                      allowed_hosts=None, exporters=[rec])
        dispatch_mod.truncate = flaky_truncate
        try:
            result = agent.try_run("go")
        finally:
            dispatch_mod.truncate = real_truncate

        self.assertTrue(result.ok, result.stop_reason)
        self.assertEqual(RAN, ["https://example.com"],
            "fetch's own side effect must have run exactly once, not once per attempt")
        self.assertGreaterEqual(calls["n"], 2, "truncate must actually have been retried")

        finished = rec.of("tool.finished")
        self.assertEqual(len(finished), 1)
        self.assertTrue(finished[0].get("replayed"),
            "the attempt that finally succeeded replayed fetch's cached result, "
            "it didn't call fetch again")


class DurableBackendDedup(unittest.TestCase):
    """lg/runtime.py::_run_tools, white-box: the SAME `_pending` call, at the SAME
    step, dispatched to the node a second time (what a genuine crash-mid-batch,
    process-restart, checkpoint-replay would look like from this node's perspective)
    must not re-run the tool's fn() the second time."""

    def setUp(self):
        RAN.clear()

    def test_the_same_call_id_at_the_same_step_is_replayed_not_rerun(self):
        from fake_chat import FakeChat

        from harness import tool as tool_deco
        from harness.lg import build_agent

        @tool_deco(effect="read")
        def fetch2(url: str) -> str:
            """Fetch a URL."""
            RAN.append(url)
            return f"fetched {url}"

        graph, runtime = build_agent(model=FakeChat(script=[]), tools=[fetch2], budget="$5")
        state = {
            "run_id": "thread-xyz",
            "step": 3,
            "messages": [],
            "ledger": {},
            "_pending": [{"call": {"id": "c1", "name": "fetch2",
                                   "args": {"url": "https://example.com"}},
                         "tool": "fetch2", "verdict": 1, "reason": "ok"}],
        }
        out1 = runtime._run_tools(state)
        out2 = runtime._run_tools(state)   # same run_id, same step, same call_id

        self.assertEqual(RAN, ["https://example.com"],
            "fetch2 must have run exactly once across both node invocations")
        content1 = out1["messages"][0].content
        content2 = out2["messages"][0].content
        self.assertEqual(content1, content2, "the replay must carry the same result")


if __name__ == "__main__":
    unittest.main()
