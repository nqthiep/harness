"""`coding_profile.py::CodingProfile` — specifically the resource-lifecycle fix from the
architecture review: `apply()` used to open TWO `SqliteStore` connections per call (one
each for `TaskLedger`/`FindingsLog`), neither ever closed — measured at 74 leaked file
descriptors after 20 `apply()` calls with `enable_findings=True`. Fixed by sharing ONE
store between the two, and by accepting an injected `store=` so a caller that builds
many agents in a loop (`coding_bench.py`'s own case) can own and close it explicitly.
These tests check the fix mechanically, not just that fewer fds leak in one manual run.
"""
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "examples")

from harness import Agent
from harness.memory.inmemory import InMemoryStore
from harness.models.fake import FakeModel


class CodingProfileStoreThat(unittest.TestCase):
    def test_task_ledger_and_findings_log_share_one_injected_store(self):
        """Not two connections to the same path — literally the same `Store` object,
        proven by writing through one tool and reading it back through the OTHER
        feature's own tool, which only works if both landed in the same place."""
        import asyncio

        from coding_profile import CodingProfile

        store = InMemoryStore()
        profile = CodingProfile(root=".", store=store, enable_findings=True)
        agent = Agent(name="Coder", job="hi",
                     provider=FakeModel([FakeModel.text("x")])).with_profile(profile)

        add_task = next(t for t in agent.toolset if t.name == "add_task")
        add_finding = next(t for t in agent.toolset if t.name == "add_finding")

        async def scenario():
            await add_task.fn(title="fix the bug")
            await add_finding.fn(text="root cause was in parser.py")

        asyncio.run(scenario())
        # Read directly off the SAME store object the profile was given — both keys
        # must be there, because both tools were backed by IT, not by two stores that
        # each independently opened the same underlying file.
        self.assertIsNotNone(asyncio.run(store.get("harness:tasks")))
        self.assertIsNotNone(asyncio.run(store.get("harness:findings")))

    def test_an_injected_store_is_used_verbatim_not_wrapped_or_copied(self):
        from coding_profile import CodingProfile

        store = InMemoryStore()
        profile = CodingProfile(root=".", store=store)
        agent = Agent(name="Coder", job="hi",
                     provider=FakeModel([FakeModel.text("x")])).with_profile(profile)
        list_tasks = next(t for t in agent.toolset if t.name == "list_tasks")

        import asyncio
        asyncio.run(store.put("harness:tasks",
                              '[{"id":"t1","title":"seeded","status":"todo",'
                              '"note":"","created_at":0.0,"updated_at":0.0}]'))
        out = asyncio.run(list_tasks.fn())
        self.assertIn("seeded", out)

    def test_no_store_injected_still_consolidates_to_one_sqlite_connection(self):
        """The default path (no `store=`) is still fixed: before this change, apply()
        opened a SEPARATE SqliteStore for TaskLedger and for FindingsLog even though
        both pointed at the same `tasks_db` path — two live connections to one file,
        neither ever closed. An fd-count measurement is how the review FOUND this bug,
        but it is a bad regression test: fd counts around this code turned out to vary
        with GC timing between runs (measured 1.5/call in one batch, 3.0/call in
        another, for the SAME fixed code) — noisy enough to swamp the "1 vs 2
        connections" signal this test needs to catch. Counting actual `SqliteStore.
        __init__` calls is exact and deterministic instead.
        """
        import tempfile
        from pathlib import Path
        from unittest import mock

        from coding_profile import CodingProfile

        root = Path(tempfile.mkdtemp())
        with mock.patch("coding_profile.SqliteStore",
                        wraps=__import__("harness.memory.sqlite", fromlist=["SqliteStore"]).SqliteStore) as spy:
            profile = CodingProfile(root=root, tasks_db=str(root / "s.db"),
                                    enable_findings=True)
            Agent(name="X", job="hi",
                 provider=FakeModel([FakeModel.text("x")])).with_profile(profile)
        self.assertEqual(spy.call_count, 1,
                         f"CodingProfile.apply() constructed SqliteStore "
                         f"{spy.call_count} time(s) — TaskLedger and FindingsLog "
                         f"should share exactly one")


if __name__ == "__main__":
    unittest.main()
