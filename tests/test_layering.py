"""The dependency directions, as assertions.

Two of them were already violated when this file was written, and both had been invisible
because nothing looked (ADR-098):

* `agent._resolve_provider` did `from .cli import api_key` — **core's run path importing
  the CLI package.** Credential resolution is a domain concern the CLI consumes, not one
  it owns; a library used with no CLI at all still has to find a key.
* `middleware.with_middleware` did a function-local `from .agent import _resolve_provider`
  to dodge the cycle with `agent.py`, which imports `middleware._run_scope` at module
  level. A worked-around import cycle is a missing module announcing itself.

Both are gone. These tests are what stops them coming back, because a layering violation
never breaks a test on its own — it just makes the next one harder to write.
"""
import ast
import pathlib
import unittest
import _paths

CORE = _paths.CORE

#: Layers, outermost first. A module may import its own layer and anything BELOW it,
#: never above. `cli` and `server` are entry points: they exist to be imported BY a user,
#: not by the library.
ENTRY_POINTS = ("harness.cli", "harness.server")


#: Below this, `modules()` is not measuring the package — it is measuring a typo.
#: The number is deliberately far from the real count (102 at the time of writing) and
#: deliberately not derived from it: a guard that tracks the thing it guards moves every
#: time the package does and stops meaning anything.
_MIN_PLAUSIBLE_MODULES = 20


def modules() -> dict[str, pathlib.Path]:
    """Every module in core, by dotted name.

    The guard at the end is the actual fix for what `cd /tmp && pytest` exposed. The
    anchor being wrong was how it got triggered; the DEFECT is that `rglob` on a
    directory that is not there yields nothing and raises nothing, so an empty module
    graph answered every question below with "no cycles, no violations" and four tests
    passed over nothing. An empty measurement and a clean one have to be
    distinguishable, or the cheapest way to make this file green is to break it.
    """
    out = {}
    for f in CORE.rglob("*.py"):
        name = ".".join(f.relative_to(CORE.parent).with_suffix("").parts)
        out[name.removesuffix(".__init__")] = f
    if len(out) < _MIN_PLAUSIBLE_MODULES:
        raise AssertionError(
            f"only {len(out)} modules found under {CORE} — this file cannot prove "
            f"anything about a package it cannot see, and reporting 'no cycles' over "
            f"an empty graph is worse than failing.")
    return out


def packages() -> set[str]:
    """Which module names are PACKAGES. Relative-import resolution depends on it: inside
    `pkg/__init__.py`, `from .x` means `pkg.x`; inside `pkg/mod.py` it means `pkg.x` too,
    but the anchor is computed differently. Getting this wrong is not a small error — the
    first version of this file resolved every relative import to a name that does not
    exist, found ZERO edges, and passed every test below by measuring nothing."""
    return {".".join(f.relative_to(CORE.parent).parent.parts)
            for f in CORE.rglob("__init__.py")}


