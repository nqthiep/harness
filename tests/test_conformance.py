"""Round 38 — the source code against HARNESS.md, executably.

Every test here exists because an audit found the claim and the code disagreeing.  They
are grouped by the requirement in HARNESS.md they defend, so a future change that breaks
one can see which promise it broke.
"""
import asyncio, json, sys, unittest

from harness import Agent, tool
from harness.models.base import ModelRequest, ModelResponse
from harness.models.fake import FakeModel
from harness.result import StopReason, Usage
import harness.testing as T
import _paths

RAN: list = []


@tool(effect="danger")
def xoa(x: int) -> str:
    """Xoá."""
    RAN.append(x); return "đã xoá"


def run(script, **kw):
    RAN.clear()
    return Agent(name="T", job="j", model="fake", tools=[xoa], budget="$5",
                 provider=FakeModel(script), **kw).try_run("go")


PAUSE = ModelResponse(({"type": "text", "text": "đang tìm..."},), "pause_turn",
                      Usage(100, 20), "fake")


class ProviderPayload(unittest.TestCase):
    """HARNESS.md §I.4 — the payload is the whole of what 'Intelligent' buys, and it had
    never been sent, because `AnthropicProvider` has never run against the live API."""

    def payload(self, model="claude-opus-5", max_tokens=1000, **kw):
        from harness.models.anthropic import AnthropicProvider
        seen = {}

        class M:
            async def create(self, **k): seen.update(k); raise SystemExit
        class B: messages = M()
        class C: messages = M(); beta = B()

        p = AnthropicProvider.__new__(AnthropicProvider)
        p._client, p._counts, p._sdk = C(), {}, None
        p._fallbacks = kw.get("fallbacks", True)
        req = ModelRequest(model=model, system=(), tools=(),
                           messages=({"role": "user", "content": "hi"},),
                           max_tokens=max_tokens, effort="medium", stream=False,
                           output_format=None)
        try: asyncio.run(p.complete(req))
        except SystemExit: pass
        return seen

    def test_the_adaptive_models_get_adaptive_thinking_and_no_budget_tokens(self):
        """`budget_tokens` is a 400 on these four. NOT on all five — see the next
        test, which is the correction this one used to be wrong about."""
        for model in ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-5",
                      "claude-fable-5"):
            with self.subTest(model=model):
                k = self.payload(model)
                self.assertEqual(k["thinking"], {"type": "adaptive"})
                self.assertNotIn("budget_tokens", json.dumps(k))

    def test_the_budgeted_model_gets_budget_tokens_and_no_effort(self):
        """`claude-haiku-4-5` REJECTS adaptive thinking and rejects
        `output_config.effort`; it takes `{"type": "enabled", "budget_tokens": N}`.

        The old version of this class asserted the opposite as a general rule
        ("`budget_tokens` is a 400 on every model this package prices") while only ever
        exercising `claude-opus-5`, so `Agent(model="claude-haiku-4-5")` built a payload
        the endpoint refuses and every test passed (ADR-091).
        """
        k = self.payload("claude-haiku-4-5", max_tokens=8000)
        self.assertEqual(k["thinking"], {"type": "enabled", "budget_tokens": 4000})
        self.assertNotIn("output_config", k)

    def test_a_thinking_budget_stays_below_max_tokens_or_is_omitted(self):
        """The floor is 1024 and it must be strictly under `max_tokens`. A tightly
        sized budget can leave no room for both, and then the payload carries no
        `thinking` rather than an invalid pair."""
        k = self.payload("claude-haiku-4-5", max_tokens=3000)
        self.assertEqual(k["thinking"]["budget_tokens"], 1500)

        k = self.payload("claude-haiku-4-5", max_tokens=2000)
        self.assertEqual(k["thinking"]["budget_tokens"], 1024,
                         "the floor applies, and 1024 < 2000 so it still fits")

        for tight in (1024, 500):
            with self.subTest(max_tokens=tight):
                k = self.payload("claude-haiku-4-5", max_tokens=tight)
                self.assertNotIn("thinking", k)

    def test_every_priced_model_has_a_declared_payload_shape(self):
        """The two tables cannot drift: `price()` refuses an unpriced model, so this
        equality is what makes `THINKING_SHAPE` complete by construction."""
        from harness.models.anthropic import THINKING_SHAPE
        from harness.models.pricing import PRICES
        self.assertEqual(set(THINKING_SHAPE), {m for m in PRICES if m != "fake"})

    def test_an_undeclared_model_fails_visibly_rather_than_guessing(self):
        from harness.errors import ProviderBadRequest
        with self.assertRaises(ProviderBadRequest) as ctx:
            self.payload("claude-from-the-future")
        self.assertIn("THINKING_SHAPE", str(ctx.exception))

    def test_effort_lives_inside_output_config(self):
        self.assertEqual(self.payload()["output_config"]["effort"], "medium")

    def test_the_deprecated_top_level_output_format_is_never_sent(self):
        self.assertNotIn("output_format", self.payload())

    def test_refusal_fallbacks_are_on_by_default(self):
        """IDL-19 said 'enabled by default' in three documents and the payload had never
        carried it (Round 38).  A routine safety decline was a dead end."""
        k = self.payload()
        self.assertEqual(k["fallbacks"], "default")
        self.assertEqual(k["betas"], ["server-side-fallback-2026-07-01"])

    def test_the_beta_id_matches_the_parameter_form(self):
        """`fallbacks: "default"` pairs with -2026-07-01; the array form pairs with
        -2026-06-01, and mixing them is a 400."""
        k = self.payload()
        if k.get("fallbacks") == "default":
            self.assertIn("2026-07-01", k["betas"][0])

    def test_fallbacks_can_be_turned_off(self):
        k = self.payload(fallbacks=False)
        self.assertNotIn("fallbacks", k)
        self.assertNotIn("betas", k)

    def test_every_priced_model_id_is_a_real_current_id(self):
        """No date suffixes: `claude-opus-5`, never `claude-opus-5-20260101`."""
        from harness.models.pricing import PRICES
        import re
        for m in PRICES:
            if m == "fake": continue
            self.assertIsNone(re.search(r"-20\d{6}$", m), f"{m} carries a date suffix")

    def test_tools_are_strict_with_the_schema_strict_requires(self):
        @tool(effect="read")
        def f(a: str, b: int = 3) -> dict:
            """Doc."""
            return {}
        self.assertIs(f.input_schema["additionalProperties"], False)
        self.assertEqual(f.input_schema["required"], ["a"])


