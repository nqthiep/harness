"""What `CodingProfile.apply()` produces TODAY, pinned so a refactor has to admit it
changed something.

Written BEFORE any restructuring of `apply()`, and for one reason: the claim "this
refactor is behavior-preserving, verified by the existing test suite passing unchanged"
was unfalsifiable. `tests/test_coding_profile.py` has three tests and all three are
about `Store` lifetime; `tests/test_profile.py` exercises core's `with_profile` guards,
not this profile's output. Nothing asserted the system prompt, the toolset, the effect
classification, the policy order, or that the verification feedback loop fires at all.
So "the tests still pass" proved nothing about behavior, in a repo whose stated standard
is that a claim comes with a measurement.

Every expected value below was MEASURED against the current implementation, not
predicted. When one of these fails, that is the point: read the diff and decide whether
the change was intended, then update the constant in the same commit that changes the
behavior — never separately, and never by re-running a generator that would launder an
accident into a new baseline.

Two of these guard findings from an independent design review of a proposed `Faculty`
refactor:

* `test_effect_classification_of_every_tool_is_pinned` — the union of tool EFFECTS is
  what the safety engine reasons over (`policy/builtin.py::check_flow`), and a
  refactor that rebundles tools can change that union without changing any tool. It
  also pins the fact the review's threat analysis turns on: `run_command`/`run_shell`
  are `write`, not `danger`, so `_check_tool_set`'s lethal-trifecta refusal (scoped to
  `external`+`danger`, `agent.py`) does NOT fire for them.
* `test_the_verification_feedback_loop_still_fires_on_write_tools` — `with_verification`
  works by APPENDING to a tool's result string. Any later wrapper in the stack that
  returns only `await inner(**kwargs)` — or truncates, or reformats — silently deletes
  the feedback loop while every other test here keeps passing. Behavior, not identity:
  comparing `spec.fn` against a fresh `CodeTools(...)`'s bound method is useless
  (every instance makes new bound methods, so all eleven look "replaced"), which is
  why this calls the tool and looks for the marker.
"""
import asyncio
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "examples")

from harness import Agent
from harness.memory.inmemory import InMemoryStore
from harness.models.fake import FakeModel
from harness.policy.label import Integrity
from harness.tools import EFFECT_PROFILES

from coding_profile import CodingProfile

#: `(enable_shell, enable_findings)` — the whole flag matrix `apply()` branches on.
COMBOS = ((False, False), (False, True), (True, False), (True, True))

_CODE_TOOLS = (
    "edit_source", "git_commit", "git_diff", "git_status", "list_files", "outline",
    "read_source", "refresh_codebase_docs", "run_tests", "search_code", "write_source",
)
_TASK_TOOLS = ("add_task", "block_task", "finish_task", "list_tasks", "start_task")
_FINDINGS_TOOLS = ("add_finding", "list_findings")
_SHELL_TOOLS = ("run_command", "run_shell")

#: Measured. `ask_reader` is the read-only explorer subagent (`as_tool()`).
EXPECTED_TOOLS = {
    combo: sorted(
        _CODE_TOOLS + _TASK_TOOLS + ("ask_reader",)
        + (_SHELL_TOOLS if combo[0] else ())
        + (_FINDINGS_TOOLS if combo[1] else ())
    )
    for combo in COMBOS
}

#: Measured, and the one table here with direct safety consequences. Note `run_tests`
#: and `refresh_codebase_docs` are `write` (they run a subprocess / rewrite a file), and
#: the two shell tools are `write` as well — deliberately, `shell_tools.py`'s own
#: docstring records why. Nothing in this profile is `external` or `danger`: a caller's
#: own `danger` tools arrive through `Agent(tools=...)`, never from the profile.
EXPECTED_EFFECTS = {
    "add_finding": "write", "add_task": "write", "ask_reader": "read",
    "block_task": "write", "edit_source": "write", "finish_task": "write",
    "git_commit": "write", "git_diff": "read", "git_status": "read",
    "list_files": "read", "list_findings": "read", "list_tasks": "read",
    "outline": "read", "read_source": "read", "refresh_codebase_docs": "write",
    "run_command": "write", "run_shell": "write", "run_tests": "write",
    "search_code": "read", "start_task": "write", "write_source": "write",
}

#: Measured. Order matters: `PolicyEngine` composes verdicts with `max()` so the outcome
#: is order-independent, but the DecisionLog records them in this order and a dropped
#: policy is exactly what `_refuse_if_loosened` exists to catch.
EXPECTED_POLICIES = {
    (False, False): ["protected-paths"],
    (False, True): ["protected-paths"],
    (True, False): ["protected-paths", "shell-command"],
    (True, True): ["protected-paths", "shell-command"],
}

