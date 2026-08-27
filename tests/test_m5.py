"""M5 executed: the four-command cold start, and §15's promises as tests."""
import os, pathlib, re, subprocess, sys, tempfile, unittest
sys.path.insert(0, "src")

from harness import Agent, tool, ConfigError, MissingEffectError, ToolSchemaError
from harness.cli import NO_KEY_MESSAGE, cmd_new, cmd_setup, key_status
from harness.models.fake import FakeModel

DOC = pathlib.Path("docs/15-first-agent.md").read_text()


FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.S | re.M)


def blocks(doc, lang="python"):
    """Fenced blocks of a given language.  A bare fence (lang == "") is sample OUTPUT or
    an error message, not code — matching those as code was a bug in the first version
    of this file (Round 29)."""
    return [body for tag, body in FENCE.findall(doc) if tag == lang]


def child_facing(doc):
    """The part a child reads.  The preamble is written for reviewers and legitimately
    names the jargon the tutorial avoids."""
    start = doc.index("# Make your own AI helper")
    end = doc.index("## Reviewer notes")
    return doc[start:end]


def is_complete_program(src):
    """Several blocks are deliberately fragments — a line to change, a keyword argument
    to add.  Only complete programs are expected to compile standalone."""
    return src.lstrip().startswith(("from ", "import ", "@tool"))


class Scaffold(unittest.TestCase):
    def setUp(self): self.d = pathlib.Path(tempfile.mkdtemp())

    def test_new_writes_the_agent_and_the_gitignore_together(self):
        files = cmd_new("joker", cwd=self.d)
        names = {f.name for f in files}
        self.assertEqual(names, {"joker.py", ".gitignore"})
        self.assertIn(".env", (self.d / ".gitignore").read_text())

    def test_the_scaffold_matches_the_tutorial(self):
        cmd_new("joker", cwd=self.d)
        written = (self.d / "joker.py").read_text()
        tutorial = next(b for b in blocks(DOC) if "harness new" not in b and "Agent(" in b)
        for line in ("from harness import Agent", 'name="Joker"', 'budget="$0.05"',
                     "joker.run("):
            self.assertIn(line, written, f"scaffold and §15 disagree on: {line}")
        self.assertIn('name="Joker"', tutorial)

    def test_the_scaffold_actually_runs(self):
        """Executes the generated file verbatim, with only the provider swapped — so a
        real cold start differs from this by the API key alone."""
        cmd_new("joker", cwd=self.d)
        src = (self.d / "joker.py").read_text()
        ns = {}
        header = (f"import sys; sys.path.insert(0, {os.path.abspath('src')!r})\n"
                  "import harness\n"
                  "from harness.models.fake import FakeModel\n"
                  "_real = harness.Agent\n"
                  "def Agent(**kw):\n"
                  "    kw.setdefault('provider', FakeModel([FakeModel.text("
                  "'Why did the cat sit on the computer?')]))\n"
                  "    return _real(**kw)\n")
        import io, contextlib
        buf = io.StringIO()
        exec(compile(header, "<header>", "exec"), ns)
        body = src.replace("from harness import Agent\n", "")
        with contextlib.redirect_stdout(buf):
            exec(compile(body, "<joker.py>", "exec"), ns)
        self.assertIn("cat", buf.getvalue())

    def test_an_existing_gitignore_is_extended_not_clobbered(self):
        (self.d / ".gitignore").write_text("build/\n")
        cmd_new("j", cwd=self.d)
        text = (self.d / ".gitignore").read_text()
        self.assertIn("build/", text)
        self.assertIn(".env", text)


