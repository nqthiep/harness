"""The only place a test constructs a path to this repository's own content.

`tests/conftest.py` already says why 136 hand-rolled `sys.path.insert` calls were a
defect rather than a style preference: *"it breaks the moment pytest is invoked from
another directory."* The same sentence was true of the paths those tests then read, and
nobody noticed, because of how the failure lands:

    $ cd /tmp && pytest /home/user/harness/tests/test_layering.py -q
    7 failed, 4 passed

The seven that failed did so loudly — a missing file raises. The four that **passed** are
the four that matter: the tier boundary, the entry-point rule, and both import-cycle
checks. `Path("src/harness").rglob("*.py")` on a directory that is not there yields
nothing and raises nothing, so the module graph came back empty and every question asked
about it was answered "no cycles, no violations" over no modules at all. And
`tests/test_m5.py` read a doc at IMPORT time, so from another directory the whole suite
failed to collect before reaching any of it.

Anchoring on `__file__` fixes where the paths point. It does not fix the shape of the
bug, which is that an empty measurement is indistinguishable from a clean one — that is
what `test_layering.py`'s own emptiness guard is for, and why this module refuses to hand
out a path that does not exist.
"""
import pathlib

#: This file is `<repo>/tests/_paths.py`, so the repository root is two parents up.
#: Resolved, so a symlinked checkout still compares equal to what `rglob` yields.
ROOT = pathlib.Path(__file__).resolve().parent.parent


def repo(*parts: str) -> pathlib.Path:
    """A path inside this repository, and it must already exist.

    The existence check is the point. Every path handed out here names content that is
    committed — `src/harness`, a document, `pyproject.toml` — so a missing one is a
    broken anchor or a moved file, never a legitimate absence. Returning it silently is
    how `rglob` came to measure nothing; raising here turns that into a stack trace with
    the offending path in it.

    Not for paths a test CREATES (a tmpdir, a scaffolded `.env`, a `.harness/`
    checkpoint): those are deliberately relative to a `chdir`-ed working directory and
    have nothing to do with this repository's layout.
    """
    p = ROOT.joinpath(*parts)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} does not exist. This helper only names content committed to the "
            f"repository, so a miss here is a moved file or a broken anchor — not "
            f"something to skip past. (repo root resolved to {ROOT})")
    return p


SRC = repo("src")
CORE = repo("src", "harness")
DOCS = repo("docs")
EXAMPLES = repo("examples")