#: Measured, with the workspace path normalised to `<ROOT>` (it is a fresh tmpdir per
#: run). The digest is the tripwire; `EXPECTED_PROMPT_SECTIONS` below makes the failure
#: readable instead of just "a hash moved".
EXPECTED_PROMPT = {
    (False, False): (2356,
                     "60c941e11b88f6d3e85557de1a5fa0dd3a7a4c312c7e1f93f15801642cc03480"),
    (False, True): (2695,
                    "a84c0cd839d99053696f65134c93aa553b5cf316f38f56a357cb0257680a5be9"),
    (True, False): (2886,
                    "a818c8bcbb533d4cae6f833ab34f3c5b73a4e729e91aaf336b882664813edd43"),
    (True, True): (3225,
                   "e5ed348d766a49f1f327e760e58823a19b00002b9574111b4ffcb029d93b0f2a"),
}

#: Measured. `# Running other commands` appears only with `enable_shell=True`; the
#: findings clause is a numbered sub-item inside `# How to work`, not its own heading,
#: which is why `enable_findings` moves the digest but not this list.
EXPECTED_PROMPT_SECTIONS = {
    False: ["# Your task for this session", "# What finishing means", "# How to work",
            "# Being wrong", "# Hard rules"],
    True: ["# Your task for this session", "# What finishing means", "# How to work",
           "# Running other commands", "# Being wrong", "# Hard rules"],
}

#: Measured. `with_smart_truncation` raises the result cap for logs whose payoff is at
#: the tail; everything else keeps the library default from `tools/__init__.py`.
DEFAULT_RESULT_TOKENS = 4_000
WIDENED_RESULT_TOKENS = 12_000
WIDENED_TOOLS = frozenset({"run_tests", "git_diff", "run_command", "run_shell"})

#: A stand-in for the project's own linter: always fails, always prints one marker to
#: stderr, needs nothing installed. `Verifier` appends a report only when the command
#: exits non-zero AND wrote something, so both halves are load-bearing.
FAKE_LINTER = (("sh", "-c", "echo LINT_MARKER >&2; exit 1"),)


def _build(root: Path, shell: bool, findings: bool, **overrides) -> Agent:
    """One agent per combination, built exactly the way a caller would.

    `store=InMemoryStore()` rather than the default `SqliteStore(self.tasks_db)`: this
    test builds many agents in one process, which is the loop shape ADR-076 documents as
    a real leak when nothing owns closing the store. An in-memory store needs no close
    and keeps the test hermetic — no `coding_session.db` written next to the tests.
    """
    profile = CodingProfile(root=root, enable_shell=shell, enable_findings=findings,
                            store=InMemoryStore(), **overrides)
    return Agent(name="Coder", job="MISSION",
                 provider=FakeModel([])).with_profile(profile)


