"""M4 executed: memory stores, plugin registry, subagents."""
import asyncio, os, sys, tempfile, unittest
sys.path.insert(0, "src")

from harness import Agent, tool, Effect, ConfigError
from harness.memory import InMemoryStore, SqliteStore
from harness.memory.sqlite import SCHEMA_VERSION
from harness.models.fake import FakeModel
from harness.plugins import PluginRegistry
from harness.tools import EFFECT_PROFILES


@tool(effect="read")
def peek(x: int) -> int:
    """Peek."""
    return x

@tool(effect="external")
def fetch(url: str) -> str:
    """Fetch."""
    return "page"

@tool(effect="danger")
def nuke(x: int) -> str:
    """Nuke."""
    return "gone"


def run(coro): return asyncio.run(coro)


class StoreConformance:
    """One suite, two implementations."""
    def make(self): raise NotImplementedError

    def test_put_get_delete(self):
        s = self.make()
        run(s.put("a", "one"))
        self.assertEqual(run(s.get("a")), "one")
        run(s.delete("a"))
        self.assertIsNone(run(s.get("a")))
        run(s.close())

    def test_missing_key_is_none_not_an_error(self):
        s = self.make()
        self.assertIsNone(run(s.get("nope")))
        run(s.close())

    def test_ttl_is_enforced_on_read_not_only_by_a_sweep(self):
        s = self.make()
        run(s.put("k", "v", ttl_s=-1))       # already expired
        self.assertIsNone(run(s.get("k")), "a TTL that needs a sweep is not a TTL")
        run(s.close())

    def test_search_finds_by_key_and_value(self):
        s = self.make()
        run(s.put("colour", "the sky is blue"))
        run(s.put("food", "pizza"))
        hits = run(s.search("blue"))
        self.assertEqual([m.key for m in hits], ["colour"])
        run(s.close())

    def test_overwrite_updates_rather_than_duplicating(self):
        s = self.make()
        run(s.put("k", "v1")); run(s.put("k", "v2"))
        self.assertEqual(run(s.get("k")), "v2")
        run(s.close())


class TestInMemory(StoreConformance, unittest.TestCase):
    def make(self): return InMemoryStore()


class TestSqlite(StoreConformance, unittest.TestCase):
    def make(self):
        self.path = os.path.join(tempfile.mkdtemp(), "m.db")
        return SqliteStore(self.path)

    def test_a_newer_schema_refuses_rather_than_guessing(self):
        s = self.make()
        s._db.execute("UPDATE schema_meta SET version=?", (SCHEMA_VERSION + 1,))
        s._db.commit(); run(s.close())
        with self.assertRaises(ConfigError):
            SqliteStore(self.path)

    def test_two_connections_to_one_file(self):
        a = self.make(); b = SqliteStore(self.path)
        run(a.put("k", "v"))
        self.assertEqual(run(b.get("k")), "v")
        run(a.close()); run(b.close())

    def test_values_must_be_strings(self):
        s = self.make()
        with self.assertRaises(Exception):
            run(s.put("k", {"not": "a string"}))   # STRICT rejects it
        run(s.close())


class Plugins(unittest.TestCase):
    def test_a_plugin_cannot_exceed_its_declared_ceiling(self):
        r = PluginRegistry()
        with self.assertRaises(ConfigError) as cm:
            r.register("sneaky", [nuke], provides="read")
        self.assertIn("more power than it declared", str(cm.exception))

    def test_registering_within_the_ceiling_works(self):
        r = PluginRegistry()
        r.register("fine", [peek], provides="read")
        self.assertEqual([t.name for t in r.tools()], ["peek"])
        self.assertEqual(r.source_of("peek"), "fine")

    def test_an_api_version_mismatch_fails_at_registration(self):
        r = PluginRegistry()
        with self.assertRaises(ConfigError):
            r.register("old", [peek], provides="read", api_version=99)

    def test_two_plugins_cannot_shadow_one_tool_name(self):
        r = PluginRegistry()
        r.register("a", [peek], provides="read")
        with self.assertRaises(ConfigError):
            r.register("b", [peek], provides="read")

    def test_discovery_is_off_by_default(self):
        import inspect
        src = inspect.getsource(Agent.__init__)
        self.assertNotIn("discover()", src,
                         "entry-point discovery must never run unless asked (ADR-008)")


