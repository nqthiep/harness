"""T-7.1 — workspace root confinement, docs/17-research-alignment.md M7.

"Tool đụng file chỉ thấy được dưới một `workspace=` đã khai... từ chối, không escape"
— same idiom IDL-44 already paid for once (`memory/viking.py::check_key`, keys becoming
a `viking://` path): resolve fully, then check containment. Nothing here tries to be
clever by stripping `..` or decoding percent-escapes and re-checking that — that is
exactly the class of bug "escaping" is, versus "refusing."
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import HarnessError

#: Windows drive letter (`C:\`) or UNC (`\\server\share`) prefixes — `os.path.isabs`
#: only recognizes POSIX-absolute paths on a POSIX system, so a Windows-shaped absolute
#: path would sail through that check alone on Linux/macOS (the two platforms this
#: library is actually built and tested on, IDL-16/NFR-01).
_WINDOWS_ABS = re.compile(r"^([A-Za-z]:[\\/]|\\\\)")

#: G-16, design/review-architect.md — a blanket `"%" in path` rejection (this module's
#: first version) refused every legitimate filename that happens to contain a literal
#: `%` (`"50% off.txt"`, `"report%.csv"` — a perfectly ordinary POSIX filename
#: character, no different from `#` or `&`), not just an attack. Narrowed to the actual
#: threat T-7.1's own test list names: a percent-encoded sequence that would DECODE to
#: something path-traversal-relevant — `.` (`%2e`), `/` (`%2f`), `\` (`%5c`), or a null
#: byte (`%00`, redundant with the raw null-byte check above but kept explicit) — case-
#: insensitively, since a real URL-decoder would accept either case. `"100%25.txt"`
#: (which would decode to a literal `%`) is not on this list and passes: decoding it
#: introduces no path-relevant character, so there is nothing here to refuse.
_SUSPICIOUS_PERCENT = re.compile(r"%(?:2e|2f|5c|00)", re.IGNORECASE)


class WorkspaceEscapeError(HarnessError):
    """Not a `ConfigError` — AC-06 forbids raising one from inside `RunEngine`, and a
    path escape is discovered on a (frequently model-supplied) argument during a tool
    call, not at construction time. A tool that raises this gets the same treatment as
    any other tool exception (`dispatch.py`'s existing catch-all): an `is_error` tool
    result the model sees and can react to, not a crash."""


def confine(root: str | Path, path: str) -> Path:
    """Resolve `path` against `root`; return the absolute, resolved `Path`, or raise
    `WorkspaceEscapeError` if it would land outside `root`.

    Every rejection below ends at the same single containment check
    (`Path.relative_to`) — the up-front checks (absolute paths, percent-encoding, null
    bytes) exist to refuse the input outright rather than let it reach a join/resolve
    that has its own footguns (`Path("/root") / "/etc/passwd" == Path("/etc/passwd")` —
    pathlib's `/` operator silently discards the left side when the right side is
    absolute; this function's own first version fell into exactly that before its own
    test suite caught it).
    """
    if not isinstance(path, str) or not path:
        raise WorkspaceEscapeError(f"{path!r} is not a usable workspace path")
    if "\x00" in path:
        raise WorkspaceEscapeError("workspace path contains a null byte")
    if _SUSPICIOUS_PERCENT.search(path):
        # G-16: narrowed from a blanket `"%" in path` (see `_SUSPICIOUS_PERCENT`'s own
        # comment) — a bare `%` is an ordinary filename character and no longer refused
        # on its own. What IS still refused: a percent-encoded `.`/`/`/`\`/NUL, the one
        # shape this check exists for (an encoded ".." trying to survive a check
        # written for the decoded form — T-7.1's own test list names this case).
        # Reject outright rather than decode-then-check — decoding invites exactly the
        # "did I decode enough times" bug class this whole function exists to avoid.
        raise WorkspaceEscapeError(
            f"{path!r} contains a percent-encoded path separator or dot, which a "
            "workspace path may never use — write the literal character instead")
    if os.path.isabs(path) or _WINDOWS_ABS.match(path):
        raise WorkspaceEscapeError(
            f"{path!r} is an absolute path; workspace paths must be relative to the "
            "workspace root")
    root_r = Path(root).resolve()
    # `strict=False` (the default): symlinks that exist along the path ARE still
    # resolved and `..` segments ARE still collapsed — only a non-existent FINAL
    # component is tolerated, which a tool creating a new file needs.
    candidate = (root_r / path).resolve()
    try:
        candidate.relative_to(root_r)
    except ValueError:
        raise WorkspaceEscapeError(
            f"{path!r} resolves to {candidate}, outside the workspace root {root_r}"
        ) from None
    return candidate