def imports_of(path: pathlib.Path, module: str, *, module_level_only: bool) -> set[str]:
    """Every `harness.*` module this file imports, resolved to a real module name."""
    tree = ast.parse(path.read_text())
    known = set(modules())
    is_package = module in packages()
    skip: set[int] = set()
    if module_level_only:
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                skip |= {id(n) for n in ast.walk(fn)}

    # `if TYPE_CHECKING:` imports are erased at runtime, so they are not dependencies —
    # they are how Python spells a mutual type reference, and forbidding them would mean
    # forbidding `Profile.apply(self, agent: Agent) -> Agent` from naming its own
    # argument type. `policy/base.py` says so in its own docstring. Excluded here, which
    # is what makes the remaining cycles the real ones.
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            name = (test.id if isinstance(test, ast.Name)
                    else getattr(test, "attr", None))
            if name == "TYPE_CHECKING":
                for stmt in node.body:
                    skip |= {id(n) for n in ast.walk(stmt)}

    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        targets = []
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:                                   # relative
                # `from .x` inside a package's `__init__` anchors at the package; inside
                # a module it anchors at the module's package. Then each extra dot goes
                # up one more.
                parts = module.split(".")
                anchor = parts if is_package else parts[:-1]
                anchor = anchor[: max(0, len(anchor) - (node.level - 1))]
                base = ".".join(anchor + ([base] if base else []))
            if base.startswith("harness"):
                targets = [base] + [f"{base}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            targets = [a.name for a in node.names if a.name.startswith("harness")]
        for t in targets:
            while t and t not in known:
                t = t.rsplit(".", 1)[0] if "." in t else ""
            if t and t != module:
                found.add(t)
    return found


def graph(*, module_level_only: bool) -> dict[str, set[str]]:
    return {m: imports_of(p, m, module_level_only=module_level_only)
            for m, p in modules().items()}


def cycles_in(g: dict[str, set[str]]) -> list[list[str]]:
    """Every simple cycle, as a path. A package importing its own submodule is not one —
    those edges are dropped first, since `harness.policy` re-exporting
    `harness.policy.engine` is a package boundary, not a dependency loop."""
    def real(a: str, b: str) -> bool:
        return not (a.startswith(b + ".") or b.startswith(a + "."))

    trimmed = {m: {d for d in ds if real(m, d)} for m, ds in g.items()}
    found: list[list[str]] = []
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        path.append(node)
        for nxt in sorted(trimmed.get(node, ())):
            if state.get(nxt) == 1:
                found.append(path[path.index(nxt):] + [nxt])
            elif state.get(nxt, 0) == 0:
                visit(nxt)
        path.pop()
        state[node] = 2

    for m in sorted(trimmed):
        if state.get(m, 0) == 0:
            visit(m)
    return found


class TheLibraryDoesNotImportItsOwnEntryPoints(unittest.TestCase):
    def test_no_core_module_imports_the_cli_or_the_server(self):
        """At ANY level, function-local imports included: a deferred import of the CLI is
        the same inversion, just harder to see. `agent.py` had exactly this."""
        offenders = []
        for module, deps in graph(module_level_only=False).items():
            if module.startswith(ENTRY_POINTS):
                continue
            for entry in ENTRY_POINTS:
                if entry in deps:
                    offenders.append(f"{module} -> {entry}")
        self.assertEqual(offenders, [], "core must not depend on an entry point")

    def test_the_entry_points_may_import_the_library(self):
        """The direction that IS allowed, asserted so the test above cannot be satisfied
        by making everything import nothing."""
        self.assertIn("harness.credentials", graph(module_level_only=True)["harness.cli"])


class CredentialsIsBelowEverythingThatNeedsIt(unittest.TestCase):
    def test_it_imports_no_facade_no_entry_point_and_no_engine(self):
        deps = imports_of(CORE / "credentials.py", "harness.credentials",
                          module_level_only=False)
        for forbidden in ("harness.agent", "harness.middleware", "harness.cli",
                          "harness.run", "harness.lg"):
            self.assertNotIn(forbidden, deps,
                             f"credentials must sit below {forbidden}")

    def test_all_three_provider_call_sites_use_the_one_resolver(self):
        """`agent` (classic and durable) and `middleware`. A fourth copy is how
        `with_middleware` came to build `AnthropicProvider()` itself and lose the clear
        no-key error (ADR-086)."""
        import re
        for path in (CORE / "agent.py", CORE / "middleware.py"):
            body = path.read_text()
            self.assertIn("resolve_provider(", body, f"{path.name} should use it")
            self.assertIsNone(
                re.search(r"AnthropicProvider\(\)", body),
                f"{path.name} builds the default provider itself instead of resolving it")


class TheGuardsSitBelowBothEngines(unittest.TestCase):
    """The construction-time refusals — `_check_tool_set`, `_check_subagent_safety`,
    `_refuse_if_loosened` — are the one piece of `agent.py` a SECOND backend needs. While
    they lived in the facade, `harness.lg` imported `harness.agent` to reach them and
    `agent.py` deferred its own import of `lg` into a function body to dodge the cycle
    (ADR-098)."""

    def test_guards_import_no_engine_and_no_facade(self):
        deps = imports_of(CORE / "guards.py", "harness.guards", module_level_only=False)
        for forbidden in ("harness.agent", "harness.lg", "harness.run",
                          "harness.middleware", "harness.cli"):
            self.assertNotIn(forbidden, deps)

    def test_both_engines_reach_the_guards_without_reaching_each_other(self):
        g = graph(module_level_only=False)
        self.assertIn("harness.guards", g["harness.agent"])
        self.assertIn("harness.guards", g["harness.lg"])
        self.assertNotIn("harness.agent", g["harness.lg"],
                         "the durable backend must not import the facade")


class ThereAreNoImportCycles(unittest.TestCase):
    def test_no_module_level_cycle_anywhere_in_core(self):
        found = cycles_in(graph(module_level_only=True))
        self.assertEqual(
            [" -> ".join(c) for c in found], [],
            "a module-level cycle means two modules are really one, or one is missing")

    def test_no_cycle_even_counting_function_local_imports(self):
        """The stricter reading, and the one that matters: a cycle deferred into a
        function body is still a cycle — `middleware -> agent -> middleware` was exactly
        that, and it was invisible to any check that only read the top of the file."""
        found = cycles_in(graph(module_level_only=False))
        self.assertEqual([" -> ".join(c) for c in found], [])


class TheTiersPointOneWay(unittest.TestCase):
    """`harness.contrib` is shipped mechanism; `examples/` is judgment meant to be
    copied (ADR-082). Examples may build on contrib. Contrib may never reach back."""

    def test_contrib_imports_only_core(self):
        for module, path in modules().items():
            if not module.startswith("harness.contrib"):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                for n in names:
                    root = n.split(".")[0]
                    self.assertNotIn(
                        root, {"vision_tools", "vision_sensor", "coding_profile",
                               "shell_tools", "research_profile", "examples"},
                        f"{module} imports the examples tier")

    def test_every_tool_contrib_names_is_implemented_by_shipped_code(self):
        """The inversion that actually happened, and which no import analysis could see:
        `contrib.driver.PLAN_TOOLS` named `list_findings` as proof that an agent keeps a
        durable plan, while `FindingsLog` lived in `examples/`. Shipped code depending on
        a concept only copy-pasted code implements — by NAME, not by import, so the tier
        tests above were all green (ADR-101)."""
        import re

        from harness.contrib.driver import PLAN_TOOLS

        shipped = set()
        for path in CORE.rglob("*.py"):
            body = path.read_text()
            shipped |= set(re.findall(r"(?:async )?def (\w+)\(", body))
        missing = sorted(PLAN_TOOLS - shipped)
        self.assertEqual(missing, [],
                         f"contrib names {missing} but nothing in src/harness provides it")

    def test_an_example_may_import_contrib(self):
        used = [p.name for p in _paths.EXAMPLES.glob("*.py")
                if "harness.contrib" in p.read_text()]
        self.assertTrue(used, "if nothing uses contrib, contrib has no consumer")


if __name__ == "__main__":
    unittest.main()


#: L1 in each adapter package: the Protocol, not an implementation of it. Everything else
#: under `models/`, `memory/` and `observe/` is an L0 adapter.
_PROTOCOL_MODULES = {"harness.models.base", "harness.memory.base",
                     "harness.observe.events"}

#: Not adapters, whatever package they live in. `models/pricing.py` is a price TABLE —
#: shared vendor-neutral data every backend needs to compute a cost, and no
#: implementation of any seam. Filing it as an L0 adapter would make `run.py` a layering
#: violation for knowing what a token costs, which is not what the rule is protecting.
_NOT_ADAPTERS = {"harness.models.pricing"}
_ADAPTER_PACKAGES = ("harness.models.", "harness.memory.", "harness.observe.")

#: Modules allowed to name a concrete adapter, each with the reason it is allowed.
#: A composition root is SUPPOSED to know concrete types — that is its whole job, and
#: forbidding it would only move the wiring somewhere else behind a factory nobody asked
#: for (`docs/02 §8` rejects exactly that). What the rule protects is everything else.
_COMPOSITION_ROOTS = {
    "harness.agent": "the facade: it builds the exporters a run gets",
    "harness.server": "an entry point, by definition a composition root",
    "harness.credentials": "resolves a provider name to the provider that serves it",
    "harness.testing": "the testing kit's job is to hand out fakes",
    "harness.testing.chaos": "same",
    "harness.lg.runtime": "the durable engine's default for a seam it also EXPOSES "
                          "(`idempotency_store=`) — a default, not a hard-wiring",
    # Not a composition root, and recorded as the exception it is: `dispatch` hard-wires
    # the same store WITHOUT exposing the seam, so the classic engine cannot be given a
    # different one while the durable engine can. Same capability, two backends, one
    # switch. Held here rather than quietly excluded so it reads as a defect with a
    # reason, not as an approved shape.
    "harness.dispatch": "KNOWN GAP: hard-wires InMemoryStore for idempotency dedup with "
                        "no `idempotency_store=` to override it, unlike lg.runtime",
    "harness.tools.code": "KNOWN GAP: picks `Subprocess` directly rather than taking a "
                          "Sandbox; `sandbox.py` also declares the Protocol beside the "
                          "implementations, so the import alone cannot distinguish them",
    "harness.cli": "an entry point, by definition a composition root",
    "harness.contrib.driver": "KNOWN GAP: imports FakeModel for the 245-line demo it "
                              "ships, which `contrib/__init__.py`'s own tier rule says "
                              "belongs in `examples/` — closing that closes this",
}


class L2NeverNamesAnL0Adapter(unittest.TestCase):
    """`docs/02 §2` has said since Round 7 that this is *"enforced by an import-linter
    rule in CI (§09.6), not by discipline."* Measured: `grep -rn "import-linter"` hits
    three `.md` files and nothing else — no `.importlinter`, nothing in `pyproject.toml`,
    not in the dev dependencies. `docs/09 §…` lists the gate and `docs/14` carries it as
    AC-01. The property was real in most of the tree and unenforced in all of it.

    Deleting the claim was the other option and the smaller one. This is better: the
    property holds everywhere except the two places named above, and naming those two is
    worth more than a deleted sentence — a KNOWN GAP with a reason is a finding a reader
    can act on, where silence is not.
    """

    def test_only_a_composition_root_imports_a_concrete_adapter(self):
        offenders = {}
        for module, path in modules().items():
            if module in _COMPOSITION_ROOTS:
                continue
            # A module inside an adapter package may name its own siblings:
            # `models/anthropic.py` reading `models/pricing.py` is that package's
            # internals, not L2 reaching down into L0.
            #
            # The first version of this computed `module.rsplit(".", 1)[0] + "."`, which
            # for a TOP-LEVEL module like `harness.session` is `"harness."` — so it
            # exempted every `harness.*` import and the check was vacuous for every
            # module in core's root, which is most of core. Caught by mutation: adding
            # `from .memory.sqlite import SqliteStore` to `session.py` left this file
            # green. The exemption has to name the adapter package, not the importer's
            # parent.
            pkg = next((a for a in _ADAPTER_PACKAGES
                        if module == a.rstrip(".") or module.startswith(a)), None)
            hits = sorted(
                imp for imp in imports_of(path, module, module_level_only=False)
                if imp.startswith(_ADAPTER_PACKAGES)
                and imp not in _PROTOCOL_MODULES and imp not in _NOT_ADAPTERS
                and not (pkg is not None and imp.startswith(pkg)))
            if hits:
                offenders[module] = hits
        self.assertEqual(offenders, {},
                         "L2 must know the L1 protocols and never the L0 adapters. If one "
                         "of these is genuinely a composition root, add it to "
                         "_COMPOSITION_ROOTS with the reason.")

    def test_the_allowlist_does_not_outlive_its_entries(self):
        """An exception nobody has to justify any more is an exception nobody reads. Each
        entry must still be importing something, or it goes."""
        known = modules()
        stale = [m for m in _COMPOSITION_ROOTS if m not in known]
        self.assertEqual(stale, [], f"_COMPOSITION_ROOTS names modules that are gone: {stale}")

    def test_the_known_gaps_are_exactly_these(self):
        """So that closing one is visible, and adding a fourth is not free."""
        gaps = sorted(m for m, why in _COMPOSITION_ROOTS.items() if why.startswith("KNOWN GAP"))
        self.assertEqual(gaps, ["harness.contrib.driver", "harness.dispatch",
                                "harness.tools.code"])