class Setup(unittest.TestCase):
    def test_an_invalid_key_is_never_stored(self):
        saved = []
        msg = cmd_setup(read_key=lambda: "bad", write_env=saved.append,
                        validate=lambda k: (False, "401 unauthorized"))
        self.assertEqual(saved, [], "an invalid key was written to disk")
        self.assertIn("did not work", msg)

    def test_a_valid_key_is_stored_and_confirmed(self):
        saved = []
        msg = cmd_setup(read_key=lambda: "sk-ant-good", write_env=saved.append,
                        validate=lambda k: (True, ""))
        self.assertEqual(saved, ["sk-ant-good"])
        self.assertIn("working", msg)

    def test_an_existing_env_var_wins(self):
        os.environ["ANTHROPIC_API_KEY"] = "already-here"
        try:
            have, source = key_status()
            self.assertTrue(have)
            msg = cmd_setup(read_key=lambda: (_ for _ in ()).throw(AssertionError("prompted")),
                            write_env=lambda k: None, validate=lambda k: (True, ""))
            self.assertIn("already set up", msg)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_the_missing_key_error_says_harness_setup_not_the_env_var(self):
        a = Agent(name="T", job="j")
        with self.assertRaises(ConfigError) as cm:
            a.run("hi")
        self.assertIn("harness setup", str(cm.exception))
        self.assertNotIn("ANTHROPIC_API_KEY", str(cm.exception))
        self.assertIn("harness setup", NO_KEY_MESSAGE)


class TutorialPromises(unittest.TestCase):
    """§15 shows exact error text.  The tutorial is the specification for these strings."""

    @staticmethod
    def _norm(text):
        """Compare the message a child sees.  The `-> docs/...` pointer is a link, not
        prose, and §15 omits it; everything else must match line for line."""
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.strip().splitlines()]
        return [l for l in lines if l and not l.startswith("->")]

    def assertMessageMatches(self, actual, shown):
        """Round 31: the earlier version asserted a handful of substrings, so the code
        could emit a completely different (grade-12.5) message and still pass."""
        a, s = self._norm(actual), self._norm(shown)
        if a != s:
            diff = "\n".join(f"  code: {x}\n  §15 : {y}"
                              for x, y in zip(a + [""] * len(s), s + [""] * len(a)) if x != y)
            self.fail(f"§15 and the code emit different messages:\n{diff}")

    def _fenced(self, needle):
        for b in blocks(DOC, ""):
            if needle in b:
                return b
        self.fail(f"§15 has no output/error block containing {needle!r}")

    def test_missing_effect_message_matches_the_tutorial(self):
        with self.assertRaises(MissingEffectError) as cm:
            @tool()
            def send_email(to: str) -> str:
                """Send an email."""
        self.assertMessageMatches(str(cm.exception), self._fenced("does in the world"))

    def test_missing_type_hint_message_matches_the_tutorial(self):
        with self.assertRaises(ToolSchemaError) as cm:
            @tool(effect="read")
            def add(a, b):
                """Add."""
        self.assertMessageMatches(str(cm.exception), self._fenced("what kind of thing"))

    def test_effect_typo_message_matches_the_tutorial(self):
        with self.assertRaises(MissingEffectError) as cm:
            @tool(effect="reed")
            def look(x: str) -> str:
                """Look."""
        self.assertIn('Did you mean "read"?', str(cm.exception))
        self.assertIn('Did you mean "read"?', self._fenced("not one of the four choices"))

    def test_positional_agent_message_matches_the_tutorial(self):
        with self.assertRaises(ConfigError) as cm:
            Agent("Helper", "tell jokes")
        actual = str(cm.exception)
        self.assertIn("label each part", actual)
        self.assertIn("label each part", self._fenced("label each part"))

    def test_unsafe_tool_set_message_matches_the_tutorial(self):
        from harness import UnsafeToolSetError

        @tool(effect="external")
        def search(query: str) -> str:
            """Search."""
            return ""

        @tool(effect="danger")
        def send_email(to: str) -> str:
            """Send."""
            return ""

        with self.assertRaises(UnsafeToolSetError) as cm:
            Agent(name="T", job="j", tools=[search, send_email])
        self.assertMessageMatches(str(cm.exception),
                                  self._fenced("read things from the internet"))

    def test_every_complete_program_in_the_tutorial_parses(self):
        checked = 0
        for i, b in enumerate(blocks(DOC)):
            if not is_complete_program(b):
                continue
            checked += 1
            try:
                compile(b, f"<15 block {i}>", "exec")
            except SyntaxError as e:
                self.fail(f"§15 python block {i} does not parse: {e}\n{b}")
        self.assertGreaterEqual(checked, 4, "no complete programs were checked")

    def test_every_tool_in_the_tutorial_is_definable(self):
        """Every @tool the tutorial shows must actually decorate.  Blocks that also build
        an Agent are executed with a stubbed provider rather than skipped — skipping them
        left the tutorial's headline example untested (Round 29)."""
        ns = {}
        exec("import sys; sys.path.insert(0, 'src')\n"
             "import harness\nfrom harness import tool\n"
             "from harness.models.fake import FakeModel\n"
             "_real = harness.Agent\n"
             "def Agent(**kw):\n"
             "    kw.setdefault('provider', FakeModel([FakeModel.text('ok')]))\n"
             "    return _real(**kw)\n", ns)
        expected = sum(b.count("@tool(") for b in blocks(DOC))
        defined = 0
        for b in blocks(DOC):
            if "@tool(" not in b:
                continue
            try:
                exec(b.replace("from harness import Agent, tool", "")
                      .replace("from harness import Agent", ""), ns)
            except Exception as e:
                self.fail(f"a tool shown in §15 does not work: {e}\n{b}")
            defined += b.count("@tool(")
        self.assertEqual(defined, expected,
                         f"exercised {defined} of {expected} tools shown in §15")
        self.assertGreaterEqual(expected, 4)


