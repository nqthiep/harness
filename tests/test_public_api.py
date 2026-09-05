"""The CI test poka-yoke register #8 says exists.

`docs/08-poka-yoke.md` row 8: *"Importing from a private path that later moves — `__all__`
is the contract; a CI test fails if any example or doc imports outside it. Rank: CI."*
Before this file, `grep -rn "__all__" tests/*.py` returned nothing: the register described
a test that did not exist, and the library's own examples broke the stated rule 19 times
out of 20 files.  Measured at that point:

    len(harness.__all__) == 48;  Store, Memo, Exporter, Event, EventKind, Sandbox and
    Completed absent — three of the six seams `docs/02-architecture.md §4` declares had
    no top-level export at all
    19 of 20 examples/*.py imported at least one harness name outside `__all__`
    docs/*.py code blocks: 3 distinct submodules, 0 top-level names outside `__all__`

Two of those three numbers were fixed rather than written down.  The seams are exported
now (`harness/__init__.py`).  The 19 examples are not a bug to be silenced: the library
deliberately routes whole tiers through their own module — `docs/03-public-api.md §6`
says so in as many words ("Everything else is reached through its own submodule — `from
harness.lg import build_agent` … `from harness.testing import FakeModel`") — so the
register's literal wording ("imports outside it") is stricter than the design it is meant
to defend.

**So the claim is narrowed here, to the rule the library actually keeps**, and every
place it is looser than the register's wording is an explicit line with a reason:

  1. Every seam in `docs/02-architecture.md §4` is exported, and is the same object as
     the one in its defining module.  No allowlist, no exceptions (`test_every_seam...`).
  2. Every name in `__all__` resolves, exactly once.  No exceptions.
  3. An example or doc may import a `harness` name outside `__all__` only through a
     module in `SUBMODULE_TIERS` below — one entry per module, each with the reason the
     top level does not carry it.  Anything else fails.
  4. A name that IS in `__all__` must be imported from `harness`, not from the module
     that defines it — that is exactly the failure register #8 names, and it is the one
     the examples really do commit.  `PUBLIC_NAME_BY_PRIVATE_PATH` pins the surviving
     cases exactly: a new one fails the test, and *fixing* one also fails it until the
     line is deleted, so the list can only shrink.

Rules 3 and 4 are separate on purpose.  A tier is a design decision that stays; a
public name reached by its private path is a stale import with a one-line fix, and
lumping the two together would let the second hide inside the first forever.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

import harness

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_EXAMPLES = _ROOT / "examples"
_DOCS = _ROOT / "docs"

# --- 1. the seams -----------------------------------------------------------------
#: `docs/02-architecture.md §4`'s table, and for each seam the names an implementer must
#: be able to say.  Value: the module that DEFINES it — the export must be that object,
#: so a re-export that drifts to a look-alike is caught.
SEAMS: dict[str, str] = {
    # Tool
    "tool": "harness.tools", "ToolSpec": "harness.tools", "Effect": "harness.tools",
    # ModelProvider
    "ModelProvider": "harness.models.base",
    # Store — you cannot write one without Memo
    "Store": "harness.memory.base", "Memo": "harness.memory.base",
    # Policy — you cannot write one without the verdict types
    "Policy": "harness.policy.base", "Ruling": "harness.policy.base",
    "ToolCall": "harness.policy.base", "Verdict": "harness.policy.base",
    # Exporter — you cannot write one without the event it is handed
    "Exporter": "harness.observe.events", "Event": "harness.observe.events",
    "EventKind": "harness.observe.events",
    # Sandbox — you cannot write one without its return type
    "Sandbox": "harness.sandbox", "Completed": "harness.sandbox",
}

# --- 3. the tiers -------------------------------------------------------------------
#: Modules an example or doc may import from directly, and why the top level does not
#: carry them.  Not a blanket exemption: a module not on this list fails the test, and
#: three of these are load-bearing (`lg`, `models.anthropic`, `memory.viking` would each
#: make `import harness` pull a dependency it must not pull).
SUBMODULE_TIERS: dict[str, str] = {
    # -- optional extras.  A top-level re-export would import the extra eagerly, which
    #    `pyproject.toml` forbids in as many words ("`import harness` must never import
    #    LangChain", ADR-032).  `test_import_harness_is_cheap` guards that.
    "harness.lg": "extra `graph` — re-exporting imports LangGraph at `import harness`",
    "harness.memory.viking": "extra `viking` — re-exporting imports the OpenViking SDK",
    "harness.models.anthropic": "re-exporting imports the `anthropic` SDK eagerly",
    # -- contrib.  `contrib/__init__.py` states the rule itself: "Never `from harness
    #    import Driver`. The longer path is the disclaimer."
    "harness.contrib.driver": "contrib tier — no compatibility promise, by design",
    "harness.contrib.calibration": "contrib tier — no compatibility promise, by design",
    "harness.contrib.output_shaping": "contrib tier — no compatibility promise, by design",
    # -- tiers `docs/03-public-api.md §6` names as submodule-reached.
    "harness.eval": "doc 03 §6 — the eval tier is reached by its own submodule",
    "harness.eval.cost": "doc 03 §6 — the eval tier is reached by its own submodule",
    "harness.testing": "doc 03 §6 — test doubles are reached by their own submodule",
    "harness.models.fake": "test double behind `harness.testing`",
    # -- implementations of a seam.  The seam is top-level (rule 1); the concrete classes
    #    are not, so that adding a fourth store does not widen the public surface.
    "harness.memory": "Store implementations — the seam itself is top-level",
    "harness.memory.base": "Store implementers' helpers (`read_modify_write`)",
    "harness.memory.inmemory": "Store implementation — the seam itself is top-level",
    "harness.memory.sqlite": "Store implementation — the seam itself is top-level",
    "harness.sandbox": "Sandbox implementations (`InProcess`, `Subprocess`)",
    "harness.models.base": "provider-author types (`ModelRequest`, `ModelResponse`, `DeltaFn`)",
    "harness.models.pricing": "the price table — data, versioned with the providers",
    "harness.policy.builtin": "shipped Policy implementations",
    "harness.observe.transcript": "reader for the transcript file, not a run-time type",
    # -- mechanism a caller reaches for deliberately, kept off the top level so the
    #    48-line contract stays readable.
    "harness.budget.ledger": "`Ledger` is core mechanism; `Budget` is top-level",
    "harness.errors": "provider-error subclasses; every error a caller catches is top-level",
    "harness.findings": "FindingsLog — a tool bundle, constructed by name",
    "harness.tasks": "TaskLedger — a tool bundle, constructed by name",
    "harness.tools.code": "tool bundle — a class of @tool functions, built by name",
    "harness.tools.calc": "tool bundle (a two-line re-export of tools.builtin.calc)",
    "harness.result": "`Money`/`Usage`/`StopReason` are top-level; this is their home",
    "harness.policy.base": "`Policy`/`ToolCall`/`Verdict`/`Ruling` are top-level; their home",
    "harness.tools.web": "tool bundle (doc 03 §6 names this path)",
    "harness.tools.registry": "`ToolSet` — used to MEASURE the loop, not to build one",
    "harness.workspace": "`confine` and its error — the filesystem boundary helper",
    "harness.secrets": "`redact` — the redactor itself; `Secret` is top-level",
    "harness.tools": "`EFFECT_PROFILES` — the effect table, read by proofs",
}

# --- 4. the ratchet -----------------------------------------------------------------
#: (example file, module, name) for every import of an ALREADY-PUBLIC name through the
#: module that defines it.  This is register #8's literal failure mode, and it is the
#: only one the examples really commit.  Each has a one-line fix: delete the submodule
#: import and take the name from `harness`.  Asserted with `==`, so this set can only
#: shrink — fixing an example turns the test red until its line here is deleted.
PUBLIC_NAME_BY_PRIVATE_PATH: frozenset[tuple[str, str, str]] = frozenset({
    ("coding_agent.py", "harness.errors", "UnsafeToolSetError"),
    ("coding_agent.py", "harness.result", "StopReason"),
    ("deepseek_provider.py", "harness.errors", "ProviderError"),
    ("deepseek_provider.py", "harness.result", "Usage"),
    ("full_agent.py", "harness.secrets", "Secret"),
    ("proof.py", "harness.budget.ledger", "Budget"),
    ("proof.py", "harness.errors", "UnsafeToolSetError"),
    ("proof.py", "harness.memory.base", "Memo"),
    ("proof.py", "harness.result", "Money"),
    ("proof.py", "harness.result", "Usage"),
    ("proof.py", "harness.secrets", "Secret"),
    ("shell_tools.py", "harness.policy.base", "ToolCall"),
    ("viking_memory.py", "harness.errors", "UnsafeToolSetError"),
    ("vision_profile.py", "harness.errors", "ConfigError"),
})


def _harness_imports(tree: ast.AST):
    """Yield (module, name) for every `from harness… import name` in a parsed module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "harness" or node.module.startswith("harness."):
                for alias in node.names:
                    yield node.module, alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("harness."):
                    yield alias.name, ""


