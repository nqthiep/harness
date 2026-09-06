"""`docs/02 §5` claims to list every module. It stopped doing that, and nothing noticed.

The section's own sentence was *"Nothing in the plan creates a file that is not on this
map."* Measured before this test existed:

    on the map, not on disk    7
    on disk, not on the map   47

Every safety-critical module added after roughly Round 28 was missing — `guards.py`,
`dispatch.py`, `credentials.py`, `policy/decision.py`, `sandbox.py`, `session.py`,
`stop.py`, and the whole of `lg/`, `server/`, `mcp/`, `eval/` and `contrib/`. Including,
pointedly, `guards.py`: the module ADR-098 is about. A map that omits the guards is worse
than no map, because a reader who consults it concludes they do not exist.

A document that describes the code drifts unless something compares the two. This is that
something.
"""
import ast
import unittest

import _paths


def listed_in_the_map() -> set[str]:
    """Parse §5's tree back into paths, by indentation.

    A line ending in `/` opens a directory at its indent and closes every deeper one; a
    line whose first token ends in `.py` is a module under whatever directories are still
    open above it. The root line is `src/harness/`, which is stripped so both sides of
    the comparison speak the same language.
    """
    doc = (_paths.DOCS / "02-architecture.md").read_text()
    block = doc.split("## 5. Module map", 1)[1].split("```", 2)[1]
    stack: dict[int, str] = {}
    out: set[str] = set()
    for line in block.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        token = line.strip().split()[0]
        if token.endswith("/"):
            stack = {k: v for k, v in stack.items() if k < indent}
            stack[indent] = token.rstrip("/")
        elif token.endswith(".py"):
            parts = [stack[k] for k in sorted(stack) if k < indent] + [token]
            rel = "/".join(parts)
            out.add(rel.removeprefix("src/harness/"))
    return out


def on_disk() -> set[str]:
    return {str(p.relative_to(_paths.CORE)) for p in _paths.CORE.rglob("*.py")}


class TheMapIsTheTree(unittest.TestCase):
    def test_the_parser_sees_a_plausible_number_of_modules(self):
        """The vacuity guard, learnt from `test_layering.py`: a parser that silently
        returns nothing makes every comparison below pass. Change the fence style or the
        heading and this fails instead of going quiet."""
        self.assertGreater(len(listed_in_the_map()), 20)

    def test_nothing_on_the_map_is_missing_from_the_tree(self):
        extra = sorted(listed_in_the_map() - on_disk())
        self.assertEqual(extra, [],
                         "docs/02 §5 lists modules that do not exist. Delete them, or "
                         "explain in the section why a planned file is listed as shipped.")

    def test_nothing_in_the_tree_is_missing_from_the_map(self):
        missing = sorted(on_disk() - listed_in_the_map())
        self.assertEqual(missing, [],
                         "these modules ship and docs/02 §5 does not mention them. A "
                         "reader consulting the map concludes they do not exist.")

    def test_each_line_says_what_the_module_says_about_itself(self):
        """The descriptions come from the modules' own docstrings, so they cannot drift
        either — but only if they are still recognisably that. Checked loosely: the
        map's text for a module must share its opening words with the docstring's, for
        every module that has one.
        """
        doc = (_paths.DOCS / "02-architecture.md").read_text()
        block = doc.split("## 5. Module map", 1)[1].split("```", 2)[1]
        described = {}
        stack: dict[int, str] = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            parts = line.strip().split(None, 1)
            if parts[0].endswith("/"):
                stack = {k: v for k, v in stack.items() if k < indent}
                stack[indent] = parts[0].rstrip("/")
            elif parts[0].endswith(".py") and len(parts) == 2:
                path = "/".join([stack[k] for k in sorted(stack) if k < indent] + [parts[0]])
                described[path.removeprefix("src/harness/")] = parts[1].strip()

        drifted = []
        for rel, text in described.items():
            f = _paths.CORE / rel
            doc_s = ast.get_docstring(ast.parse(f.read_text())) or ""
            if not doc_s:
                continue
            opener = " ".join(doc_s.strip().split())[:24].lower()
            if opener and not text.lower().startswith(opener[:16]):
                drifted.append((rel, text[:40], opener))
        self.assertEqual(drifted, [],
                         "a map entry no longer matches its module's docstring")


if __name__ == "__main__":
    unittest.main()