class Subagents(unittest.TestCase):
    def test_effect_is_the_maximum_of_the_childs_tools(self):
        child = Agent(name="Reader", job="read", tools=[peek, fetch],
                      provider=FakeModel([]), budget="$1")
        self.assertIs(child.as_tool().effect, Effect.EXTERNAL,
                      "a parent must not gain capability by wrapping a child")

    def test_a_read_only_child_is_a_read_tool(self):
        child = Agent(name="R", job="read", tools=[peek], provider=FakeModel([]), budget="$1")
        self.assertIs(child.as_tool().effect, Effect.READ)

    def test_wrapping_an_external_child_beside_a_danger_tool_is_rejected(self):
        child = Agent(name="R", job="read", tools=[fetch], provider=FakeModel([]), budget="$1")
        with self.assertRaises(Exception):
            Agent(name="Parent", job="j", tools=[child.as_tool(), nuke],
                  provider=FakeModel([]), budget="$1")

    def test_a_subagent_actually_runs_and_returns_its_text(self):
        child = Agent(name="Reader", job="summarize",
                      provider=FakeModel([FakeModel.text("the summary")]), budget="$1")
        parent = Agent(name="Boss", job="delegate", tools=[child.as_tool()],
                       provider=FakeModel([FakeModel.tool_call("ask_reader", {"task": "go"}),
                                           FakeModel.text("done")]), budget="$5")
        r = parent.run("delegate")
        self.assertIn("the summary", str(r.messages[2]["content"][0]["content"]))

    def test_a_child_that_stops_early_does_not_crash_the_parent(self):
        child = Agent(name="Reader", job="summarize",
                      provider=FakeModel([FakeModel.text("x", stop="max_tokens")]),
                      budget="$1")
        parent = Agent(name="Boss", job="delegate", tools=[child.as_tool()],
                       provider=FakeModel([FakeModel.tool_call("ask_reader", {"task": "go"}),
                                           FakeModel.text("done")]), budget="$5")
        r = parent.run("delegate")
        self.assertTrue(r.ok)
        self.assertIn("stopped", str(r.messages[2]["content"][0]["content"]))


class SubagentBudget(unittest.TestCase):
    """§06.4's two budget claims, which Round 28 found documented and unenforced."""

    def _priced(self, script, inp=2_000):
        from harness.models.pricing import MAX_OUTPUT, price
        from harness.models.base import ModelResponse
        from harness.result import Usage
        class P(FakeModel):
            def price(s, m): return price("claude-opus-5")
            def max_output(s, m): return MAX_OUTPUT["claude-opus-5"]
            async def complete(s, req, *, on_delta=None):
                r = await FakeModel.complete(s, req, on_delta=on_delta)
                return ModelResponse(r.content, r.stop_reason,
                                     Usage(s._input_tokens, 2_000), r.model)
        return P(script, input_tokens=inp)

    def _delegating(self, parent_budget, n=8):
        child = Agent(name="R", job="read", budget="$5",
                      provider=self._priced([FakeModel.text("s")] * 80))
        return Agent(name="B", job="d", tools=[child.as_tool()], budget=parent_budget,
                     provider=self._priced(
                         [FakeModel.tool_call("ask_r", {"task": "go"}, call_id=f"c{i}")
                          for i in range(n)] + [FakeModel.text("done")]))

    def test_child_spend_is_charged_to_the_parent(self):
        r = self._delegating("$2").try_run("go")
        self.assertGreater(r.cost.decimal, 0,
                           "delegated spend was invisible to the parent (reported $0.0000)")

    def test_parallel_children_divide_the_headroom_rather_than_each_claiming_it(self):
        # Each child READ the same remaining budget before Round 28 and each claimed all
        # of it — a TOCTOU on the ledger.  A hold makes them divide it.
        for budget in ("$0.50", "$1", "$2", "$5"):
            r = self._delegating(budget).try_run("go")
            limit = float(budget.strip("$"))
            self.assertLessEqual(float(r.cost.decimal) / limit, 1.25,
                                 f"{budget}: delegation overshot by more than the stated bound")

    def test_the_bound_compounds_one_level_per_delegation_and_is_documented(self):
        import pathlib
        doc = pathlib.Path("docs/07-cost.md").read_text()
        self.assertIn("compounds one level per delegation", doc,
                      "SC-2b's per-ledger bound composes with depth; the docs must say so")


if __name__ == "__main__":
    unittest.main(verbosity=2)
