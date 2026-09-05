"""No test may name this repository's content with a path relative to the working
directory.

`tests/conftest.py` removed 136 `sys.path.insert` calls with the reason *"it breaks the
moment pytest is invoked from another directory."* It was right, and the same sentence
applied to every path those tests then read — which nobody noticed, because of how this
particular failure lands:

    $ cd /tmp && pytest /home/user/harness/tests/test_layering.py -q
    7 failed, 4 passed

The seven that failed did so loudly. The four that **passed** are the tier boundary, the
entry-point rule, and both import-cycle checks — everything that file exists to prove —
and they passed because `Path("src/harness").rglob("*.py")` on a directory that is not
there yields nothing and raises nothing. An empty graph has no cycles.

Three guards, because no one of them is enough:

* `tests/_paths.py` is the only place a repo path is built, it anchors on `__file__`, and
  it refuses to hand out a path that does not exist.
* `test_layering.py::modules()` refuses to return an implausibly small graph, so a future
  anchoring mistake fails instead of passing over nothing. That one is behavioural.
* This file, which is the textual net around both — and it reads the AST rather than the
  line, because the first version of it matched `"tests"` as a substring and flagged
  `str(_paths.repo("tests"))`, the fix, alongside the defect. A test that cannot tell a
  repair from the thing it repairs is worse than no test.
"""
import ast
import pathlib
import re
import unittest

import _paths

#: Names that mean "this repository's own content" at the front of a relative literal.
_REPO_ROOTS = ("src", "docs", "examples", "tests", "design")
_REPO_FILES = ("pyproject.toml", "README.md", "HARNESS.md")

#: Empty, and it did its job on the way here. It held exactly one entry —
#: `tests/test_parity.py`, which another change had checked out at the time — as an EXACT
#: match rather than a floor, so the moment that change landed and its paths were anchored
#: this test failed until the entry was deleted. A debt with a name gets paid; a category
#: that quietly stops reporting is how the original defect survived a round of cleanup.
_PENDING_HANDOFF: frozenset[str] = frozenset()
_PENDING_HANDOFF_SCRIPTS: tuple[str, ...] = ()

#: `sys.path.insert(0, "src")` written INSIDE a string that a child interpreter will run.
#: The AST of this file sees a string constant there, not a call, so it needs its own
#: matcher — and that back door is exactly how the removed inserts came back.
_CHILD_INSERT = re.compile(
    r"""sys\.path\.insert\(\s*0\s*,\s*['"](?:src|tests|examples)['"]""")


