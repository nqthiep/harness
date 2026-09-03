"""The conventions every `Profile` in `examples/` follows, asserted across all three at
once rather than restated per profile.

`Profile`'s contract is two members — `name` and `apply(agent) -> Agent` — and that is
all `Agent.with_profile()` touches. Everything below is CONVENTION on top of it:
promises a profile author makes to a caller, which the type system cannot state and
which `_refuse_if_loosened` does not cover. They were followed by `CodingProfile` and
`ResearchProfile` by imitation; writing them down (`docs/03-public-api.md` §3.7) is only
worth doing if something checks them, so this is that check, and it runs over the real
profiles rather than over a fixture.

Deliberately NOT asserted here: that every profile takes the same parameters. Measured
across the three, the constructors share exactly `name` and `budget` — 18 fields for
`CodingProfile`, 6 for `ResearchProfile`, 15 for `VisionProfile` — because a profile
packages one domain's judgement and `root=` means nothing to a camera. A shared
parameter schema would be the `AgentBuilder` `docs/02-architecture.md` §"what was
proposed and rejected" already turned down.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "examples")

from harness import Agent, Ruling, Verdict
from harness.memory.inmemory import InMemoryStore
from harness.models.fake import FakeModel
from harness.tools import ToolSpec

from coding_profile import CodingProfile
from research_profile import ResearchProfile
from vision_profile import VisionProfile
from vision_tools import Body, Camera, Face, FakeDetector

MISSION = "MISSION-SENTINEL: answer the question in front of you."


class _FakeCapture:
    def isOpened(self) -> bool:
        return True

    def set(self, *_a) -> bool:
        return True

    def read(self):
        import numpy
        return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)

    def release(self) -> None:
        pass


class _KeepsSaying:
    """A caller's own policy, to check profiles append rather than replace."""

    name = "callers-own-policy"

    def check(self, call, ctx) -> Ruling:
        return Ruling(Verdict.ALLOW, "", self.name)


def _profiles(root: Path):
    """One instance of every real profile, built the way its own docs show."""
    box = (140, 70, 240, 240)
    return [
        CodingProfile(root=root, store=InMemoryStore(),
                      tasks_db=str(root / "tasks.db")),
        ResearchProfile(),
        VisionProfile(store=InMemoryStore(), camera=Camera(capture=_FakeCapture()),
                      detector=FakeDetector(faces=(Face(box=box),),
                                            bodies=(Body("đang ngồi"),),
                                            embeddings={box: (1.0, 0.0, 0.1)})),
    ]


def _caller_tool() -> ToolSpec:
    from harness import Effect, tool

    @tool(effect=Effect.READ)
    async def callers_own_tool() -> str:
        "Something the caller brought, which no profile knows about."
        return "ok"

    return callers_own_tool


class EveryRealProfileThat(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="profile-conventions-"))
        self.profiles = _profiles(self.root)

    def _agent(self, **kwargs) -> Agent:
        return Agent(name="Subject", job=MISSION, provider=FakeModel([]), **kwargs)

    def test_names_itself_with_a_non_empty_string(self):
        """`name` is what `ProfileLoosenedSafetyError` and the second-profile refusal
        quote back at the caller. An empty one makes both diagnostics useless."""
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                self.assertIsInstance(profile.name, str)
                self.assertTrue(profile.name.strip())

    def test_keeps_the_tools_the_caller_already_passed(self):
        """"Add, don't replace." A profile that rebuilt `tools=` from scratch would
        silently drop the caller's own `danger` tools — the ones a profile is least
        entitled to touch."""
        mine = _caller_tool()
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                built = self._agent(tools=[mine]).with_profile(profile)
                self.assertIn(mine.name, {t.name for t in built.toolset})

    def test_adds_at_least_one_tool_of_its_own(self):
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                before = len(self._agent().toolset)
                after = len(self._agent().with_profile(profile).toolset)
                self.assertGreater(after, before)

    def test_folds_the_callers_mission_into_the_prompt_rather_than_discarding_it(self):
        """`job=` IS the whole system prompt (`context/assembler.py` assigns it to
        `_system_text`), so a profile that owns a template has to fold the caller's
        mission in as a section. Dropping it loses the only statement of what THIS
        session is for."""
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                built = self._agent().with_profile(profile)
                self.assertIn("MISSION-SENTINEL", built.job)
                self.assertGreater(len(built.job), len(MISSION),
                                   "the profile should add a prompt of its own too")

    def test_declares_a_budget_of_its_own(self):
        """The one sizing knob all three set: how much of a task shape this is, which
        the profile knows and the caller often does not."""
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                self.assertTrue(str(profile.budget).strip())
                built = self._agent().with_profile(profile)
                self.assertIsNotNone(built.budget)

    def test_keeps_the_policies_the_caller_already_set(self):
        mine = _KeepsSaying()
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                built = self._agent(policies=[mine]).with_profile(profile)
                self.assertIn(mine.name,
                              [getattr(p, "name", "") for p in built.policies])

    def test_loosens_no_safety_knob_on_a_tightly_configured_agent(self):
        """`_refuse_if_loosened` would raise, so this passing IS the assertion — but it
        also pins that every profile stays applicable to a hardened agent at all, which
        a profile that insisted on its own `approve=` or `allowed_hosts=` would break."""
        def approve(call, ctx=None):
            return False

        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                before = self._agent(safety="strict", allowed_hosts=["example.com"],
                                     max_asks_per_run=3, approve=approve,
                                     require_approval_evidence=True)
                after = before.with_profile(profile)
                self.assertEqual(after.safety, "strict")
                self.assertEqual(list(after.allowed_hosts or []), ["example.com"])
                self.assertEqual(after.max_asks_per_run, 3)
                self.assertTrue(after.require_approval_evidence)
                self.assertIsNotNone(after.approve)

    def test_grants_itself_neither_accepts_tainted_nor_a_wider_host_list(self):
        """The two grants that decide what untrusted content can reach. A profile may
        ask the caller to write them; it may never write them itself."""
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                before = self._agent()
                after = before.with_profile(profile)
                self.assertEqual(set(after._grants.accepts_tainted),
                                 set(before._grants.accepts_tainted))
                self.assertEqual(list(after.allowed_hosts or []),
                                 list(before.allowed_hosts or []))

    def test_produces_a_byte_stable_prompt_so_a_session_can_rebuild_the_agent(self):
        """`Chat.say()` calls `with_()` every turn where the session has a dollar
        budget, and `Agent.__init__` re-runs the cache-determinism linter each time. A
        profile whose prompt varied would raise `NonDeterministicPromptError` on turn N
        of a conversation rather than at startup."""
        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                first = self._agent().with_profile(profile)
                second = self._agent().with_profile(profile)
                self.assertEqual(first.job, second.job)
                self.assertEqual(first.with_(effort="high").job, first.job)

    def test_is_applied_at_most_once_unless_the_caller_says_otherwise(self):
        """ADR-074, checked over the real profiles rather than only in the core tests:
        every one of them records its name, so the second-profile refusal sees it."""
        from harness.errors import ConfigError

        for profile in self.profiles:
            with self.subTest(profile=type(profile).__name__):
                once = self._agent().with_profile(profile)
                self.assertEqual(once._profiles, (profile.name,))
                with self.assertRaises(ConfigError):
                    once.with_profile(ResearchProfile())


if __name__ == "__main__":
    unittest.main()
