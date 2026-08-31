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

import threading
import time
import uuid
from typing import Any

from .agent import Agent, Chat
from .errors import HarnessError
from .result import Result


class SessionExpiredError(HarnessError):
    """Raised by `Session.say()` once past `expires_at`. A session is not silently
    revived by using it past its TTL — the caller must `.fork()` a fresh one or start
    over, the same "visible, not silent" shape this whole design uses elsewhere
    (S-20's `budget.unlimited`, T-7.2's egress default)."""


class Session:
    def __init__(self, agent: Agent, *, owner: str | None = None,
                 ttl_s: float | None = None, budget: Any | None = None,
                 id: str | None = None, chat: Chat | None = None) -> None:
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

    def say(self, message: str, *, on_delta=None) -> Result:
        if self.expired():
            raise SessionExpiredError(
                f"session {self.id!r} expired at {self.expires_at} "
                f"(ttl_s={self.ttl_s}) — call .fork() or start a new Session rather "
                f"than reuse an expired one")
        with self._lock:
            return self._chat.say(message, on_delta=on_delta)

    def fork(self, *, owner: str | None = None, ttl_s: float | None = None) -> "Session":
        """A NEW `Session` — new id, its own lock — whose history starts as a COPY of
        this one's current messages and spend. A branch point: mutating the fork
        (further `.say()` calls on it) never touches the original, and vice versa
        (T-8.6's own "fork" requirement)."""
        return Session(self.agent, owner=owner if owner is not None else self.owner,
                       ttl_s=ttl_s if ttl_s is not None else self.ttl_s,
                       chat=self._chat.fork())

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