def _first_arg_literals(tree: ast.AST):
    """String constants used as the FIRST argument of a path-building call.

    Only the first: `Path(self.root, "README.md")` builds a path under a tmpdir the test
    just made, where the literal is a leaf name and not a root. Flagging that would train
    the reader to ignore this test.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.attr if isinstance(fn, ast.Attribute) else
                fn.id if isinstance(fn, ast.Name) else "")
        if name not in ("Path", "open", "read_text", "glob", "rglob"):
            continue
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            yield arg.value, node.lineno


def _is_repo_relative(value: str) -> bool:
    if value.startswith(("/", "~", "*")):
        return False
    return value.split("/")[0] in _REPO_ROOTS or value in _REPO_FILES


def _unanchored_repo_paths(tree: ast.AST):
    """Repo-path literals that are NOT being joined to an anchor.

    `_first_arg_literals` sees `Path("src/harness/agent.py")` and misses
    `for path in ("src/harness/agent.py", ...): Path(path)` — the call's first argument
    there is a NAME, not a constant, and that is how two more of these survived the first
    version of this file. Widening to "any repo-shaped literal" is the obvious next move
    and it is wrong: `_ROOT / "src/harness/run.py"` is the FIX, and
    `examples/coding_profile.py` names `src/app.py` in a demo about a user's own project.

    Two discriminators, both behavioural rather than textual:

    * the literal must name something that actually EXISTS in this repository, which
      `src/app.py` does not;
    * it must not be an operand of a `/` join or an argument to `os.path.join`, which is
      what anchoring looks like once it has been done.
    """
    anchored = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            anchored |= {id(n) for n in (node.left, node.right)
                         if isinstance(n, ast.Constant)}
        elif isinstance(node, ast.Call):
            fn = node.func
            if (fn.attr if isinstance(fn, ast.Attribute) else
                    getattr(fn, "id", "")) in ("join", "repo", "Path"):
                anchored |= {id(a) for a in node.args[1:] if isinstance(a, ast.Constant)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in anchored and "/" in node.value
                and _is_repo_relative(node.value)
                # A FILE, not a directory name: `assertIn("src/harness", files)` compares
                # a string against pyproject's mypy list and opens nothing, and a rule
                # that cannot tell that apart is a rule people learn to ignore. A bare
                # `Path("src/harness")` is still caught by `_first_arg_literals`.
                and pathlib.PurePosixPath(node.value).suffix
                and (_paths.ROOT / node.value).exists()):
            yield node.value, node.lineno


def _py_files(*dirs):
    for d in dirs:
        for f in sorted(_paths.repo(d).glob("*.py")):
            if f.name != pathlib.Path(__file__).name:
                yield d, f


class EveryRepoPathIsAnchored(unittest.TestCase):
    def test_no_test_names_repo_content_relative_to_the_cwd(self):
        offenders = {}
        for _, f in _py_files("tests"):
            if f.name in ("_paths.py", "conftest.py"):
                continue                      # these ARE the anchor
            tree = ast.parse(f.read_text())
            hits = sorted({(v, n) for v, n in _first_arg_literals(tree)
                           if _is_repo_relative(v)}
                          | set(_unanchored_repo_paths(tree)))
            if hits:
                offenders[f.name] = hits
        self.assertEqual(
            set(offenders), set(_PENDING_HANDOFF),
            f"repo paths built relative to the working directory: {offenders}\n"
            f"Use `_paths.repo(...)` / `_paths.SRC` / `_paths.DOCS` — they anchor on "
            f"__file__ and refuse a path that does not exist. If an entry here was just "
            f"fixed, delete it from _PENDING_HANDOFF.")

    def test_no_bare_relative_sys_path_insert_survives_anywhere(self):
        """Two spellings of the same defect, both from the AST.

            tests/test_m3.py       "import sys; sys.path.insert(0, 'src')"
            tests/test_m8...py     "import sys; sys.path.insert(0, 'src'); import harness"
            examples/proof.py      "import sys;sys.path.insert(0,'src');import harness"

        All three passed from the repository root and produced `ModuleNotFoundError:
        No module named 'harness'` from anywhere else. `examples/` is included because a
        recipe is meant to be copied: a reader who pastes one into their own project
        inherits whichever spelling it shipped with, and four of these files already used
        the anchored one.
        """
        bad = []
        for d, f in _py_files("tests", "examples"):
            for node in ast.walk(ast.parse(f.read_text())):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "insert"
                        and ast.unparse(node.func).endswith("sys.path.insert")):
                    for arg in node.args[1:]:
                        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                                and _is_repo_relative(arg.value)):
                            bad.append(f"{d}/{f.name}:{node.lineno}: "
                                       f"sys.path.insert(..., {arg.value!r})")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if _CHILD_INSERT.search(node.value):
                        bad.append(f"{d}/{f.name}:{node.lineno}: "
                                   f"a child script is handed a relative path")
        bad = sorted(b for b in bad if not b.startswith(_PENDING_HANDOFF_SCRIPTS))
        self.assertEqual(bad, [], "\n".join(bad))


class TheAnchorRefusesToPointAtNothing(unittest.TestCase):
    def test_a_path_that_does_not_exist_raises_rather_than_returning(self):
        with self.assertRaises(FileNotFoundError):
            _paths.repo("src", "harness", "a-module-that-was-deleted.py")

    def test_the_root_is_this_repository_and_not_the_cwd(self):
        self.assertTrue((_paths.ROOT / "pyproject.toml").exists())
        self.assertEqual(_paths.CORE, _paths.ROOT / "src" / "harness")


if __name__ == "__main__":
    unittest.main()