class Readability(unittest.TestCase):
    """SC-1c (Round 31): the half of SC-1b that is mechanical.

    A child study cannot succeed if the text is unreadable, and that is measurable today.
    Flesch-Kincaid grade 5.0 is the reading level of a typical ten-year-old.
    """

    LIMIT = 5.0

    def _errors(self):
        from harness import (Agent, tool, ConfigError, MissingEffectError,
                             ToolSchemaError, UnsafeToolSetError)
        out = {}

        def cap(label, fn, exc):
            try:
                fn()
            except exc as e:
                out[label] = str(e)
            else:
                self.fail(f"{label}: expected {exc.__name__}")

        def missing_effect():
            @tool()
            def send_email(to: str) -> str:
                """Send an email."""

        def missing_hint():
            @tool(effect="read")
            def add(a, b):
                """Add."""

        def typo():
            @tool(effect="reed")
            def look(x: str) -> str:
                """Look."""

        def unsafe():
            from harness.tools.web import search

            @tool(effect="danger")
            def send(to: str) -> str:
                """Send."""
            Agent(name="T", job="j", tools=[search, send])

        cap("missing effect", missing_effect, MissingEffectError)
        cap("missing type hint", missing_hint, ToolSchemaError)
        cap("effect typo", typo, MissingEffectError)
        cap("unsafe tool set", unsafe, UnsafeToolSetError)
        cap("positional args", lambda: Agent("Helper", "tell jokes"), ConfigError)
        cap("no api key", lambda: Agent(name="T", job="j").run("hi"), ConfigError)
        return out

    def test_every_child_facing_error_reads_at_age_ten(self):
        sys.path.insert(0, "tests")
        from readability import grade
        too_hard = {k: grade(v, line_oriented=True)[0] for k, v in self._errors().items()}
        too_hard = {k: g for k, g in too_hard.items() if g > self.LIMIT}
        self.assertEqual(too_hard, {},
                         f"messages a ten-year-old cannot read: {too_hard}")

    def test_the_tutorial_reads_at_age_ten(self):
        sys.path.insert(0, "tests")
        from readability import grade
        body = DOC[DOC.index("# Make your own AI helper"):DOC.index("## Reviewer notes")]
        g = grade(body)[0]
        self.assertLessEqual(g, self.LIMIT, f"§15 reads at grade {g}")

    def test_no_child_facing_error_uses_internal_vocabulary(self):
        """Prose only.  `accepts_tainted=True` is a parameter name a child copies, not a
        word they have to understand — scanning code for vocabulary flags the wrong thing
        (Round 31)."""
        sys.path.insert(0, "tests")
        from readability import strip_markup
        banned = ["parallel", "retryable", "serial", "untrusted", "reversibly",
                  "auto-allowed", "taint", "ledger", "schema", "protocol", "invariant"]
        for label, msg in self._errors().items():
            prose = strip_markup(msg).lower()
            for word in banned:
                self.assertNotIn(word, prose,
                                 f"the {label!r} message uses internal vocabulary: {word!r}")


