"""`FindingsLog` moved to `harness.findings` — this file is the demo that stayed.

It was here while `harness.contrib.driver` listed `list_findings` among the tools that
prove an agent keeps a durable plan: shipped code depending on a concept only
copy-pasted code implemented (ADR-101). Its twin `TaskLedger` was already in core, so
core is where it went.

    from harness.findings import FindingsLog
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.findings import (  # noqa: E402,F401  (re-export for anything importing here)
    DEFAULT_KEY, MAX_CHARS_PER_FINDING, FindingsLog)
