"""`coding_bench.py::bench()` — specifically the resource-lifecycle fix: each case now
builds its OWN `SqliteStore`, passes it to `CodingProfile(store=...)`, and closes it in
a `finally` right after the agent's run — instead of `CodingProfile.apply()`'s own
internal default silently opening one per case that nothing ever closed (the loop shape
that turns "a short script that exits anyway" into a real, growing leak).

`provider=` (added alongside this fix, `coding_bench.py`'s own docstring explains why)
is what makes this testable at all without a live API key.
"""
import subprocess
import unittest

from harness.models.fake import FakeModel


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path):
    """Two commits: a buggy add.py, then a fix + its own test in one commit — the
    exact shape `harvest()` looks for (a commit touching both a source and a test
    file), reconstructed by hand here instead of relied on from this project's own
    history, so the fixture is stable regardless of what this repo's log looks like
    later."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "t@example.com")
    _run(repo, "config", "user.name", "T")
    (repo / "add.py").write_text("def add(a, b):\n    return a - b\n")
    _run(repo, "add", "add.py")
    _run(repo, "commit", "-q", "-m", "add add()")
    (repo / "add.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_add.py").write_text(
        "from add import add\ndef test_add():\n    assert add(2, 3) == 5\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "fix add() and test it")
    return repo


class BenchStoreLifecycleThat(unittest.TestCase):
    def test_the_case_store_is_closed_exactly_once_after_a_successful_case(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        import coding_bench as cb

        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            cases = cb.harvest(repo, limit=5, scan=10)
            self.assertEqual(len(cases), 1, "fixture should yield exactly one case")

            provider = FakeModel([
                FakeModel.tool_call("edit_source",
                                    {"path": "add.py", "old": "return a - b",
                                     "new": "return a + b"}),
                FakeModel.tool_call("run_tests", {}),
                FakeModel.text("Fixed."),
            ])

            closes: list[int] = []
            real_close = cb.SqliteStore.close

            async def counting_close(self):
                closes.append(1)
                return await real_close(self)

            with mock.patch.object(cb.SqliteStore, "close", counting_close):
                outcomes = cb.bench(repo, cases, provider=provider)

        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].valid)
        self.assertTrue(outcomes[0].fixed, "the scripted fix should have made the "
                                           "test pass")
        self.assertEqual(len(closes), 1,
                         "exactly one SqliteStore should have been opened AND closed "
                         "for this one case")

    def test_the_case_store_is_still_closed_when_try_run_raises(self):
        """The `finally` is the actual point of the fix — a case that errors out must
        not leak its store just because the happy path never returns normally.

        `Agent.try_run` itself is mocked to raise directly, rather than relying on a
        misbehaving provider: the harness's own run loop catches an ordinary exception
        from `provider.complete()` internally and returns a `Result` with
        `stop_reason=ERROR` rather than letting it propagate (verified while writing
        this test — an earlier version expected `RuntimeError` to reach `bench()` and
        it never did), so that path does not actually exercise `finally` against a
        genuine raise. Patching `try_run` itself does.
        """
        import tempfile
        from pathlib import Path
        from unittest import mock

        import coding_bench as cb
        from harness import Agent

        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            cases = cb.harvest(repo, limit=5, scan=10)

            closes: list[int] = []
            real_close = cb.SqliteStore.close

            async def counting_close(self):
                closes.append(1)
                return await real_close(self)

            with mock.patch.object(cb.SqliteStore, "close", counting_close), \
                 mock.patch.object(Agent, "try_run",
                                   side_effect=RuntimeError("simulated failure")):
                with self.assertRaises(RuntimeError):
                    cb.bench(repo, cases, provider=FakeModel([]))

        self.assertEqual(len(closes), 1,
                         "the store must be closed even though try_run raised")


if __name__ == "__main__":
    unittest.main()
