"""One place that makes `src/` and `examples/` importable, instead of 100 files each
doing it themselves.

Measured before this file existed: 136 `sys.path.insert` calls across 100 files. That is
not a style preference — it is a defect. It breaks the moment pytest is invoked from
another directory, it hides the real dependency graph (a reader cannot tell what a test
actually imports from where), and it is the reason `examples/` grew into a second
library that nothing could import honestly (ADR-082).

`src` is here because this repository is not installed (src layout, no editable install).
`examples/` is here because the recipe layer is deliberately a directory of flat,
copy-pasteable modules rather than a package — `from vision_tools import ...` is what a
user's own copy will say, so that is what the tests say too. The genuinely reusable
mechanism that used to live there now ships as `harness.contrib` and is imported by its
real path.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _path in (_ROOT / "src", _ROOT / "examples", _ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
