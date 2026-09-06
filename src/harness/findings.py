"""`FindingsLog` — the other half of what a long exploratory session needs to survive
compaction. `TaskLedger` (`src/harness/tasks.py`) already solves "what's done and what's
left"; it does not solve "what did I LEARN" — the real cause of a stack trace, a file
that turned out to be a red herring, a build quirk ("this repo needs `NODE_ENV=test` or
the linter OOMs") — the kind of thing `context/window.py` drops WHOLESALE at 80% of the
window with no summarization (ADR-023's own refusal of a second model call for
reflection, and the taint-laundering risk a summary would reopen — S-19), and
`Task.note` has no room for (`tasks.py:60-64`: "một dòng cho model đọc — ngắn").

Same shape, same reasoning, same zero-extra-token cost as `TaskLedger` — this is
DURABLE STATE the model writes to on its own initiative, not a planner or a second model
call. `Store`-backed (the same seam `TaskLedger`/`VikingStore` already use), so it
survives a process restart the same way the task list does, and reading it back is one
cheap `list_findings()` call after a compaction instead of the agent re-discovering the
same dead end twice — the specific, expensive failure mode a long exploratory session
runs into without this.

    findings = FindingsLog(SqliteStore("session.db"))
    lead = Agent(name="Coder", job="...", tools=[*code.tools(), *findings.tools()])

Unbounded, on purpose, for now — same trade `tasks.py` names for itself: "a session has
tens of findings, not millions; when someone needs a different scale, this is the line
to change" (`tasks.py:82-84`'s own reasoning, restated here because it applies again).

**Why this is core and not `examples/`.** It was in `examples/` while
`harness.contrib.driver` listed `list_findings` in `PLAN_TOOLS` — shipped code depending
on a concept only copy-pasted code implemented, which is the inversion the tier rule
exists to prevent. It passes every contrib admission criterion, and then one more: its
twin `TaskLedger` is already here, and splitting two modules that are the same shape,
same reasoning and same seam across two tiers would be arbitrary (ADR-101).
"""
from __future__ import annotations

import json
import time
from typing import Final


from .errors import HarnessError
from .memory.base import Store, read_modify_write
from .tools import tool

DEFAULT_KEY: Final = "harness:findings"
#: Read grade in the printed log — `line_oriented` readability is a house style this
#: module borrows, not a hard requirement; kept short because `summary()` is reprinted
#: on every `list_findings()` call, same reasoning as `Task.line()`.
MAX_CHARS_PER_FINDING: Final = 400


class FindingsLog:
    def __init__(self, store: Store, *, key: str = DEFAULT_KEY) -> None:
        self._store = store
        self._key = key

    async def all(self) -> tuple[str, ...]:
        raw = await self._store.get(self._key)
        if not raw:
            return ()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            # Same fail-visible discipline as TaskLedger.all() (IDL-30): a corrupt log
            # is reported, never silently replaced with an empty one that would erase
            # every dead end already ruled out.
            raise HarnessError(
                f"findings log at key {self._key!r} does not parse (corrupt JSON) — "
                f"not silently reset, since that would erase every finding recorded "
                f"so far."
            ) from None
        return tuple(f"[{r['at']}] {r['text']}" for r in rows)

    async def add(self, text: str) -> None:
        def mutate(raw: str | None) -> str:
            rows = json.loads(raw) if raw else []
            rows.append({"at": time.strftime("%H:%M:%S", time.localtime()),
                         "text": text[:MAX_CHARS_PER_FINDING]})
            return json.dumps(rows, ensure_ascii=False)

        # Through `read_modify_write` like its twin, so a second writer is an error
        # rather than a finding that quietly vanishes (ADR-100). Moving this module is
        # what surfaced it: in `examples/` it was a bare get/put pair, and the rule
        # ADR-100 established applies to every ledger on a `Store`, not to the two that
        # happened to be in core when it was written.
        await read_modify_write(self._store, self._key, mutate,
                                what="this session's findings log")

    async def summary(self) -> str:
        rows = await self.all()
        return "\n".join(rows) if rows else "(no findings recorded yet)"

    def tools(self) -> list:
        log = self

        @tool(effect="write")
        async def add_finding(text: str) -> str:
            """Record something you learned that is worth remembering later in this
            session — a root cause, a dead end you already ruled out, a quirk of this
            project's build. This survives context compaction; your own memory of it
            does not. One or two sentences — this is a note to your future self, not a
            report."""
            await log.add(text)
            return "recorded"

        @tool(effect="read")
        async def list_findings() -> str:
            """Read back everything recorded with `add_finding` so far this session —
            call this after noticing you've lost track, or right after a compaction."""
            return await log.summary()

        return [add_finding, list_findings]
