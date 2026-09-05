"""Two gates that were narrower than the sentence describing them.

`EgressPolicy` promises to stop "trường hợp RÕ RÀNG (host không hề có trong danh sách)"
and then missed four argument shapes, one of which is that obvious case spelled with
capital letters. `PolicyEngine.resolve` degrades a surviving ASK to ALLOW when there is
no approver, which is the right default for the built-in effect ladder and the wrong one
for a policy a user deliberately wrote.
"""
import asyncio
import unittest

from harness import tool
from harness.policy.base import Ruling, ToolCall, Verdict
from harness.policy.builtin import EgressPolicy
from harness.policy.engine import PolicyEngine
from harness.policy.label import Label
from harness.run import RunContext


@tool(effect="external")
def fetch(url: str) -> str:
    """Fetch a URL."""
    return "ok"


@tool(effect="danger")
def send_report(url: str) -> str:
    """Send a report somewhere it cannot be recalled."""
    return "sent"


def ctx():
    return RunContext("r1", "A", 1, Label(), "standard", 100.0)


class EgressSeesEveryShapeItClaimsTo(unittest.TestCase):
    def rule(self, args, spec=fetch, hosts=("docs.python.org",)):
        return EgressPolicy(list(hosts)).check(ToolCall("c1", spec.name, args, spec), ctx())

    def test_the_shapes_that_were_always_caught_still_are(self):
        self.assertEqual(self.rule({"url": "http://evil.example/x"}).verdict, Verdict.DENY)
        self.assertEqual(self.rule({"url": "http://docs.python.org/x"}).verdict, Verdict.ALLOW)
        # urlparse already handled the userinfo trick; keep it measured so a rewrite
        # cannot quietly lose it.
        self.assertEqual(self.rule({"url": "http://docs.python.org@evil.example/"}).verdict,
                         Verdict.DENY)

    def test_an_uppercase_scheme_is_the_obvious_case_spelled_loudly(self):
        """`value.startswith("http")` is case-sensitive. `HTTP://evil.example/` is not a
        clever evasion — it is the exact case the docstring promises to catch.

        The key here is `link`, deliberately. Under `url` (or any other name in
        `_EGRESS_HOST_KEYS`) the value is inspected whatever its scheme, so the
        case-fold is never reached and the same assertion passes with the fold removed
        — measured: reverting to `value.startswith("http")` left this file green until
        the key changed. The scheme comparison only decides anything for an argument
        name the policy does not already recognise.
        """
        self.assertEqual(self.rule({"link": "HTTP://evil.example/"}).verdict, Verdict.DENY)
        # ...and the allowed host must not become a false positive on the way.
        self.assertEqual(self.rule({"link": "HTTP://docs.python.org/x"}).verdict,
                         Verdict.ALLOW)

    def test_a_bare_host_under_a_host_shaped_key_is_folded_too(self):
        """`urlparse` lowercases `.hostname` for us; the `or value` fallback — a bare host
        with no scheme to parse — is the path that needs the fold of its own."""
        self.assertEqual(self.rule({"host": "EVIL.EXAMPLE"}).verdict, Verdict.DENY)
        self.assertEqual(self.rule({"host": "DOCS.PYTHON.ORG"}).verdict, Verdict.ALLOW)

    def test_a_list_of_urls_is_a_normal_tool_signature_not_an_evasion(self):
        self.assertEqual(self.rule({"urls": ["http://docs.python.org/a",
                                             "http://evil.example/b"]}).verdict, Verdict.DENY)
        self.assertEqual(self.rule({"urls": ["http://docs.python.org/a"]}).verdict,
                         Verdict.ALLOW)

    def test_a_nested_mapping_brings_its_own_keys(self):
        self.assertEqual(self.rule({"req": {"url": "http://evil.example/"}}).verdict,
                         Verdict.DENY)
        self.assertEqual(self.rule({"req": {"headers": {"host": "evil.example"}}}).verdict,
                         Verdict.DENY)

    def test_a_danger_tool_that_touches_the_network_is_the_one_worth_inspecting(self):
        """`effect=` grades irreversibility, not whether a call touches the network, and
        `tools/_guess()` assigns it by NAME — `send_report` is `danger` because of the
        word "send", and used to fall out of the allowlist by that same guess."""
        self.assertEqual(self.rule({"url": "http://evil.example/"}, spec=send_report).verdict,
                         Verdict.DENY)

    def test_a_self_referential_argument_costs_a_bounded_walk_not_a_hang(self):
        """`arguments` is model-authored. A policy that raises `RecursionError` would be
        turned into a DENY of an innocent call, which is a denial-of-service with extra
        steps."""
        loop: dict = {"url": "http://docs.python.org/"}
        loop["self"] = loop
        self.assertEqual(self.rule(loop).verdict, Verdict.ALLOW)
        deep: dict = {"url": "http://evil.example/"}
        for _ in range(5000):
            deep = {"n": deep}
        self.rule(deep)   # must return, not raise

    def test_the_known_limits_stay_documented_by_a_test_that_asserts_them(self):
        """A bare host under an unrecognised key still passes, deliberately: detecting it
        means guessing "is this string a hostname" for every string, and `"notes.txt"`
        would become a DENY. This test exists so the limit is a decision on the record
        rather than a surprise — change it and you are changing the decision."""
        self.assertEqual(self.rule({"target": "evil.example"}).verdict, Verdict.ALLOW)


class Refunds:
    """A policy a user deliberately wrote. It asks; it does not decide."""
    name = "big-refunds"

    def check(self, call, ctx):
        return Ruling(Verdict.ASK, "over the limit", self.name)


class AnAskWithNoApproverIsNotAnAllowForAUserPolicy(unittest.TestCase):
    def resolve(self, engine, decision, approve):
        call = ToolCall("c1", "fetch", {"url": "http://docs.python.org/"}, fetch)
        return asyncio.run(engine.resolve(decision, call, ctx(), approve,
                                          require_evidence=False))[0]

    def test_a_user_policys_ask_denies_when_there_is_nobody_to_ask(self):
        engine = PolicyEngine(builtins=(), user=(Refunds(),))
        d = self.resolve(engine, Ruling(Verdict.ASK, "over the limit", "big-refunds"), None)
        self.assertEqual(d.verdict, Verdict.DENY)
        self.assertIn("big-refunds", d.reason)

    def test_the_builtin_effect_ladder_still_degrades_to_allow(self):
        """Deliberately unchanged. `safety="standard"` has to stay usable without an
        `approve=`, and the ladder's ASK is a graded default rather than a statement that
        somebody must be asked. Changing this would break every existing caller."""
        engine = PolicyEngine(builtins=(), user=())
        d = self.resolve(engine, Ruling(Verdict.ASK, "write needs a look", "effect"), None)
        self.assertEqual(d.verdict, Verdict.ALLOW)

    def test_a_user_policy_with_an_approver_is_untouched(self):
        engine = PolicyEngine(builtins=(), user=(Refunds(),))
        d = self.resolve(engine, Ruling(Verdict.ASK, "over the limit", "big-refunds"),
                         lambda c, x: True)
        self.assertEqual(d.verdict, Verdict.ALLOW)


if __name__ == "__main__":
    unittest.main()