class StopReasonMapping(unittest.TestCase):
    """HARNESS.md §II — 'fail safe'. IDL-30: an unrecognised stop reason maps to ERROR,
    never to a success."""

    def test_pause_turn_resumes_rather_than_dying(self):
        """T-0.4 said 'surface pause_turn rather than swallowing it'; the code had never
        heard of it, so a resumable pause became a fatal error (Round 38)."""
        r = run([PAUSE, PAUSE, FakeModel.text("xong")])
        self.assertIs(r.stop_reason, StopReason.COMPLETED)
        self.assertEqual(r.text, "xong")

    def test_an_endless_pause_is_bounded_and_loud(self):
        r = run([PAUSE] * 20)
        self.assertIs(r.stop_reason, StopReason.ERROR)
        self.assertIn("paused", r.detail)

    def test_an_unknown_stop_reason_is_still_an_error(self):
        weird = ModelResponse(({"type": "text", "text": "x"},), "con_meo_bay",
                              Usage(10, 5), "fake")
        self.assertIs(run([weird]).stop_reason, StopReason.ERROR)


class DanglingToolCalls(unittest.TestCase):
    """The subtlety the research names about PydanticAI: a final output can end the run
    before dangling tool calls execute. Round 43 found this harness had it too."""

    def test_tool_calls_run_even_when_the_turn_says_end_turn(self):
        """A response carrying both text and tool_use with stop_reason "end_turn" had its
        tool calls silently dropped: the run reported completed, the tool never ran."""
        RAN.clear()
        both = ModelResponse(
            ({"type": "text", "text": "Xong."},
             {"type": "tool_use", "id": "c1", "name": "xoa", "input": {"x": 1}}),
            "end_turn", Usage(100, 20), "fake")
        r = run([both, FakeModel.text("thật sự xong")], approve=T.approve_all())
        self.assertEqual(RAN, [1], "tool_use blocks were dropped")
        self.assertEqual(r.tools_run, ("xoa",))

    def test_the_conversation_keeps_invariant_i3(self):
        """Every tool_use gets exactly one tool_result. Without it the stored conversation
        is rejected outright when replayed to the provider."""
        RAN.clear()
        both = ModelResponse(
            ({"type": "text", "text": "Xong."},
             {"type": "tool_use", "id": "c1", "name": "xoa", "input": {"x": 1}}),
            "end_turn", Usage(100, 20), "fake")
        r = run([both, FakeModel.text("ok")], approve=T.approve_all())
        uses, results = [], []
        for m in r.messages:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                uses += [b["id"] for b in c if b.get("type") == "tool_use"]
                results += [b["tool_use_id"] for b in c if b.get("type") == "tool_result"]
        self.assertEqual(set(uses), set(results), f"dangling: {set(uses) - set(results)}")


