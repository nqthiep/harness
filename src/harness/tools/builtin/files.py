"""Local file tools.  read_file is `read`; write_file is `write` — reversible, but not
parallel-safe and never auto-retried (EFFECT_PROFILES).

**Both are confined to the process's current working directory.** They used to take a
model-supplied path and hand it straight to `Path` — which made `read_file` an
exfiltration primitive one `tool_use` block wide (`"path": "../../.ssh/id_rsa"`), on the
tools the beginner path hands out precisely because a beginner has not yet thought about
any of this. `confine()` (T-7.1) had existed since M7 and these never called it; the
docs said so out loud, which is not the same as fixing it.

CWD is the root because it is the only root a module-level tool has: there is no
constructor here to pass one to. An agent that needs a DIFFERENT root builds its tools
from `harness.tools.code.CodeTools(root=...)`, which takes one — and gets navigation,
search and precise editing with it.
"""
from __future__ import annotations

from pathlib import Path

from ...workspace import confine
from .. import tool

MAX_BYTES = 200_000


@tool(effect="read")
def read_file(path: str) -> str:
    """Read what is inside a file."""
    p = confine(Path.cwd(), path)
    return p.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES]


@tool(effect="write")
def write_file(path: str, text: str) -> str:
    """Write text into a file, replacing what was there."""
    p = confine(Path.cwd(), path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"wrote {len(text)} characters to {p.name}"