# ---------------------------------------------------------------------------------


def test_every_declared_seam_is_exported() -> None:
    """Rule 1. Three of six seams had no export; implementing one started with an error."""
    missing = [n for n in SEAMS if n not in harness.__all__]
    assert not missing, (
        f"{missing} declared a plugin seam by docs/02-architecture.md §4 and absent from "
        f"harness.__all__ — `from harness import {missing[0]}` is an ImportError, which is "
        f"the first thing an implementer types")


def test_exported_seam_is_the_defining_modules_object() -> None:
    """Rule 1, the half a name-only check misses: the export must not drift to a copy."""
    for name, module in SEAMS.items():
        defined = getattr(importlib.import_module(module), name)
        assert getattr(harness, name) is defined, f"harness.{name} is not {module}.{name}"


def test_all_names_resolve_and_are_unique() -> None:
    """Rule 2."""
    assert len(harness.__all__) == len(set(harness.__all__)), "duplicate entry in __all__"
    unresolved = [n for n in harness.__all__ if not hasattr(harness, n)]
    assert not unresolved, f"__all__ names nothing importable: {unresolved}"


def test_import_harness_is_cheap() -> None:
    """The reason three tiers in SUBMODULE_TIERS may never be re-exported."""
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, harness; print([m for m in ('langgraph','langchain_core',"
         "'anthropic','openviking') if m in sys.modules])"],
        capture_output=True, text=True, cwd=str(_ROOT),
        env={"PYTHONPATH": str(_ROOT / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"`import harness` pulled {out.stdout.strip()}"


def test_examples_import_only_public_paths(subtests) -> None:
    """Rule 3, over `examples/` — the half of register #8 that was 19-for-20 broken."""
    public = set(harness.__all__)
    for path in sorted(_EXAMPLES.glob("*.py")):
        with subtests.test(example=path.name):
            for module, name in _harness_imports(ast.parse(path.read_text())):
                if module == "harness":
                    assert name in public, (
                        f"{path.name}: `from harness import {name}` — not in __all__")
                    continue
                assert module in SUBMODULE_TIERS, (
                    f"{path.name}: `from {module} import {name}` — {module} is not a "
                    f"public tier. Export the name from harness, or add {module} to "
                    f"SUBMODULE_TIERS with the reason the top level does not carry it.")


def test_docs_import_only_public_paths(subtests) -> None:
    """Rule 3, over `docs/` — the other half the register names."""
    public = set(harness.__all__)
    pattern = re.compile(r"^\s*from (harness[\w.]*) import (.+)$", re.M)
    for path in sorted(_DOCS.glob("*.md")):
        with subtests.test(doc=path.name):
            for block in re.findall(r"```(?:python|py)\n(.*?)```", path.read_text(), re.S):
                for module, raw in pattern.findall(block):
                    names = [n.strip().split(" as ")[0]
                             for n in raw.rstrip(" \\").split(",") if n.strip()]
                    if module == "harness":
                        outside = [n for n in names if n not in public and n != "*"]
                        assert not outside, f"{path.name}: `from harness import {outside}`"
                    else:
                        assert module in SUBMODULE_TIERS, (
                            f"{path.name}: `from {module} import {raw}` is not a public tier")


def test_public_names_are_not_reached_by_their_private_path() -> None:
    """Rule 4, the ratchet.  Equality, not containment: the list may only shrink."""
    public = set(harness.__all__)
    found = set()
    for path in sorted(_EXAMPLES.glob("*.py")):
        for module, name in _harness_imports(ast.parse(path.read_text())):
            if module == "harness" or name not in public:
                continue
            # Same NAME is not enough: `harness.contrib.driver.Event` is a sensor event
            # and `harness.Event` is an observability event — two unrelated classes that
            # happen to collide.  Only the same OBJECT is a stale path.
            if getattr(importlib.import_module(module), name, None) is getattr(harness, name):
                found.add((path.name, module, name))
    new = sorted(found - PUBLIC_NAME_BY_PRIVATE_PATH)
    stale = sorted(PUBLIC_NAME_BY_PRIVATE_PATH - found)
    assert not new, (
        f"{new} import a name that IS in harness.__all__ through the module that defines "
        f"it — register #8's exact failure mode. Use `from harness import ...`")
    assert not stale, (
        f"{stale} are fixed — delete them from PUBLIC_NAME_BY_PRIVATE_PATH so the list "
        f"keeps meaning what it says")


def test_the_allowlists_carry_no_dead_entries() -> None:
    """An allowlist nobody prunes becomes the blanket exemption it was written to avoid."""
    used = set()
    for path in list(_EXAMPLES.glob("*.py")):
        used.update(m for m, _ in _harness_imports(ast.parse(path.read_text())))
    for path in _DOCS.glob("*.md"):
        for block in re.findall(r"```(?:python|py)\n(.*?)```", path.read_text(), re.S):
            used.update(re.findall(r"^\s*from (harness[\w.]*) import", block, re.M))
    dead = sorted(set(SUBMODULE_TIERS) - used)
    assert not dead, f"SUBMODULE_TIERS entries nothing imports any more: {dead}"


@pytest.mark.parametrize("module", sorted(SUBMODULE_TIERS))
def test_every_allowlisted_tier_carries_a_reason(module: str) -> None:
    reason = SUBMODULE_TIERS[module]
    assert len(reason) > 20, f"{module}: a reason, not a label"
