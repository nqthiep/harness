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

CORE = pathlib.Path("src/harness")

#: Layers, outermost first. A module may import its own layer and anything BELOW it,
#: never above. `cli` and `server` are entry points: they exist to be imported BY a user,
#: not by the library.
ENTRY_POINTS = ("harness.cli", "harness.server")


def modules() -> dict[str, pathlib.Path]:
    out = {}
    for f in CORE.rglob("*.py"):
        name = ".".join(f.relative_to(CORE.parent).with_suffix("").parts)
        out[name.removesuffix(".__init__")] = f
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

    def test_an_example_may_import_contrib(self):
        used = [p.name for p in pathlib.Path("examples").glob("*.py")
                if "harness.contrib" in p.read_text()]
        self.assertTrue(used, "if nothing uses contrib, contrib has no consumer")


if __name__ == "__main__":
    unittest.main()