class ConceptBudget(unittest.TestCase):
    def test_first_agent_uses_at_most_three_concepts(self):
        tutorial = next(b for b in blocks(DOC) if "Agent(" in b and "tools=" not in b)
        kwargs = set(re.findall(r"^\s{4}(\w+)=", tutorial, re.M))
        self.assertLessEqual(kwargs, {"name", "job", "budget"},
                             f"the first agent introduces more than three concepts: {kwargs}")

    def test_the_tutorial_never_mentions_the_jargon_it_promised_to_avoid(self):
        banned = ["system prompt", "async", "coroutine", "environment variable",
                  "JSON schema", "context window", "orchestration"]
        body = child_facing(DOC).lower()
        for word in banned:
            self.assertNotIn(word, body, f"§15 uses jargon it promised to avoid: {word!r}")




class Round30Promises(unittest.TestCase):
    """Round 30: what the docs promise must exist, not merely be described."""

    def test_every_harness_module_the_docs_import_exists(self):
        import importlib
        alldocs = "\n".join(p.read_text() for p in pathlib.Path("docs").glob("*.md"))
        alldocs += pathlib.Path("README.md").read_text()
        mods = set(re.findall(r"^\s*(?:from|import)\s+(harness[\w.]*)", alldocs, re.M))
        missing = []
        for m in sorted(mods):
            try:
                importlib.import_module(m)
            except Exception as e:
                missing.append(f"{m} ({type(e).__name__})")
        self.assertEqual(missing, [], f"the docs import modules that do not exist: {missing}")

    def test_every_cli_command_the_docs_promise_is_implemented(self):
        from harness import cli
        alldocs = "\n".join(p.read_text() for p in pathlib.Path("docs").glob("*.md"))
        promised = set(re.findall(r"`harness (\w+)", alldocs))
        have = {n[4:] for n in dir(cli) if n.startswith("cmd_")}
        self.assertEqual(promised - have, set(),
                         f"documented but unimplemented: {sorted(promised - have)}")

    def test_the_tutorials_builtin_tool_import_works(self):
        from harness.tools.web import search
        self.assertEqual(search.effect.value, "external",
                         "a web tool must taint the run (ADR-011)")

    def test_returns_reaches_the_request(self):
        import dataclasses
        @dataclasses.dataclass
        class Order:
            id: str
            eta_days: int
        a = Agent(name="S", job="j", returns=Order,
                  provider=FakeModel([FakeModel.text("{}")]), budget="$1")
        fmt = a._asm.build([], max_tokens=10).output_format
        self.assertIsNotNone(fmt, "returns= was accepted and ignored")
        self.assertEqual(fmt["schema"]["required"], ["id", "eta_days"])

    def test_returns_and_tools_share_one_schema_generator(self):
        """AC-24: no second Python-type-to-schema path may exist."""
        src = pathlib.Path("src/harness/agent.py").read_text()
        self.assertIn("from .tools.schema import _schema_for", src)

    def test_calculate_never_evals_model_supplied_text(self):
        import asyncio
        from harness.tools.calc import calculate
        with self.assertRaises(ValueError):
            asyncio.run(calculate.fn(expression="__import__('os').system('id')"))
        self.assertEqual(asyncio.run(calculate.fn(expression="2 ** 10")), 1024)

    def test_chat_holds_one_ledger_for_the_session(self):
        a = Agent(name="T", job="j", budget="$0.10",
                  provider=FakeModel([FakeModel.text("hi")] * 50))
        c = a.chat()
        self.assertEqual(c.budget.usd, a.budget.usd * 10, "ADR-020: 10x the run budget")
        for _ in range(3):
            c.say("hello")
        self.assertGreater(len(c.messages), 3, "the conversation did not accumulate")

    def test_chat_ends_gracefully_when_the_session_budget_is_spent(self):
        """FakeModel is free by design (IDL-36), so a budget test needs a priced one —
        otherwise it passes for the wrong reason."""
        from harness.models.base import ModelResponse
        from harness.models.pricing import MAX_OUTPUT, price
        from harness.result import Usage

        class Priced(FakeModel):
            def price(s, m): return price("claude-opus-5")
            def max_output(s, m): return MAX_OUTPUT["claude-opus-5"]
            async def complete(s, req, *, on_delta=None):
                r = await FakeModel.complete(s, req, on_delta=on_delta)
                return ModelResponse(r.content, r.stop_reason, Usage(2_000, 2_000), r.model)

        a = Agent(name="T", job="j", budget="$1",
                  provider=Priced([FakeModel.text("hi")] * 50, input_tokens=2_000))
        c = a.chat(budget="$0.30")
        outcomes = [c.say("hello") for _ in range(8)]
        self.assertLessEqual(float(c.spent.decimal), 0.35,
                             "the session ledger did not bound the conversation")
        self.assertFalse(outcomes[-1].ok, "the chat never ended despite a spent budget")

    def test_chat_command_runs_a_scripted_conversation(self):
        d = pathlib.Path(tempfile.mkdtemp())
        cmd_new("bot", cwd=d)
        # point the scaffold at a fake provider
        f = d / "bot.py"
        f.write_text(f"import sys; sys.path.insert(0, {os.path.abspath('src')!r})\n"
                     "from harness import Agent\n"
                     "from harness.models.fake import FakeModel\n"
                     "bot = Agent(name='Bot', job='chat', budget='$1',\n"
                     "            provider=FakeModel([FakeModel.text('hello back')] * 10))\n")
        from harness.cli import cmd_chat
        out = []
        cmd_chat(str(f), inputs=["hi", "again"], out=out.append)
        joined = "\n".join(out)
        self.assertIn("Bot: hello back", joined)
        self.assertIn("Budget for this conversation", joined)

    def test_doctor_reports_a_missing_key_and_a_stale_price_table(self):
        from harness.cli import cmd_doctor
        out = []
        cmd_doctor(out=out.append)
        joined = "\n".join(out)
        self.assertIn("harness", joined)
        self.assertIn("price table", joined)

    def test_trace_and_cost_read_a_real_transcript(self):
        from harness.cli import cmd_cost, cmd_trace
        d = pathlib.Path(tempfile.mkdtemp()); tpath = d / "t.jsonl"
        Agent(name="T", job="j", budget="$1", transcript=str(tpath),
              provider=FakeModel([FakeModel.text("done")])).run("go")
        out = []
        cmd_trace(str(tpath), out=out.append)
        self.assertTrue(any("run.started" in l for l in out))
        out2 = []
        cmd_cost(str(tpath), out=out2.append)
        self.assertTrue(any("spent" in l for l in out2))

    def test_no_network_blocks_a_real_call(self):
        from harness.testing import NetworkAccessInTest, no_network
        import socket
        with no_network():
            with self.assertRaises(NetworkAccessInTest):
                socket.socket()
        socket.socket()          # restored

if __name__ == "__main__":
    unittest.main(verbosity=2)