class TestingHelpers(unittest.TestCase):
    """HARNESS.md §I.3 + §XIII — §09 calls these 'assertions on behaviour'."""

    def test_a_blocked_tool_did_not_run(self):
        """They read `tool_use` blocks — the model's requests — so a tool policy blocked
        counted as called, and `assert_no_tool` failed on exactly the case it exists to
        prove (Round 38)."""
        r = run([FakeModel.tool_call("xoa", {"x": 1}), FakeModel.text("ok")],
                approve=T.deny_all())
        self.assertEqual(RAN, [], "the tool ran despite deny_all")
        self.assertEqual(r.tools_run, ())
        T.assert_no_tool(r, "xoa")
        with self.assertRaises(AssertionError):
            T.assert_tool_called(r, "xoa")

    def test_an_approved_tool_did_run(self):
        r = run([FakeModel.tool_call("xoa", {"x": 1}), FakeModel.text("ok")],
                approve=T.approve_all())
        self.assertEqual(RAN, [1])
        T.assert_tool_called(r, "xoa")
        with self.assertRaises(AssertionError):
            T.assert_no_tool(r, "xoa")

    def test_the_failure_message_explains_the_distinction(self):
        r = run([FakeModel.tool_call("xoa", {"x": 1}), FakeModel.text("ok")],
                approve=T.deny_all())
        with self.assertRaises(AssertionError) as cm:
            T.assert_tool_called(r, "xoa")
        self.assertIn("blocked", str(cm.exception))


class NotOverEngineered(unittest.TestCase):
    """HARNESS.md §III — the loop staying boring is what keeps it auditable (IDL-13)."""

    def test_the_loop_is_still_under_its_ceiling(self):
        for f, cap in (("run.py", 250), ("dispatch.py", 250)):
            body = [l for l in (_paths.CORE / f).read_text().splitlines()
                    if l.strip() and not l.strip().startswith("#")]
            self.assertLessEqual(len(body), cap, f"{f} is {len(body)} lines")


