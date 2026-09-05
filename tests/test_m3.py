"""M3 executed: transcript, resume, exporters, event taxonomy."""
import io, os, subprocess, sys, tempfile, textwrap, unittest

from harness import Agent, tool, Secret
from harness.models.fake import FakeModel
from harness.observe.console import ConsoleExporter
from harness.observe.events import EventKind
from harness.observe.transcript import read
from harness.result import StopReason
import _paths

RAN: list = []

@tool(effect="read")
def look(x: int) -> str:
    """Look something up."""
    RAN.append(("look", x))
    return f"found {x}"

@tool(effect="danger")
def charge(amount: int) -> str:
    """Charge a card."""
    RAN.append(("charge", amount))
    return "charged"

@tool(effect="read")
def with_pii(email: str, ssn: str) -> str:
    """Take personal data."""
    return "ok"


class M3(unittest.TestCase):
    def setUp(self):
        RAN.clear()
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.jsonl")

    # -- transcript -------------------------------------------------------
    def test_transcript_is_append_only_with_gapless_seq(self):
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
                  transcript=self.path)
        a.run("go")
        events = list(read(self.path))
        self.assertEqual([e["seq"] for e in events], list(range(len(events))))
        self.assertEqual(events[0]["kind"], "run.started")
        self.assertEqual(events[-1]["kind"], "run.finished")

    def test_transcript_stores_an_argument_digest_not_the_arguments(self):
        m = FakeModel([FakeModel.tool_call("with_pii",
                        {"email": "a@b.com", "ssn": "123-45-6789"}), FakeModel.text("ok")])
        a = Agent(name="T", job="j", tools=[with_pii], provider=m, budget="$5",
                  transcript=self.path)
        a.run("go")
        raw = open(self.path).read()
        self.assertNotIn("123-45-6789", raw, "PII in tool arguments reached the transcript")
        self.assertNotIn("a@b.com", raw)
        self.assertIn("arguments_digest", raw)

    def test_transcript_never_contains_a_secret(self):
        s = Secret("sk-ant-TRANSCRIPT", name="key")
        @tool(effect="read")
        def leak(x: int) -> str:
            """Leak."""
            with s.reveal() as v: return f"the key is {v}"
        m = FakeModel([FakeModel.tool_call("leak", {"x": 1}), FakeModel.text("ok")])
        a = Agent(name="T", job="j", tools=[leak], provider=m, budget="$5",
                  transcript=self.path)
        a.run("go")
        self.assertNotIn("sk-ant-TRANSCRIPT", open(self.path).read())

    def test_a_truncated_final_line_still_reads(self):
        m = FakeModel([FakeModel.text("done")])
        Agent(name="T", job="j", provider=m, budget="$5", transcript=self.path).run("go")
        with open(self.path, "a") as fh:
            fh.write('{"seq": 99, "kind": "run.fin')      # simulate kill -9 mid-write
        events = list(read(self.path))
        self.assertGreater(len(events), 0)
        self.assertTrue(all("kind" in e for e in events))

    def test_an_unwritable_transcript_is_refused_before_the_run_starts(self):
        """This test used to assert the opposite, under a name that conflated two things.

        "An unwritable transcript must not kill the run" is right about a disk that FILLS
        while the agent is working — the run is underway and billed, and the next test
        keeps that. It was wrong about a path that could never have been opened: that is
        knowable at `Agent(...)`, and answering it with `disabled = True` produced

            run ok: True    file exists: False    error events: []

        A deployment that requires a transcript got a fully successful run, no artifact,
        and nothing distinguishing that from a process that was killed. docs/02 §7:
        configuration error -> raised at construction, never at run time.
        """
        from harness.errors import ConfigError
        m = FakeModel([FakeModel.text("done")])
        with self.assertRaises(ConfigError) as e:
            Agent(name="T", job="j", provider=m, budget="$5",
                  transcript="/proc/definitely/not/writable/t.jsonl")
        self.assertIn("cannot be opened", str(e.exception))

    def test_a_transcript_that_fails_mid_run_does_not_kill_the_run_but_is_not_silent(self):
        """The half worth keeping, and the half that was missing.

        Measured with a real ENOSPC injected after the first line: the writer went quiet
        and `bus error.raised` was EMPTY. `EventBus.emit` already isolates a broken
        exporter and records the failure — `TranscriptWriter` swallowed the `OSError`
        before the bus could see it, so the one error class its own comment named ("a
        full disk") was the one class that passed unnoticed.
        """
        import tempfile

        class Rec:
            def __init__(self): self.errors = []
            def emit(self, e):
                if e.kind.value == "error.raised":
                    self.errors.append(dict(e.data))

        class Full:
            """A handle that reports ENOSPC after the first line."""
            def __init__(self, fh): self._fh, self._n = fh, 0
            def write(self, s):
                self._n += 1
                if self._n > 1:
                    raise OSError(28, "No space left on device")
                return self._fh.write(s)
            def __getattr__(self, k): return getattr(self._fh, k)

        from harness.observe import transcript as T
        import pathlib as _pl
        path = _pl.Path(tempfile.mkdtemp()) / "t.jsonl"
        rec = Rec()
        orig = T.TranscriptWriter.__init__

        def patched(self, *a, **k):
            orig(self, *a, **k)
            self._fh = Full(self._fh)

        T.TranscriptWriter.__init__ = patched
        try:
            a = Agent(name="T", job="j", tools=[look], budget="$5",
                      transcript=str(path), exporters=[rec],
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("done")]))
            r = a.try_run("go")
        finally:
            T.TranscriptWriter.__init__ = orig
        self.assertTrue(r.ok, "a full disk must not kill a run")
        self.assertEqual([e.get("where") for e in rec.errors], ["exporter"])
        self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_policy_decided_is_emitted_for_allows_too(self):
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("ok")])
        a = Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
                  transcript=self.path)
        a.run("go")
        verdicts = [e["data"]["verdict"] for e in read(self.path)
                    if e["kind"] == "policy.decided"]
        self.assertEqual(verdicts, ["ALLOW"],
                         "an audit log that only records denials cannot prove what was permitted")

    # -- resume -----------------------------------------------------------
    def test_resume_reexecutes_an_interrupted_read(self):
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("ok")])
        Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
              transcript=self.path).run("find thing 1")
        # drop the tool.finished line to simulate a crash mid-tool
        lines = [l for l in open(self.path) if '"tool.finished"' not in l]
        open(self.path, "w").writelines(lines)
        RAN.clear()
        m2 = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("ok")])
        a = Agent(name="T", job="j", tools=[look], provider=m2, budget="$5")
        a.resume(self.path)
        self.assertIn(("look", 1), RAN, "an interrupted read must be re-executed")

    def test_resume_never_reexecutes_an_interrupted_danger_tool(self):
        m = FakeModel([FakeModel.tool_call("charge", {"amount": 100}), FakeModel.text("ok")])
        Agent(name="T", job="j", tools=[charge], provider=m, budget="$5",
              transcript=self.path, approve=lambda c, x: True).run("charge me")
        lines = [l for l in open(self.path) if '"tool.finished"' not in l]
        open(self.path, "w").writelines(lines)
        RAN.clear()
        m2 = FakeModel([FakeModel.text("understood")])
        a = Agent(name="T", job="j", tools=[charge], provider=m2, budget="$5",
                  approve=lambda c, x: True)
        a.resume(self.path)
        self.assertEqual(RAN, [], "a danger tool was re-executed on resume")
        sent = str(m2.calls[0].messages)
        self.assertIn("NOT retried", sent, "the model was not told the call was interrupted")

    # -- exporters --------------------------------------------------------
    def test_console_only_attaches_to_a_tty(self):
        self.assertFalse(ConsoleExporter.should_attach(io.StringIO()))
        class Tty(io.StringIO):
            def isatty(self): return True
        self.assertTrue(ConsoleExporter.should_attach(Tty()))

    def test_console_prints_tool_names_never_arguments(self):
        buf = io.StringIO()
        ex = ConsoleExporter("Helper", stream=buf)
        from harness.observe.events import Event
        ex.emit(Event(0, 0.0, "r", EventKind.TOOL_STARTED, 0,
                      {"tool": "search", "call_id": "c1"}))
        out = buf.getvalue()
        self.assertIn("searching the web", out)
        self.assertNotIn("c1", out)

    def test_a_piped_run_emits_no_progress_on_stdout(self):
        # The child gets an ABSOLUTE `src`. A relative one is resolved against the
        # child's cwd, which is how the 136 `sys.path.insert` calls `conftest.py`
        # removed came back through the back door: they survived inside the strings
        # these tests hand to a subprocess.
        script = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {str(_paths.SRC)!r})
            from harness import Agent
            from harness.models.fake import FakeModel
            a = Agent(name="T", job="j", provider=FakeModel([FakeModel.text("hi")]),
                      budget="$1")
            print(a.run("x"))
        """)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                             text=True, cwd=str(_paths.ROOT))
        self.assertEqual(out.stdout.strip(), "hi", f"stdout polluted: {out.stdout!r}")

    # -- taxonomy ---------------------------------------------------------
    def test_every_emitted_kind_is_in_the_closed_taxonomy(self):
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("ok")])
        Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
              transcript=self.path).run("go")
        known = {k.value for k in EventKind}
        for e in read(self.path):
            self.assertIn(e["kind"], known, f"{e['kind']} is not in the closed taxonomy")

    def test_every_kind_has_an_emit_site(self):
        """Round 27: three of fifteen kinds were never emitted, and one of them because
        window.manage() was built, tested, and never called from the loop."""
        import re
        src = "\n".join(p.read_text() for p in _paths.SRC.rglob("*.py"))
        sites = set(re.findall(r"emit\(\s*EventKind\.([A-Z_]+)", src))
        missing = [k.value for k in EventKind if k.name not in sites]
        self.assertEqual(missing, [], f"kinds the code can never emit: {missing}")

    def test_step_started_and_finished_are_paired(self):
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("ok")])
        Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
              transcript=self.path).run("go")
        kinds = [e["kind"] for e in read(self.path)]
        self.assertEqual(kinds.count("step.started"), kinds.count("step.finished"),
                         "an unpaired lifecycle event makes step duration underivable")

    def test_a_failing_tool_emits_error_raised(self):
        @tool(effect="read")
        def boom(x: int) -> int:
            """Boom."""
            raise ValueError("kaboom")
        m = FakeModel([FakeModel.tool_call("boom", {"x": 1}), FakeModel.text("ok")])
        Agent(name="T", job="j", tools=[boom], provider=m, budget="$5",
              transcript=self.path).run("go")
        errs = [e for e in read(self.path) if e["kind"] == "error.raised"]
        self.assertTrue(errs, "a tool failure produced no error.raised event")
        self.assertEqual(errs[0]["data"]["where"], "tool")

    def test_context_management_fires_at_a_reachable_configuration(self):
        """T-2.6 cannot trigger on defaults — see test_context_management_is_not_reachable
        on defaults below.  It engages for long-running agents that raise both limits,
        which is the configuration this exercises."""
        @tool(effect="read", max_result_tokens=40_000)
        def bulky(i: int) -> str:
            """Bulky."""
            return "R" * 400_000
        m = FakeModel([FakeModel.tool_call("bulky", {"i": i}, call_id=f"c{i}")
                       for i in range(12)] + [FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[bulky], provider=m,
                  budget="$500, 60 steps", model="claude-haiku-4-5",
                  transcript=self.path)
        a.try_run("go")
        managed = [e for e in read(self.path) if e["kind"] == "context.managed"]
        self.assertTrue(managed, "context management never ran even at raised limits")
        # ADR-066: real compaction now runs alongside editing (context/window.py), and
        # this fixture's 40k-token results are large enough that the ratio crosses
        # COMPACT_AT (0.80) in the same step it first crosses EDIT_AT (0.60) — so the
        # very first context.managed event here is legitimately "compacted", not
        # "edited". "not none" is the actual claim this test makes.
        self.assertIn(managed[0]["data"]["strategy"], ("edited", "compacted"))

    def test_compact_needed_dung_run_thay_vi_lap_lai_lang_le(self):
        """Bug thật: `manage_context()` đã hứa trả `"compact_needed"` khi hết chỗ dọn
        (`window.py`'s own docstring), nhưng `RunEngine._manage_context` chỉ dùng
        `out`, vứt luôn `action` — mỗi lượt tiếp theo lại gửi đúng request đã quá khổ,
        không có gì báo dừng. Patch `manage_context` để CHẮC CHẮN rơi vào nhánh này
        (không phụ thuộc dựng đúng số token thật) rồi khẳng định run dừng có lý do,
        không lặp tới hết ngân sách."""
        import harness.run as run_mod
        real_manage = run_mod.manage_context
        calls = {"n": 0}

        def _force_compact_needed(msgs, *, used_tokens, context_window):
            calls["n"] += 1
            return list(msgs), "compact_needed"

        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[look], provider=m,
                  budget="$500, 60 steps", transcript=self.path)
        run_mod.manage_context = _force_compact_needed
        try:
            r = a.try_run("go")
        finally:
            run_mod.manage_context = real_manage
        self.assertEqual(calls["n"], 1, "lẽ ra dừng ngay sau lượt compact_needed đầu "
                         "tiên, không gọi lại manage_context lần hai")
        self.assertEqual(r.stop_reason, StopReason.ERROR)
        self.assertIn("context", r.detail.lower())

    def test_context_management_is_documented_as_not_default_reachable(self):
        """Round 27: max_result_tokens(4,000) x budget.steps(20) = 80,000 tokens, and the
        editing threshold on the smallest window is 120,000.  The feature is dead by
        arithmetic on defaults; the docs must say so rather than imply it is active."""
        from harness.context.window import EDIT_AT
        from harness.models.pricing import MAX_CONTEXT
        reachable = 20 * 4_000
        smallest = min(w for k, w in MAX_CONTEXT.items() if k != "fake")
        self.assertLess(reachable, smallest * EDIT_AT)
        doc = (_paths.DOCS / "07-cost.md").read_text()
        self.assertIn("not reachable on the shipped defaults", doc,
                      "a feature no default can reach must say so in the docs")

    def test_the_enum_and_the_documented_table_agree(self):
        import re
        doc = (_paths.DOCS / "05-data-and-state.md").read_text()
        table = set(re.findall(r"^\| `([a-z]+\.[a-z_]+)` \|", doc, re.M))
        self.assertEqual(table, {k.value for k in EventKind},
                         "the taxonomy table and the enum have drifted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
