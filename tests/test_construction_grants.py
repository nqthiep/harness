"""Three spellings that used to be trusted: `safety=`, `sensitive=`, `accepts_tainted=`.

All three are short, closed vocabularies typed by hand, all three are matched by exact
string somewhere far from where they were written, and all three used to treat a
near-miss as a silent downgrade rather than as a question. `effect=` — the fourth
spelling of the same shape — has had a did-you-mean since Round 4; these three did not,
and the measurements below are what that cost.

The point of every test here is the SILENCE, not the typo. A refused agent is a bug
report; an agent that quietly runs at a laxer setting than the one written in its
constructor is an incident nobody opens a ticket for.
"""
import unittest

from harness import Agent, tool
from harness.errors import ConfigError
from harness.guards import _check_subagent_safety, _refuse_if_loosened
from harness.models.fake import FakeModel
from harness.tools.registry import ToolSet

RAN: list[str] = []


@tool(effect="read")
def payroll(who: str) -> str:
    """Look up a salary."""
    RAN.append("payroll")
    return "alice earns 120000"


@tool(effect="write")
def publish(text: str) -> str:
    """Send something onward, where it cannot be taken back."""
    RAN.append("publish")
    return "published"


def agent(**kw):
    kw.setdefault("name", "p")
    kw.setdefault("job", "spell")
    kw.setdefault("allowed_hosts", None)
    return Agent(**kw)


class SafetyIsCheckedNotTrusted(unittest.TestCase):
    """`Literal["standard", "strict"]` is an annotation. Nothing used to compare it."""

    def test_both_real_levels_are_accepted_and_kept(self):
        for level in ("standard", "strict"):
            self.assertEqual(agent(safety=level).safety, level)

    def test_a_near_miss_is_refused_and_told_what_it_meant(self):
        with self.assertRaises(ConfigError) as e:
            agent(safety="stict")
        self.assertIn("stict", str(e.exception))
        self.assertIn('Did you mean "strict"?', str(e.exception))

    def test_a_case_typo_is_refused_and_still_gets_the_hint(self):
        # `difflib` compares characters, and "STRICT" shares none with "strict", so the
        # hint is computed on a lowered copy. Without that, the two spellings a person
        # is most likely to reach for get the refusal without the answer.
        for spelling in ("STRICT", "Strict"):
            with self.subTest(spelling=spelling), self.assertRaises(ConfigError) as e:
                agent(safety=spelling)
            self.assertIn('Did you mean "strict"?', str(e.exception))

    def test_a_value_that_is_not_a_string_is_refused_too(self):
        for value in (None, 1, ["strict"]):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                agent(safety=value)

    def test_the_regime_the_typo_used_to_disable(self):
        """The reason this matters, measured rather than asserted.

        One `write` tool, no `approve=`. Under `standard` the write is auto-allowed;
        under `strict` it becomes ASK, and an ASK with no approver is a DENY. Before
        this check, `safety="stict"` took the first branch — the whole strict regime off,
        no exception, no event, nothing in the transcript recording that the level asked
        for was not the level applied.
        """
        def run(level):
            RAN.clear()
            script = [FakeModel.tool_call("publish", {"text": "hi"}),
                      FakeModel.text("done")]
            agent(provider=FakeModel(script), tools=[publish], safety=level).try_run("go")
            return tuple(RAN)

        self.assertEqual(run("standard"), ("publish",))
        self.assertEqual(run("strict"), ())
        # and the third spelling no longer reaches either branch
        with self.assertRaises(ConfigError):
            run("stict")

    def test_the_guards_refuse_readably_instead_of_raising_keyerror(self):
        """`_SAFETY_RANK[...]` used to be a bare lookup in both guards.

        `Agent.__init__` now validates, so no `Agent` this package builds can carry a
        bad level — but these two functions sit BELOW the constructor: `harness.lg`'s
        `build_agent()` reaches them with its own string, and the module docstring says
        a duck-typed stand-in is accepted. The guard whose entire job is a readable
        construction-time refusal must not answer with an internal `KeyError`.
        """
        child = agent(name="child", tools=[payroll], safety="standard")
        with self.assertRaises(ConfigError):
            _check_subagent_safety(ToolSet([child.as_tool()]), "stict")
        before, after = agent(safety="standard"), agent(safety="standard")
        object.__setattr__(after, "safety", "stict")
        with self.assertRaises(ConfigError):
            _refuse_if_loosened(before, after, "a-profile")


class GrantNamesMustNameTools(unittest.TestCase):
    """`sensitive=` and `accepts_tainted=` are matched to tools by name and nowhere else."""

    def test_a_sensitive_typo_is_refused_and_told_what_it_meant(self):
        with self.assertRaises(ConfigError) as e:
            agent(tools=[payroll, publish], sensitive=["payrol"])
        self.assertIn("payrol", str(e.exception))
        self.assertIn("'payroll'", str(e.exception))

    def test_the_correct_spelling_still_gates_the_sink(self):
        """The behaviour the typo silently removed, on the same fixture.

        `payroll` is `read` and marked `sensitive`, so its result raises the run's
        confidentiality label; `publish` is a `write` sink, which may not carry a SECRET
        label onward. With the grant spelled right the sink is refused. With `payrol` it
        used to run — the salary went out, and construction had raised nothing.
        """
        RAN.clear()
        script = [FakeModel.tool_call("payroll", {"who": "alice"}),
                  FakeModel.tool_call("publish", {"text": "alice earns 120000"}, call_id="c2"),
                  FakeModel.text("done")]
        agent(provider=FakeModel(script), tools=[payroll, publish],
              sensitive=["payroll"]).try_run("go")
        self.assertEqual(tuple(RAN), ("payroll",))

    def test_an_accepts_tainted_typo_is_refused_by_the_check_not_by_an_accident(self):
        """This one LOOKED covered and was not.

        A typo in `accepts_tainted=` leaves the `external`+`danger` pair unmatched, so
        `_check_tool_set` refuses the whole SET and the mistake surfaces. That is a
        property of the trifecta rule, not a check on the grant: with no `external` tool
        in the set there is no trifecta, and the same typo used to go through in silence.
        Both shapes must now raise, and the second must raise for the right reason.
        """
        with self.assertRaises(ConfigError) as e:
            agent(tools=[payroll, publish], accepts_tainted=["payrol"])
        self.assertIn("accepts_tainted", str(e.exception))

    def test_an_agent_with_no_tools_is_exempt_only_until_it_has_some(self):
        """The exemption is narrow on purpose, and `with_()` is why it can be.

        A profile's caller writes the grant before the profile supplies the tool
        (`examples/vision_profile.py`), so an empty toolset cannot be an error — there is
        no candidate to suggest and nothing the grant could yet be wrong about. Every
        path that adds tools rebuilds the whole `Agent` through `__init__`, so the check
        runs the moment a toolset exists to check against.
        """
        empty = agent(tools=[], sensitive=["arrives_later"])
        with self.assertRaises(ConfigError):
            empty.with_(tools=[payroll, publish])


if __name__ == "__main__":
    unittest.main()
