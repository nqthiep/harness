"""Human-readable progress — ADR-014.

Attached only when stdout is a TTY, and writes to stderr (IDL-24) so redirecting stdout
stays clean.  Tool NAMES only, never arguments (register #44).
"""
from __future__ import annotations

import sys
from typing import TextIO

from .events import Event, EventKind

_VERB = {
    "search": "searching the web", "fetch": "reading a page",
    "read_file": "reading a file", "write_file": "writing a file",
    "run_command": "running a command",
}


class ConsoleExporter:
    def __init__(self, agent_name: str, *, stream: TextIO | None = None) -> None:
        self._name = agent_name
        self._out = stream if stream is not None else sys.stderr

    @staticmethod
    def should_attach(stream: TextIO | None = None) -> bool:
        s = stream if stream is not None else sys.stdout
        try:
            return bool(s.isatty())
        except Exception:
            return False

    def emit(self, event: Event) -> None:
        if event.kind is EventKind.MODEL_REQUEST:
            self._say("is thinking...")
        elif event.kind is EventKind.TOOL_STARTED:
            tool = event.data.get("tool", "a tool")
            self._say(f"is {_VERB.get(tool, f'using {tool}')}...")
        elif event.kind is EventKind.RUN_FINISHED:
            cost, steps = event.data.get("cost_usd", "?"), event.data.get("steps", "?")
            self._say(f"is done.  ({cost}, {steps} steps)")

    def _say(self, what: str) -> None:
        try:
            print(f"{self._name} {what}", file=self._out, flush=True)
        except Exception:
            pass

    def close(self) -> None: ...