class StaticChecks(unittest.TestCase):
    """HARNESS.md §III — CLEAN CODE, and §II question 5: turn a runtime error into one a
    checker catches.  Both tools were listed as never run until Round 39."""

    def _tool(self, *cmd):
        import shutil, subprocess
        if shutil.which(cmd[0]) is None:
            self.skipTest(f"{cmd[0]} is not installed")
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_ruff_is_clean(self):
        r = self._tool("ruff", "check", "src", "tests", "examples")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])

    def test_mypy_is_clean(self):
        """`sys.executable -m mypy`, never the one on `PATH`, and the difference was 15
        errors.

        A type checker sees a dependency only if that dependency is installed for the
        interpreter it is asked about. `shutil.which("mypy")` here resolved to a uv tool
        venv that does not have `anthropic` in it, so `ignore_missing_imports` erased the
        entire typed surface of a DECLARED RUNTIME DEPENDENCY and this test reported
        success over nothing:

            which mypy        /root/.local/bin/mypy (1.19.1)
            mypy              Success: no issues found in 102 source files
            python -m mypy    Found 15 errors in 2 files          (2.3.1)

        `pyproject.toml` now turns `ignore_missing_imports` off for `anthropic.*`, which
        makes the wrong-environment case loud rather than silent — but a config that
        depends on being run correctly is not a gate, so the invocation is pinned here
        too. Both halves are needed: the override catches the wrong interpreter, this
        line stops us asking the wrong interpreter in the first place.
        """
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-m", "mypy"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])

    def test_every_cited_adr_exists(self):
        """763 citations across this repository address a decision by NUMBER. Two of them
        — ADR-098 and ADR-099 — were cited from five modules and from commit messages
        while having no section at all: the reasoning went into the commit and the log was
        never updated. A citation that resolves to nothing is worse than no citation, and
        nothing checked (ADR-103)."""
        import pathlib as _p
        import re

        log = (_paths.DOCS / "12-decision-logs.md").read_text()
        defined = set(re.findall(r"^### (ADR-\d{3})", log, re.M))
        cited = set()
        for root in ("src", "tests", "examples", "docs", "design"):
            for f in _p.Path(root).rglob("*"):
                if f.suffix in (".py", ".md") and "__pycache__" not in str(f):
                    cited |= set(re.findall(r"ADR-\d{3}", f.read_text()))
        self.assertEqual(sorted(cited - defined), [],
                         "cited somewhere, defined nowhere")

    def test_the_adr_index_is_complete_and_the_numbers_are_unique(self):
        """The index is the only thing that turns a number back into a subject without
        scrolling 4,000 lines, so it is worth nothing the moment it is stale."""
        import collections
        import re

        log = (_paths.DOCS / "12-decision-logs.md").read_text()
        sections = re.findall(r"^### (ADR-\d{3})", log, re.M)
        index_block = log.split("## 0. Index", 1)[1].split("\n---", 1)[0]
        indexed = re.findall(r"^\| \[(ADR-\d{3})\]", index_block, re.M)

        counts = collections.Counter(sections)
        self.assertEqual([n for n, c in counts.items() if c > 1], [],
                         "two sections share one ADR number")
        self.assertEqual(sorted(indexed), sorted(sections),
                         "the index and the sections disagree")

    def test_mypy_co_analyses_the_examples_with_core(self):
        """`examples/` is 6,780 lines that other files in this repo import, so "it is
        only an example" stopped being true a while ago (ADR-082). `test_mypy_is_clean`
        above already covers it — `pyproject.toml` lists it in `files` — and THAT is the
        fact worth pinning down, because checking the two units separately is measurably
        weaker: with `examples/` alone, every call into core is typed `Any` and 10 real
        findings hid behind that (ADR-087).

        Worth having: pointing the checker here for the first time found a leaked loop
        variable in `proof.py` shadowing `readability.grade` (it worked only because of
        the order the two lines happened to be in) and, through the profiles, two core
        annotation defects — `Profile.name` declared as a settable variable, which meant
        NO `frozen=True` profile satisfied the Protocol, and `Agent.safety` annotated
        `str` while `__init__` takes a `Literal`, so `Agent(safety=parent.safety)` — what
        every subagent must do — failed to type check. Co-analysis then found a third:
        `cost_per_success` annotated `Sequence[Result]` while its docstring promised the
        structural contract, so the duck-typed record its own bench passes was rejected.
        """
        import tomllib
        with open(_paths.repo("pyproject.toml"), "rb") as f:
            files = tomllib.load(f)["tool"]["mypy"]["files"]
        self.assertIn("examples", files,
                      "examples/ dropped out of the checked set; a separate `mypy "
                      "examples/` run types every call into core as Any")
        self.assertIn("src/harness", files)

    def test_a_user_gets_real_type_checking_on_the_value_types(self):
        """The count was never the point.  Before `dataclass_transform`, every value type
        in the package accepted any arguments at all, and `agent.name` — documented public
        in §03 — was reported as not existing (Round 39)."""
        import shutil, subprocess, tempfile, pathlib as _p, os
        if shutil.which("mypy") is None:
            self.skipTest("mypy is not installed")
        src = os.path.abspath("src")
        with tempfile.TemporaryDirectory() as d:
            f = _p.Path(d) / "u.py"
            f.write_text(
                "from harness import Agent\n"
                "from harness.result import Usage\n"
                "from harness.models.fake import FakeModel\n"
                "a = Agent(name='T', job='j', model='fake', "
                "provider=FakeModel([]), budget='$1')\n"
                "print(a.name, a.budget, a.safety)\n"
                "bad = Usage(1, 2, 3, 4, 5)\n"
                "typo = Usage(input_tokns=1)\n")
            env = {**os.environ, "MYPYPATH": src}
            r = subprocess.run(["mypy", "u.py", "--ignore-missing-imports",
                                "--no-error-summary"],
                               capture_output=True, text=True, env=env, cwd=d)
            own = [l for l in r.stdout.splitlines() if l.startswith("u.py")]
            self.assertTrue(own or r.returncode == 0, r.stdout[-1500:])
        self.assertTrue(any("Too many arguments" in l for l in own),
                        f"wrong arity not caught: {own}")
        self.assertTrue(any("input_tokns" in l for l in own),
                        f"misspelt field not caught: {own}")
        self.assertFalse([l for l in own if "has no attribute" in l],
                         f"a documented public attribute was reported missing: {own}")


class ConfigTimeRefusals(unittest.TestCase):
    """§II — Prevent, not Detect.  Each of these used to fail at run time, after paying
    for a model call."""

    def test_returns_wants_the_type_not_an_instance(self):
        from dataclasses import dataclass
        from harness.errors import ConfigError

        @dataclass
        class KQ:
            a: str

        with self.assertRaises(ConfigError) as cm:
            Agent(name="T", job="j", model="fake", returns=KQ("x"),
                  provider=FakeModel([]), budget="$5")
        self.assertIn("returns=KQ", str(cm.exception))


class TheProofRuns(unittest.TestCase):
    """`examples/proof.py` walks HARNESS.md and asserts each requirement. A proof nobody
    runs is a claim, so it runs here too — and Round 40 verified it *fails* when the
    library breaks (disabling the taint rule breaks it at the taint assertion)."""

    def test_the_full_agent_runs(self):
        """`examples/full_agent.py` is the one agent that uses every capability. It runs
        here because an example nobody runs rots — and this one already shipped a broken
        state machine once (Round 41)."""
        import subprocess
        r = subprocess.run([sys.executable, str(_paths.EXAMPLES / "full_agent.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2500:] + r.stderr[-1500:])
        self.assertIn("WHICH CAPABILITY ON WHICH BACKEND", r.stdout)

    def test_the_proof_passes(self):
        import subprocess
        r = subprocess.run([sys.executable, str(_paths.EXAMPLES / "proof.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertIn("Proven with real running code", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
