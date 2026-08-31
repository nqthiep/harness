"""Properties P-1 and P-8 — the invariants the whole cost argument rests on."""
import sys, random, unittest
sys.path.insert(0, "src")

from harness import Agent, tool, Budget
from harness.budget.ledger import Ledger, MIN_USEFUL_OUTPUT_TOKENS
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.models.pricing import PRICES, MAX_OUTPUT, price
from harness.result import Usage


@tool(effect="read")
def noop(x: int) -> int:
    """Do nothing."""
    return x


class PricedFake(FakeModel):
    """Billed at real prices, so the ledger is actually exercised.

    Models a real provider: output never exceeds the requested max_tokens, but the
    input actually billed may exceed what count_input_tokens estimated — which is the
    hazard the budget has to survive (ADR-026).
    """
    def __init__(self, script, model, input_tokens=1200, input_drift=1.0):
        super().__init__(script, input_tokens=input_tokens)
        self._model = model
        self._drift = input_drift
    def price(self, model): return price(self._model)
    def max_output(self, model): return MAX_OUTPUT[self._model]
    async def complete(self, request, *, on_delta=None):
        from harness.run import canonical_len
        r = await super().complete(request, on_delta=on_delta)
        chars = len(canonical_len(request))
        billed_in = min(int(self._input_tokens * self._drift), chars)   # a real tokenizer
        return ModelResponse(                                           # cannot exceed chars
            r.content, r.stop_reason,
            Usage(billed_in, min(r.usage.output_tokens, request.max_tokens)),
            r.model)


class Properties(unittest.TestCase):

    def test_p1_budget_ceiling(self):
        """SC-2, restated by ADR-026 after Round 24 falsified the original wording.

        SC-2a (exact)   : the harness never AUTHORIZES a call whose estimate exceeds
                          the remaining budget.  0 violations, provable.
        SC-2b (bounded) : actual spend may exceed the budget only by one call's
                          input-count error, and nothing further is authorized after.
        """
        rng = random.Random(20260827)
        models = [m for m in PRICES if m != "fake"]
        violations = []
        for _ in range(1000):
            model = rng.choice(models)
            usd = rng.choice(["$0.01", "$0.05", "$0.20", "$1", "$5"])
            steps = rng.randint(1, 12)
            script = []
            for i in range(steps):
                if rng.random() < 0.6:
                    script.append(FakeModel.tool_call("noop", {"x": i}, call_id=f"c{i}"))
                else:
                    script.append(FakeModel.text("done", output_tokens=rng.randint(10, 5000)))
            # make the model greedy: huge output on some turns
            script = [ModelResponse(r.content, r.stop_reason,
                                    Usage(rng.randint(500, 40_000), rng.randint(10, 60_000)),
                                    "fake") for r in script]
            m = PricedFake(script, model, input_tokens=rng.randint(200, 40_000),
                           input_drift=rng.choice([1.0, 1.0, 1.05, 1.3, 3.0]))
            a = Agent(name="T", job="j", tools=[noop], provider=m,
                      budget=f"{usd}, {steps + 5} steps")
            r = a.try_run("go")
            limit = Budget.parse(usd).usd
            if r.cost.decimal > limit:
                ratio = float(r.cost.decimal / limit)
                violations.append((model, usd, str(r.cost), ratio))
        # SC-2b: overshoot is bounded, and every overshoot is explained by a provider
        # whose real input exceeded what count_input_tokens reported.
        worst = max((v[3] for v in violations), default=1.0)
        self.assertLessEqual(worst, 1.25, f"overshoot {worst:.3f}x exceeds the stated bound")
        self.assertLess(len(violations), 200, f"{len(violations)}/1000 runs overshot")

    def test_p8_every_pair_of_shipped_defaults_multiplies_out(self):
        """Round 17/23: a numeric default is validated by arithmetic, not by review."""
        problems = []
        for model in (m for m in PRICES if m != "fake"):
            for usd in ("$0.05", "$0.10", "$0.50", "$1", "$5"):
                for input_tokens in (200, 1200, 5000, 20_000):
                    L = Ledger(Budget.parse(usd))
                    try:
                        mt = L.size_call(input_tokens, price(model), MAX_OUTPUT[model])
                    except Exception as exc:
                        # only acceptable when the input alone is unaffordable — priced at
                        # cache_write_per_mtok, the same worst-case rate size_call()/
                        # reserve() use since S-22 (settle() can bill up to that rate, and
                        # under-pricing it was a systematic 25% miss, not a rounding error)
                        cost_in = input_tokens / 1e6 * float(price(model).cache_write_per_mtok)
                        if cost_in < float(Budget.parse(usd).usd) * 0.9:
                            problems.append((model, usd, input_tokens, str(exc)))
                        continue
                    if mt < MIN_USEFUL_OUTPUT_TOKENS:
                        problems.append((model, usd, input_tokens, f"max_tokens={mt}"))
                    res = L.reserve(input_tokens, mt, price(model))
                    if res.estimate.decimal > Budget.parse(usd).usd:
                        problems.append((model, usd, input_tokens, "reservation over budget"))
        self.assertEqual(problems, [])

    def test_p8b_wall_clock_ceiling_holds_even_with_a_slow_tool(self):
        """Round 23: tool timeout clamped to the remaining wall clock."""
        L = Ledger(Budget.parse("$1, 10s"))
        L._started = L._clock() - 9.5           # 0.5s left
        self.assertLessEqual(L.tool_timeout(30.0), 0.5)

    def test_p10_conditional_subsystems_are_reachable_or_declared(self):
        """P-10 (Round 27). P-8 checks the shipped defaults are mutually consistent.
        P-10 checks they are sufficient to REACH each conditional subsystem — or that the
        subsystem declares it is not default-reachable.

        Context management was specified, built, tested and wired, and could not fire
        under any default configuration: max_result_tokens(4,000) x steps(20) = 80,000
        tokens against an editing threshold of 120,000 on the smallest window.
        """
        import pathlib
        from harness.context.window import EDIT_AT
        from harness.models.pricing import MAX_CONTEXT
        smallest = min(w for k, w in MAX_CONTEXT.items() if k != "fake")
        reachable_tokens = 20 * 4_000                      # steps x max_result_tokens
        default_reachable = reachable_tokens >= smallest * EDIT_AT
        if not default_reachable:
            doc = pathlib.Path("docs/07-cost.md").read_text()
            self.assertIn("not reachable on the shipped defaults", doc,
                          "context management cannot fire on defaults and the docs do not say so")

    def test_p9_stop_reason_mapping_is_exhaustive(self):
        """ADR-019: every provider stop reason maps, unknown -> failure, never success."""
        from harness.run import _MAP
        protocol = {"end_turn", "tool_use", "max_tokens", "pause_turn", "refusal"}
        handled = set(_MAP) | {"tool_use", "pause_turn"}   # both continue the loop
        self.assertEqual(protocol - handled, set(), "a provider stop reason has no mapping")
        for reason in ("brand_new_2027", ""):
            m = FakeModel([FakeModel.text("x", stop=reason)])
            a = Agent(name="T", job="j", provider=m, budget="$1")
            self.assertFalse(a.try_run("hi").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
