"""M2 executed: parallel scheduling, duplicate suppression, context growth."""
import asyncio, sys, time, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from caching_fake import CachingFake
from harness import Agent, tool
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.result import Usage

CALLS: list = []

@tool(effect="read")
async def slow_read(i: int) -> int:
    """A slow read."""
    CALLS.append(("read", i))
    await asyncio.sleep(0.10)
    return i

@tool(effect="write")
async def slow_write(i: int) -> int:
    """A slow write."""
    CALLS.append(("write", i))
    await asyncio.sleep(0.10)
    return i

@tool(effect="read")
def counted(x: int) -> int:
    """Counted."""
    CALLS.append(("counted", x))
    return x


def multi(name, n, args=lambda i: {"i": i}):
    return ModelResponse(
        tuple({"type": "tool_use", "id": f"c{i}", "name": name, "input": args(i)}
              for i in range(n)),
        "tool_use", Usage(100, 20), "fake")


class M2(unittest.TestCase):
    def setUp(self): CALLS.clear()

    def test_t27_parallel_safe_tools_run_concurrently(self):
        m = FakeModel([multi("slow_read", 6), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[slow_read], provider=m, budget="$5")
        t0 = time.monotonic(); a.run("go"); dt = time.monotonic() - t0
        self.assertLess(dt, 0.35, f"6 parallel-safe reads took {dt:.2f}s; expected ~0.1s")

    def test_t27_unsafe_tools_serialize(self):
        m = FakeModel([multi("slow_write", 4), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[slow_write], provider=m, budget="$5")
        t0 = time.monotonic(); a.run("go"); dt = time.monotonic() - t0
        self.assertGreater(dt, 0.35, f"4 write tools took {dt:.2f}s; they must not overlap")

    def test_t27_parallelism_is_bounded(self):
        peak = 0; live = 0
        @tool(effect="read")
        async def probe(i: int) -> int:
            """Probe."""
            nonlocal peak, live
            live += 1; peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return i
        m = FakeModel([multi("probe", 30), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[probe], provider=m, budget="$5",
                  max_parallel_tools=4)
        a.run("go")
        self.assertLessEqual(peak, 4, f"peak concurrency {peak} exceeded max_parallel_tools=4")

    def test_t25_identical_calls_in_one_step_run_once(self):
        m = FakeModel([multi("counted", 5, args=lambda i: {"x": 7}), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[counted], provider=m, budget="$5")
        r = a.run("go")
        self.assertEqual(len(CALLS), 1, f"identical call executed {len(CALLS)} times")
        results = r.messages[2]["content"]
        self.assertEqual(len(results), 5, "every tool_use still needs its own result (I-3)")
        self.assertEqual([x["tool_use_id"] for x in results], [f"c{i}" for i in range(5)])

    def test_t25_different_arguments_are_not_suppressed(self):
        m = FakeModel([multi("counted", 3, args=lambda i: {"x": i}), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[counted], provider=m, budget="$5")
        a.run("go")
        self.assertEqual(len(CALLS), 3)

    def test_t26_context_editing_keeps_the_window_bounded(self):
        from harness.context.window import manage
        msgs = [{"role": "user", "content": "q"}]
        for i in range(40):
            msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"c{i}", "name": "t", "input": {}}]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"c{i}", "content": "R" * 4000}]})
        before = sum(len(str(m)) for m in msgs)
        out, action = manage(msgs, used_tokens=700_000, context_window=1_000_000)
        after = sum(len(str(m)) for m in out)
        self.assertEqual(action, "edited")
        self.assertLess(after, before * 0.5, "editing did not meaningfully shrink the window")
        self.assertEqual(len(out), len(msgs), "editing must not drop messages, only contents")

    def test_t26_recent_steps_are_preserved(self):
        from harness.context.window import manage, KEEP_RECENT_STEPS
        msgs = [{"role": "user", "content": "q"}]
        for i in range(10):
            msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"c{i}", "name": "t", "input": {}}]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"c{i}", "content": f"KEEP{i}"}]})
        out, _ = manage(msgs, used_tokens=700_000, context_window=1_000_000)
        tail = str(out[-KEEP_RECENT_STEPS * 2:])
        for i in range(10 - KEEP_RECENT_STEPS, 10):
            self.assertIn(f"KEEP{i}", tail, f"recent step {i} was cleared")

    def test_t26_below_the_threshold_nothing_happens(self):
        msgs = [{"role": "user", "content": "q"}]
        from harness.context.window import manage
        out, action = manage(msgs, used_tokens=10, context_window=1_000_000)
        self.assertEqual(action, "none")
        self.assertEqual(out, msgs)

    def test_sc4_cache_benchmark(self):
        import bench_cache
        m = bench_cache.run(10)
        later = m.turns[2:]
        sent = sum(u.input_tokens + u.cache_read_input_tokens for u in later)
        read = sum(u.cache_read_input_tokens for u in later)
        ratio = read / sent
        self.assertGreaterEqual(ratio, 0.90, f"SC-4: cache reads {ratio:.1%} < 90%")
        # and it must not degrade with length — the failure mode Round 26 found
        self.assertGreaterEqual(m.turns[-1].cache_hit_ratio, m.turns[2].cache_hit_ratio,
                                "hit rate degrades as the conversation grows")


if __name__ == "__main__":
    unittest.main(verbosity=2)