class CodingProfileProducesThat(unittest.TestCase):
    def setUp(self) -> None:
        # A fresh, EMPTY workspace: `_read_instructions` reads AGENTS.md/CLAUDE.md/
        # .agentrules from the root at construction, so a repo file leaking in here
        # would change the prompt digest for reasons that have nothing to do with the
        # profile.
        self.root = Path(tempfile.mkdtemp(prefix="coding-profile-char-"))

    def test_the_toolset_is_exactly_this_for_each_flag_combination(self):
        for shell, findings in COMBOS:
            with self.subTest(shell=shell, findings=findings):
                agent = _build(self.root, shell, findings)
                self.assertEqual(sorted(t.name for t in agent.toolset),
                                 EXPECTED_TOOLS[(shell, findings)])

    def test_effect_classification_of_every_tool_is_pinned(self):
        for shell, findings in COMBOS:
            with self.subTest(shell=shell, findings=findings):
                agent = _build(self.root, shell, findings)
                got = {t.name: t.effect.value for t in agent.toolset}
                self.assertEqual(got, {n: EXPECTED_EFFECTS[n] for n in got})
                # The property the safety engine actually reasons over, stated as its
                # own assertion so a change reads as what it is: this profile
                # contributes no untrusted-content source and no irreversible tool.
                self.assertEqual(sorted(set(got.values())), ["read", "write"])

    def test_the_policies_and_their_order_are_pinned(self):
        for shell, findings in COMBOS:
            with self.subTest(shell=shell, findings=findings):
                agent = _build(self.root, shell, findings)
                names = [getattr(p, "name", type(p).__name__) for p in agent.policies]
                self.assertEqual(names, EXPECTED_POLICIES[(shell, findings)])

    def test_the_system_prompt_is_byte_for_byte_what_it_was(self):
        for shell, findings in COMBOS:
            with self.subTest(shell=shell, findings=findings):
                agent = _build(self.root, shell, findings)
                job = agent.job.replace(str(self.root), "<ROOT>")
                headings = [ln for ln in job.splitlines() if ln.startswith("# ")]
                # Asserted first: a heading diff is legible, a hash diff is not.
                self.assertEqual(headings, EXPECTED_PROMPT_SECTIONS[shell])
                want_len, want_sha = EXPECTED_PROMPT[(shell, findings)]
                self.assertEqual(len(job), want_len)
                self.assertEqual(hashlib.sha256(job.encode()).hexdigest(), want_sha)

    def test_the_caller_own_mission_survives_as_a_section(self):
        # `Agent(job=...)` is the whole system prompt (`context/assembler.py` assigns
        # it to `_system_text`), so a profile that owns a template has to fold the
        # caller's mission in rather than discard it. Cheap to assert, and the exact
        # thing ADR-074 measured going wrong when two profiles both rebuild the prompt.
        agent = _build(self.root, False, False)
        self.assertIn("# Your task for this session", agent.job)
        self.assertIn("MISSION", agent.job)

    def test_smart_truncation_widens_exactly_these_result_caps(self):
        for shell, findings in COMBOS:
            with self.subTest(shell=shell, findings=findings):
                agent = _build(self.root, shell, findings)
                for spec in agent.toolset:
                    want = (WIDENED_RESULT_TOKENS if spec.name in WIDENED_TOOLS
                            else DEFAULT_RESULT_TOKENS)
                    self.assertEqual(spec.max_result_tokens, want, spec.name)

    def test_the_verification_feedback_loop_still_fires_on_write_tools(self):
        agent = _build(self.root, False, False, verify_commands=FAKE_LINTER)
        by_name = {s.name: s for s in agent.toolset}

        wrote = str(asyncio.run(by_name["write_source"].fn(path="x.py", text="y = 1\n")))
        self.assertIn("LINT_MARKER", wrote,
                      "write_source must return the project's own checker output for "
                      "the file it just touched — that feedback loop IS the profile's "
                      "highest-leverage behavior change")

        edited = str(asyncio.run(
            by_name["edit_source"].fn(path="x.py", old="y = 1", new="y = 2")))
        self.assertIn("LINT_MARKER", edited)

        # Scoped, not global: a read tool paying for a linter run on every call would
        # be a different profile than the one measured here.
        read = str(asyncio.run(by_name["read_source"].fn(path="x.py")))
        self.assertNotIn("LINT_MARKER", read)

    def test_wrapping_a_tool_never_changes_what_the_harness_knows_about_it(self):
        """`dataclasses.replace(spec, fn=...)` is the whole reason wrapping is safe —
        and the mechanism is stronger than "the wrapper is careful".

        `parallel_safe`, `retryable`, `emits`, and the two decision verdicts are NOT
        fields on `ToolSpec` (`name, description, input_schema, effect, fn, timeout_s,
        max_result_tokens, source, subagent, server`). They are looked up from
        `EFFECT_PROFILES[spec.effect]` at dispatch time, so a wrapper that replaces
        `fn` cannot reach them at all — it would have to change `effect` itself, which
        this test pins per tool. An earlier version of this test asserted
        `spec.parallel_safe` and failed with `AttributeError`; the failure was the more
        useful fact.
        """
        agent = _build(self.root, True, True, verify_commands=FAKE_LINTER)
        for spec in agent.toolset:
            with self.subTest(tool=spec.name):
                self.assertEqual(spec.effect.value, EXPECTED_EFFECTS[spec.name])
                profile = EFFECT_PROFILES[spec.effect]
                if spec.effect.value == "write":
                    self.assertFalse(profile.parallel_safe,
                                     "a write tool must stay non-parallel-safe")
                    self.assertFalse(profile.retryable,
                                     "a write tool must never be auto-retried")
                # The F-1-relevant invariant, stated where it is checkable: nothing
                # this profile contributes is an untrusted-content SOURCE. The moment
                # a perception/vision tool joins this toolset, `emits` for that tool
                # becomes UNTRUSTED and this assertion is what will say so.
                self.assertIs(profile.emits.integrity, Integrity.TRUSTED)


if __name__ == "__main__":
    unittest.main()
