"""Round 38 — the source code against HARNESS.md, executably.

Every test here exists because an audit found the claim and the code disagreeing.  They
are grouped by the requirement in HARNESS.md they defend, so a future change that breaks
one can see which promise it broke.
"""
import asyncio, json, sys, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from harness import Agent, tool
from harness.models.base import ModelRequest, ModelResponse
from harness.models.fake import FakeModel
from harness.result import StopReason, Usage
import harness.testing as T

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

    def payload(self, **kw):
        from harness.models.anthropic import AnthropicProvider
        seen = {}

        class M:
            async def create(self, **k): seen.update(k); raise SystemExit
        class B: messages = M()
        class C: messages = M(); beta = B()

        p = AnthropicProvider.__new__(AnthropicProvider)
        p._client, p._counts, p._sdk = C(), {}, None
        p._fallbacks = kw.get("fallbacks", True)
        req = ModelRequest(model="claude-opus-5", system=(), tools=(),
                           messages=({"role": "user", "content": "hi"},),
                           max_tokens=1000, effort="medium", stream=False,
                           output_format=None)
        try: asyncio.run(p.complete(req))
        except SystemExit: pass
        return seen

    def test_adaptive_thinking_never_budget_tokens(self):
        """`budget_tokens` is a 400 on every model this package prices."""
        k = self.payload()
        self.assertEqual(k["thinking"], {"type": "adaptive"})
        self.assertNotIn("budget_tokens", json.dumps(k))

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
        import pathlib
        for f, cap in (("src/harness/run.py", 250), ("src/harness/dispatch.py", 250)):
            body = [l for l in pathlib.Path(f).read_text().splitlines()
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
        r = self._tool("mypy")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])

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

    def test_the_proof_passes(self):
        import subprocess
        r = subprocess.run([sys.executable, "examples/proof.py"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertIn("Chứng minh được bằng code chạy thật", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
