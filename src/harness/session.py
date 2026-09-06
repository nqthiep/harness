"""T-8.6 — `Session` as a first-class resource, docs/17-research-alignment.md M8.

"Vòng 37 đã sửa phần rò rỉ; đây là phần đặt tên cho thứ đã tồn tại ngầm": the state
isolation Round 37 established — one `Ledger`/`TaintTracker`/`EventBus`/`DecisionLog`
per run or thread, never shared (S-15/S-24/S-29 in this project's own history) — already
makes each conversation isolated. What was missing is a NAME and a handful of
resource-lifecycle properties for what already behaves like a session: an id, an owner,
a TTL, a way to fork a branch, and a concurrency boundary so two callers cannot mutate
the same conversation's history at once.

Wraps `Chat` (ADR-020, the classic backend's existing multi-turn object) rather than
reinventing multi-turn state. **Scoped to the classic backend on purpose.** On the
LangGraph backend, `thread_id` (checkpointer-durable) is already the session primitive
— `session_id` is set to it (T-8.1, ADR-048) — and a symmetric `Session` there would
need checkpointer-level metadata storage (ownership, TTL) this library does not build;
that is new infrastructure, not a naming pass, and is out of scope here.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any

from .agent import Agent, Chat
from .errors import HarnessError
from .result import Result


class SessionExpiredError(HarnessError):
    """Raised by `Session.say()`/`asay()` once past `expires_at`. A session is not
    silently revived by using it past its TTL — the caller must `.fork()` a fresh one or
    start over, the same "visible, not silent" shape this whole design uses elsewhere
    (S-20's `budget.unlimited`, T-7.2's egress default)."""


class SessionModeError(HarnessError):
    """One `Session` is driven synchronously or asynchronously, never both.

    `say()` and `asay()` cannot share a mutual-exclusion primitive: `say()` needs a
    `threading.Lock` (it blocks a whole thread) and `asay()` needs an `asyncio.Lock`
    (blocking the thread would block the event loop, and a `threading.Lock` acquired
    through `to_thread` cannot be released if the await is cancelled while waiting).
    Two different locks do not exclude each other, so mixing the two would leave
    `Chat`'s read-modify-write of `_messages`/`_spent` unguarded — the exact race this
    class exists to prevent, re-introduced by the fix for it.

    Which mode a session is in is decidable at the first call, so it is decided there and
    refused afterwards rather than half-guarded. A `.fork()` starts with no mode and can
    be driven either way (ADR-093).
    """


class Session:
    def __init__(self, agent: Agent, *, owner: str | None = None,
                 ttl_s: float | None = None, budget: Any | None = None,
                 id: str | None = None, chat: Chat | None = None,
                 mode: str | None = None) -> None:
        self.id = id if id is not None else "sess_" + uuid.uuid4().hex[:16]
        self.owner = owner
        self.created_at = time.time()
        self.ttl_s = ttl_s
        self._chat = chat if chat is not None else agent.chat(budget=budget)
        # The concurrency boundary T-8.6 asks for: `Chat.say()` mutates
        # `_messages`/`_spent` with no synchronization of its own — two threads
        # calling `.say()` on the same Session at once would race on both.
        # `threading.Lock`, not `asyncio.Lock`: `Chat.say()` is synchronous (it calls
        # `Agent.try_run()`, which itself does `asyncio.run()` internally) — matching
        # the primitive this wraps rather than forcing an async boundary onto a sync one.
        self._lock = threading.Lock()
        # And the async half of the same boundary, for `asay()` (ADR-093). Created per
        # running loop rather than once: `asyncio.Lock` binds to the loop of its first
        # CONTENDED acquire and then raises `RuntimeError: ... is bound to a different
        # event loop`. Measured — an uncontended acquire never binds, so a single lock
        # reused across two `asyncio.run()` calls works right up until two callers
        # actually contend for it, which is the worst shape a latent bug can have. A lock
        # cannot be held across a loop's lifetime anyway (nothing is running once the
        # loop closes), so rebinding when the loop changes loses nothing.
        self._alock: asyncio.Lock | None = None
        self._aloop: Any = None
        #: `"say()"`, `"asay()"`, or `None` until the first turn — see `SessionModeError`.
        #:
        #: Settable at CONSTRUCTION, which is where the decision is actually made. Left
        #: to the first call, the refusal arrives in a request handler far from the line
        #: that chose wrong — my own review of ADR-093 called that the cheaper mechanism,
        #: and it was (ADR-102). Both still work: `mode=` fails at the choice, and an
        #: unset mode still fixes itself on first use for callers who never mix.
        if mode is not None and mode not in ("say()", "asay()", "sync", "async"):
            raise ValueError(
                f"mode={mode!r} is not a mode. Use \"sync\" (or \"say()\") for a "
                f"session driven with `say()`, \"async\" (or \"asay()\") for one "
                f"driven with `asay()`.")
        self._mode: str | None = (
            None if mode is None
            else {"sync": "say()", "async": "asay()"}.get(mode, mode))

    @property
    def agent(self) -> Agent:
        return self._chat._agent

    @property
    def messages(self) -> list:
        return self._chat.messages

    @property
    def spent(self):
        return self._chat.spent

    @property
    def expires_at(self) -> float | None:
        return None if self.ttl_s is None else self.created_at + self.ttl_s

    def expired(self, *, now: float | None = None) -> bool:
        exp = self.expires_at
        return exp is not None and (now if now is not None else time.time()) >= exp

    def _claim(self, mode: str) -> None:
        """Fix this session's mode on first use, refuse the other one afterwards."""
        if self._mode is None:
            self._mode = mode
            return
        if self._mode != mode:
            other = "say()" if mode == "asay()" else "asay()"
            raise SessionModeError(
                f"session {self.id!r} is already being driven with {other} and cannot "
                f"also be driven with {mode}.\n\n"
                f"  The two use different locks — a `threading.Lock` for the sync path, "
                f"an\n  `asyncio.Lock` for the async one — and two locks do not exclude "
                f"each other, so\n  mixing them would leave this session's history and "
                f"spend unguarded: exactly the\n  race the lock exists to prevent.\n\n"
                f"  Pick one, or `.fork()` for a session that can be driven the other "
                f"way.\n\n"
                f"  -> docs/12-decision-logs.md ADR-093")

    def _refuse_if_expired(self) -> None:
        if self.expired():
            raise SessionExpiredError(
                f"session {self.id!r} expired at {self.expires_at} "
                f"(ttl_s={self.ttl_s}) — call .fork() or start a new Session rather "
                f"than reuse an expired one")

    def _async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._alock is None or self._aloop is not loop:
            self._alock, self._aloop = asyncio.Lock(), loop
        return self._alock

    def say(self, message: str, *, on_delta=None) -> Result:
        """One turn, synchronously. `asay()` is the same turn from a running loop, and a
        session does one or the other — see `SessionModeError`."""
        self._claim("say()")
        self._refuse_if_expired()
        with self._lock:
            return self._chat.say(message, on_delta=on_delta)

    async def asay(self, message: str, *, on_delta=None) -> Result:
        """One turn, from inside a running event loop.

        `say()` cannot be used there at all — it goes through `try_run`, whose
        `_guard_sync` raises rather than deadlocking — so without this a `Session` was
        unusable from async code and the caller had to drop to `agent.chat()` and give up
        the id, owner, TTL and `fork()` that are this class's whole point (ADR-093).

        Everything `Chat.asay` documents applies underneath, including that a cancelled
        turn advances `spent` but not `messages` (ADR-088).
        """
        self._claim("asay()")
        self._refuse_if_expired()
        async with self._async_lock():
            return await self._chat.asay(message, on_delta=on_delta)

    def fork(self, *, owner: str | None = None, ttl_s: float | None = None,
             mode: str | None = None) -> "Session":
        """A NEW `Session` — new id, its own lock — whose history starts as a COPY of
        this one's current messages and spend. A branch point: mutating the fork
        (further `.say()` calls on it) never touches the original, and vice versa
        (T-8.6's own "fork" requirement)."""
        # The mode is NOT inherited: `SessionModeError` tells the caller to fork in order
        # to drive the conversation the other way, so a fork that carried the mode with it
        # would make its own error message false (ADR-102).
        return Session(self.agent, owner=owner if owner is not None else self.owner,
                       ttl_s=ttl_s if ttl_s is not None else self.ttl_s,
                       chat=self._chat.fork(), mode=mode)

    @classmethod
    def resume_from(cls, agent: Agent, transcript: Any, *, owner: str | None = None,
                    ttl_s: float | None = None, id: str | None = None) -> "Session":
        """Wraps the EXISTING `Agent.resume()` transcript-replay mechanism
        (`docs/05-data-and-state.md §3`) in the Session lifecycle (id/owner/TTL) — not
        a richer resume than what already exists. `Agent.resume()` re-issues the
        original message plus a note about any interrupted `write`/`danger` tools in a
        fresh run; it does not reconstruct full conversation history from the
        transcript (nothing in this codebase does that yet). This method's value is
        purely the Session wrapper around that one call, so a caller who wants to keep
        talking after a resumed run has a `Session` to call `.say()` on next, not a
        bare `Result` with nowhere to continue."""
        session = cls(agent, owner=owner, ttl_s=ttl_s, id=id)
        r = agent.resume(transcript)
        session._chat._messages = list(r.messages)
        session._chat._spent = session._chat._spent + r.cost
        return session
