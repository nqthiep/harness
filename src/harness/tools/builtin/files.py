"""Local file tools.  read_file is `read`; write_file is `write` — reversible, but not
parallel-safe and never auto-retried (EFFECT_PROFILES)."""
from __future__ import annotations

from pathlib import Path

from .. import tool

MAX_BYTES = 200_000


@tool(effect="read")
def read_file(path: str) -> str:
    """Read what is inside a file."""
    return Path(path).read_text(encoding="utf-8", errors="replace")[:MAX_BYTES]


@tool(effect="write")
def write_file(path: str, text: str) -> str:
    """Write text into a file, replacing what was there."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"wrote {len(text)} characters to {p.name}"
