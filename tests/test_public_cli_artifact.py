"""`harness new` scaffolds something that leaves a trace — F21.

Measured on the scaffold as it shipped: `Agent(...)` with no `transcript=` and no
`exporters=` writes nothing anywhere.  `EventBus.events` is in memory and discarded,
`DecisionLog` defaults to a fresh in-memory instance per run, and `ConsoleExporter`
attaches only when `sys.stdout.isatty()` (`observe/console.py:26`), so under systemd or a
pipe not even the progress lines survive.  `harness new` scaffolded exactly that, while
`harness trace <transcript>` and `harness cost <transcript>` consume a file the default
path never creates.

Asked "is there anything an operator could do that leaves no trace?", the honest answer
was: the default.

The second half of this file is a defect the FIRST half found, which is the argument for
end-to-end verification over fixtures: run `harness cost` against a transcript the
scaffold actually produced and it reported `cache reads : 0 of 0 input tokens` for a run
with 1,500 cache-read tokens in it.  It was reading
`data["usage"]["cache_read_input_tokens"]`; both engines emit the fields flat, as
`cache_read_tokens` (`run.py:145-150`, `lg/runtime.py:323-327`).  No `usage` sub-dict
exists on `model.response` anywhere in the package, so the number was always zero and the
"Low." cache-linter warning could never fire.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from harness import Agent
from harness.cli import GITIGNORE, cmd_cost, cmd_new, cmd_trace, transcript_path
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.models.pricing import MAX_OUTPUT, price
from harness.result import Usage


class _Priced(FakeModel):
    """A fake with a real price table and real cache-read tokens, so the cost command
    has something non-zero to get wrong."""

    def price(self, model): return price("claude-opus-5")
    def max_output(self, model): return MAX_OUTPUT["claude-opus-5"]

    async def complete(self, request, *, on_delta=None):
        r = await FakeModel.complete(self, request, on_delta=on_delta)
        return ModelResponse(r.content, r.stop_reason,
                             Usage(2_000, 300, cache_read_input_tokens=1_500), r.model)


class TheScaffoldLeavesATrace(unittest.TestCase):

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="f21-"))

    def test_the_scaffold_sets_a_transcript(self):
        cmd_new("jokey", cwd=self.d)
        written = (self.d / "jokey.py").read_text()
        self.assertIn(f'transcript="{transcript_path("jokey")}"', written)

    def test_the_scaffold_names_the_two_commands_that_read_it(self):
        """`harness trace` and `harness cost` were unreachable from what `new` wrote."""
        cmd_new("jokey", cwd=self.d)
        written = (self.d / "jokey.py").read_text()
        self.assertIn(f"harness trace {transcript_path('jokey')}", written)
        self.assertIn(f"harness cost {transcript_path('jokey')}", written)

    def test_the_gitignore_keeps_the_trace_off_the_internet(self):
        cmd_new("jokey", cwd=self.d)
        self.assertIn(".harness/", (self.d / ".gitignore").read_text())
        self.assertIn(".harness/", GITIGNORE)

    def test_running_the_scaffolded_agent_actually_writes_the_file(self):
        """The whole point: execute what `new` wrote, with only the provider swapped,
        and find the transcript on disk afterwards."""
        cmd_new("jokey", cwd=self.d)
        self._run_scaffold()
        produced = self.d / transcript_path("jokey")
        self.assertTrue(produced.exists(), "the scaffold ran and left nothing behind")
        kinds = [json.loads(line)["kind"] for line in
                 produced.read_text().splitlines() if line.strip()]
        self.assertIn("run.started", kinds)
        self.assertIn("run.finished", kinds)

    def test_trace_and_cost_both_read_what_the_scaffold_produced(self):
        cmd_new("jokey", cwd=self.d)
        self._run_scaffold()
        produced = str(self.d / transcript_path("jokey"))

        traced: list[str] = []
        self.assertEqual(cmd_trace(produced, out=traced.append), 0)
        self.assertTrue(any("run.finished" in line for line in traced), traced)

        costed: list[str] = []
        self.assertEqual(cmd_cost(produced, out=costed.append), 0)
        self.assertTrue(any(line.startswith("runs        : 1") for line in costed), costed)

    # -- the defect the end-to-end run found -------------------------------------

    def test_cost_reports_the_cache_tokens_the_events_actually_carry(self):
        """It read `data["usage"]["cache_read_input_tokens"]`; the engines emit
        `data["cache_read_tokens"]`, flat.  Every run said `0 of 0`."""
        cmd_new("jokey", cwd=self.d)
        self._run_scaffold()
        produced = self.d / transcript_path("jokey")

        rows = [json.loads(line) for line in produced.read_text().splitlines()
                if line.strip()]
        responses = [r for r in rows if r["kind"] == "model.response"]
        self.assertTrue(responses, "no model.response event to read")
        self.assertNotIn("usage", responses[0]["data"],
                         "if a `usage` sub-dict appears, this command must read it")
        self.assertEqual(responses[0]["data"]["cache_read_tokens"], 1_500)

        costed: list[str] = []
        cmd_cost(str(produced), out=costed.append)
        line = next(x for x in costed if x.startswith("cache reads"))
        self.assertIn("1,500", line, f"cache reads went missing: {line}")
        self.assertNotIn("0 of 0", line)

    def test_the_low_cache_warning_can_actually_fire(self):
        """The second number exists to point at the cache linter.  It never could."""
        cmd_new("jokey", cwd=self.d)
        self._run_scaffold()
        costed: list[str] = []
        cmd_cost(str(self.d / transcript_path("jokey")), out=costed.append)
        self.assertTrue(any("cache-linter" in x or "07-cost.md" in x for x in costed),
                        f"1500/3500 is under half and should have warned: {costed}")

    # -- helper ------------------------------------------------------------------

    def _run_scaffold(self) -> None:
        """Execute the generated file verbatim, provider swapped, cwd = the temp dir."""
        import contextlib
        import io
        import os

        src = (self.d / "jokey.py").read_text().replace(
            "from harness import Agent\n", "")
        real = Agent

        def _agent(**kw):
            kw.setdefault("provider", _Priced([FakeModel.text("a cat joke")]))
            return real(**kw)

        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(src, "<jokey.py>", "exec"), {"Agent": _agent})
        finally:
            os.chdir(cwd)


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
