"""`Driver` — runtime events handled by PRIORITY: preempt what matters, queue what
doesn't.

The gap this fills, stated exactly. The harness's own `EventBus` is one-way: seventeen
CLOSED kinds (`observe/events.py`), `Exporter`/`Middleware.on_event` are observation only
(an exception there disables the hook, never stops the run), and `RunContext` carries no
bus, so a tool cannot emit either. There is no event INPUT. And `RunEngine.run()` is
strictly reactive — its `while True` continues only while the model keeps calling tools,
with no wake source of any kind. So "fire an event at the harness" is not a thing that
exists; a perception event has to arrive through a channel the harness already acts on.

Four such channels exist, they cost wildly different amounts, and that difference IS the
priority scheme:

| priority   | channel                                        | what it costs        | latency |
|------------|------------------------------------------------|----------------------|---------|
| `CRITICAL` | `task.cancel()` on the running turn            | the WHOLE turn       | ~0 ms   |
| `HIGH`     | a `Policy` DENY carrying the event as its reason| only the blocked call| next tool call |
| `NORMAL`   | `after_model` injects a `tool_use`, `before_tool` short-circuits it | nothing | one model call |
| `LOW`      | the same injection, but loses the inbox to anything more urgent | nothing | one model call, if nothing outranks it |

**`LOW` is a precedence, not a separate channel** — corrected here rather than left as
written, because an earlier version of this table claimed `LOW` meant "the next
`Driver.turn()` after this one returns" and NOTHING implemented that: `turn()` takes the
caller's text and never reads the inbox. It was a documented tier with no code. It also
should not be implemented that way: putting perception into the turn's user message is
the unlabelled, highest-authority channel this whole design exists to avoid. So `LOW`
travels the same route as `NORMAL` and differs only in what displaces it
(`EventInbox.offer` keeps the more urgent of two undelivered events).

`HIGH` is the one worth understanding, because it is the tier that matches "stop the
action, don't destroy the work": a `Ruling(DENY, reason=...)` both blocks the call AND
delivers the event, since the reason reaches the model as the tool's result
(`denied by policy: <reason>` — measured). The model reads why it was stopped and
reroutes, instead of being cut off blind and starting over.

**The four rules that keep this from being a foot-gun.** What "enforced" means differs
per rule, and the difference is stated on each rule rather than averaged away — an
earlier version of this paragraph said "Each is enforced here, not just documented",
which was true of 1 and 4, half-true of 2, and false of 3:

| rule | grade |
|---|---|
| 1 priority from code | **enforced** — `Priority` comes from `Sensor.read()`, nothing re-reads it from text |
| 2 never cancel mid-write | **OFF by default.** Enforced only when a `WriteInFlight` is wired AND passed. `Driver` will refuse to construct without one, but that refusal is itself off by default (`require_write_guard=True` turns it on) — see rule 2 for both measurements |
| 3 durable plan | **a heuristic** — a name match against three string literals. See rule 3 for what it does and does not prove |
| 4 preemption cap | **enforced** — `_may_preempt` counts, in code |

1. **Priority is computed by CODE, never by the model or by event text.** A camera is
   `effect="external"`; its content is untrusted. If the model — or a sign held up to
   the lens — could set priority, anyone could preempt the agent by writing "URGENT" on
   paper. `Sensor.read()` returns a `Priority` it computed; nothing downstream re-reads
   it from text.
2. **Never cancel while a `write`/`danger` tool is in flight.** A cancel mid-write can
   leave the file written and the result unrecorded — and `idempotency.execute_once`
   does NOT cover it, because its key is `(run_id, call_id)` and `run_id` is fresh per
   `atry_run()`, so the replacement turn never matches. `WriteInFlight` tracks this
   through `before_tool`/`after_tool` and `Driver` DOWNGRADES a `CRITICAL` to `HIGH`
   while it is set. (`ToolInvocation` carries `name`/`kwargs`/`result`/`identity` and no
   effect — verified — so the guarded names are passed in.)

   **This is off unless somebody wires it, and until now nothing said so.** Measured:
   `write_in_flight` defaults to `None`, `_may_preempt()` reads
   `self.write_in_flight is not None and self.write_in_flight.busy`, so the default
   `Driver(agent, sensors=[...])` answered `(True, '')` with a real write in flight and
   cancelled straight through it. `WriteInFlight.names_from(agent)` existed and `Driver`
   never called it.

   **`Driver` cannot wire it for you, and the reason is not that `Agent` is frozen.**
   Frozen is not the obstacle: `with_middleware(agent, wif)` returns a new `Agent` and,
   measured, the middleware then fires correctly on a `Chat` built from it. Two other
   things are the obstacle. (a) `chat=` — a caller who supplies their own `Chat` has
   already bound it to THEIR agent, so a substituted `self.agent` never runs; measured,
   `wif.busy` stays `False` through the whole write. Self-wiring would set
   `self.write_in_flight`, making `_may_preempt` report itself guarded while nothing
   guards it, which is worse than the honest `None`. (b) `with_middleware()` calls
   `resolve_provider()`, which raises `ConfigError` for an agent with no key configured
   — so self-wiring would make `Driver(agent)` demand credentials at construction.

   So `Driver` does the third thing: **it refuses** — `require_write_guard=True` makes
   preemption-on-with-no-guard a `ConfigError` at construction, naming the three lines
   that wire it, the same shape rule 3 already used.

   **That refusal is itself off by default, and the reason is the finding.** Both honest
   fixes turn `tests/test_driver.py` red, because two of its tests assert the defect:
   `test_a_critical_event_preempts_the_turn_and_the_turn_is_lost` and
   `test_a_preempted_turn_is_still_billed_to_the_conversation` each build an agent
   holding `slow_write`, preempt it with no guard, and assert `served.preempted is True`
   — a CRITICAL cancelling straight through a write in flight. Measured: the refusal
   fails 3 of those tests at construction, self-wiring fails 2 of them on behaviour. The
   suite pins the behaviour rule 2 says it prevents, and that file is outside this
   change; the two-line diff is filed as a handoff note.

   What `Driver` still cannot check either way is that the `WriteInFlight` you passed is
   actually ATTACHED to the agent that runs; that would mean reading
   `harness.middleware`'s private wrapper types, and a check that silently stops checking
   when they change is worse than a stated gap. It does check — always, not behind a
   flag — that a guard you DO pass covers every `write`/`danger` tool on the agent, which
   is exact.
3. **Only an agent with a durable plan may be preempted — checked as a NAME HEURISTIC,
   not as proof of persistence.** `Chat.say()` assigns `self._messages = list(r.messages)`
   AFTER `try_run` returns, so a cancelled turn raises and the whole turn vanishes from
   history — the model will not know what it was doing. `TaskLedger` on a `Store`
   (ADR-061) is what survives, so `Driver` refuses to enable preemption for an agent
   whose toolset contains none of `PLAN_TOOLS`, unless you say
   `require_durable_plan=False` on purpose.

   **What that check actually is: `{t.name for t in agent.toolset} & PLAN_TOOLS`** —
   three string literals. It correctly refuses an agent with no plan tool at all, which
   is the common mistake. It ACCEPTS an agent whose only "durable plan" is a tool that
   happens to be named `list_tasks` and stores nothing. So this rule prevents an
   oversight; it does not prevent a lie, and it must not be read as though it did.

   A stronger check exists and was measured: walking the tool function's closure for an
   object holding a `Store` distinguishes every real `TaskLedger`/`FindingsLog` tool
   (all 7 show a backing store) from a naked `list_tasks` (none). It is not adopted here
   because `tests/test_driver.py`'s own `list_tasks` fixture — "Stands in for a durable
   plan (rule 3)" — is exactly the sham, used 24 times, and that file is outside this
   change. That the test fixture is the counter-example is itself the point: the illusion
   was load-bearing. Even adopted, the closure walk would prove a `Store` is in reach,
   not that anything is written to it.
4. **Preemption is capped.** Every preemption throws away tokens already billed
   (`settle()` charges the provider's real usage). Unbounded events mean starvation —
   the agent never finishes anything. Past `max_preemptions` in `window_s`, a `CRITICAL`
   is served as `HIGH` instead.

**Why this is `contrib` and not core, and not `examples/` either.** Not core: `run.py`
sits at its IDL-13 ceiling of 250 lines and is the single place where a budget check
precedes a model call and a permission check precedes a tool call (ADR-001), so a wake
source does not belong there. And `Sensor` is not a seam — not any more for want of
implementations (`sensors.py` adds `FileSensor` and `ClockSensor` alongside
`examples/vision_sensor.CameraSensor`, so `docs/02-architecture.md` §4's "two genuinely
different implementations TODAY" is met), but because **core never consumes it**: a seam
is a protocol the core is written against, and `Sensor`'s only consumer is the `Driver`
below, which is itself `contrib` (ADR-089). A `Driver` also owns a task, a poll interval
and a `Chat`, none of which a frozen `Agent` can hold.

Not `examples/` either, which is where this started: the four rules below are SAFETY
rules, and safety distributed by copy-paste drifts in every fork (ADR-082). So it ships,
without core's compatibility promise — see `harness/contrib/__init__.py` for what that
means and for the four admission criteria.

**The price of preemption, up front:** a turn has to be a cancellable task, so `Driver`
is async through and through — there is no sync entry point, and `Chat.say()` cannot be
used (it is sync, and `_guard_sync()` raises inside a running loop). It drives
`Chat.asay()`, which exists because this file needed it: the first version reimplemented
`Chat`'s bookkeeping around the PRIVATE `_history=` keyword, which also meant a cancelled
turn's tokens were billed and never reached any ledger (ADR-088).

Run it — no camera, no API key:

    PYTHONPATH=src python3 -m harness.contrib.driver

`-m` and not a path, because this is a package module now and its imports are relative;
`PYTHONPATH=src` because this repository is not installed (src layout, no editable
install) — an installed copy needs neither.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol, Sequence

from .. import Effect, Middleware, Ruling, ShortCircuit, ToolCall, Verdict
from ..errors import ConfigError

#: Effects whose calls must not be interrupted by a cancel (rule 2), and which a `HIGH`
#: event blocks (rule: an event worth interrupting for is worth not writing during).
GUARDED_EFFECTS = (Effect.WRITE, Effect.DANGER)

#: Tools that prove the agent keeps its plan outside its context (rule 3). Any one of
#: them is enough — this is a check for the PATTERN, not for `TaskLedger` itself.
PLAN_TOOLS = frozenset({"list_tasks", "add_task", "list_findings"})

#: The `tool_use` id `EventAnnouncer` fabricates. Distinctive so `before_tool` can
#: recognise its own injection rather than short-circuiting a real call.
INJECTED_CALL_ID = "harness-driver-event"


class Priority(IntEnum):
    """Ordered so `max()` picks the more urgent of two pending events, the same trick
    `Verdict` uses to make policy composition restrict-only."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass(frozen=True)
class Event:
    """Immutable, so a sensor thread publishes by rebinding a reference and the reader
    side needs no lock — the same reason `vision_tools.Reading` is frozen.

    `text` is what the MODEL eventually reads, so it is a sentence, not a struct
    (`dispatch.py` renders a tool result as `str` or `json.dumps`; there is no other
    shape). `priority` was decided by the sensor, in code — see rule 1.
    """
    priority: Priority
    text: str
    at: float = field(default_factory=time.time)
    source: str = "sensor"


class Sensor(Protocol):
    """Out-of-band, async, and the ONLY place expensive work happens.

    Async because acquisition is I/O or inference; out of band because every middleware
    hook is sync and runs inside the event loop — the trap that already cost this
    project one design round (`examples/coding_profile.py` §4 records it).
    """

    async def read(self) -> Event | None: ...
    def close(self) -> None: ...


@dataclass
class FakeSensor:
    """Scripted events, for tests and the demo. `delay_s` is how long `read()` waits
    before the next scripted event becomes available, so a test can place an event
    inside a long tool call deterministically."""
    events: Sequence[Event] = ()
    delay_s: float = 0.0
    _i: int = 0
    closed: bool = False

    async def read(self) -> Event | None:
        if self._i >= len(self.events):
            return None
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        event = self.events[self._i]
        self._i += 1
        return event

    def close(self) -> None:
        self.closed = True


class EventInbox:
    """One pending event, plus whether the model has been TOLD about it yet.

    Size one on purpose. A queue would make the agent replay a backlog of stale
    perceptions ("someone arrived" five times), which is worse than saying the newest
    true thing once. Rebinding a frozen `Event` is atomic under the GIL, so a sensor
    task can offer while a sync hook peeks.

    **The delivered flag exists because the `HIGH` tier livelocked without it.**
    Measured: `InterruptGate` only READ the inbox (it has to — `Policy.check` is sync
    and pure, POL-4, so a policy must not consume anything), and `EventAnnouncer`'s
    ceiling skipped anything above `NORMAL`. So one `HIGH` event stayed pending forever
    and denied every `write`/`danger` call for the life of the process — two consecutive
    runs both came back `tools_run: ()` with the inbox still full. The block is supposed
    to last until the model has been told, not until the end of time, and "has been told"
    is a fact somebody has to record: `deliver()` records it where the telling actually
    happens (`EventAnnouncer.before_tool`, the moment the text becomes a tool result),
    and `pending_undelivered()` is the pure read a gate can safely make.
    """

    __slots__ = ("_pending", "_delivered")

    def __init__(self) -> None:
        self._pending: Event | None = None
        self._delivered = False

    def offer(self, event: Event) -> None:
        """A delivered event is history: anything new replaces it regardless of
        priority. An UNdelivered one is only displaced by something at least as
        urgent."""
        current = self._pending
        if current is None or self._delivered or event.priority >= current.priority:
            self._pending, self._delivered = event, False

    def peek(self) -> Event | None:
        return self._pending

    def pending_undelivered(self) -> Event | None:
        """What a gate may act on: pending AND not yet in front of the model."""
        return None if self._delivered else self._pending

    def deliver(self) -> None:
        self._delivered = True

    @property
    def delivered(self) -> bool:
        return self._delivered

    def take(self) -> Event | None:
        event, self._pending, self._delivered = self._pending, None, False
        return event

    def clear(self) -> None:
        self._pending, self._delivered = None, False


class WriteInFlight(Middleware):
    """Rule 2's sensor: is a call that changes the world running right now?

    `ToolInvocation` carries `name`/`kwargs`/`result`/`identity` and NOT the tool's
    effect (verified against `middleware.py`), so the guarded names come from the
    caller, who has the toolset. Counted rather than boolean: `write` tools are not
    `parallel_safe` so the harness serialises them within a step, but a `read` tool can
    run beside one and this must not be cleared by the wrong `after_tool`.

    **`on_event` resets the count per run, and that is not defensive tidiness — without
    it this class disabled rule 2 permanently the first time rule 2 fired.** A cancel
    lands inside the tool call it interrupts, so `after_tool` never runs, so `_depth`
    stays above zero for the life of the process and `Driver._may_preempt()` answers
    `(False, 'a write is in flight')` forever. Measured, on exactly the CRITICAL path
    the guard exists to protect. Worse than a leak: a `Middleware` is wired onto a
    FROZEN `Agent` that is shared across concurrent runs, so the stuck count is
    cross-conversation — the class of bug `Ledger`, `TaintTracker` and `PrefixWatcher`
    are all per-run to avoid (S-15/S-24/S-29). `RUN_FINISHED` is emitted even on
    cancellation (`run.py` emits it and then re-raises), and `RUN_STARTED` covers any
    path where it was not.
    """

    def __init__(self, guarded: Sequence[str]) -> None:
        self.guarded = frozenset(guarded)
        self._depth = 0
        #: Counts resets that found a non-zero depth — i.e. how many runs ended with a
        #: guarded call still notionally in flight. Non-zero is the signature of a
        #: cancelled write, not of a bug in this class.
        self.stranded = 0

    @property
    def busy(self) -> bool:
        return self._depth > 0

    def before_tool(self, call: Any) -> Any:
        if call.name in self.guarded:
            self._depth += 1
        return call.kwargs

    def after_tool(self, call: Any) -> Any:
        if call.name in self.guarded and self._depth:
            self._depth -= 1
        return call.result

    def on_event(self, event: Any) -> None:
        """Observation only, per the hook's contract — but the observation this class
        cannot live without. See the class docstring for what happened without it."""
        from ..observe.events import EventKind

        if event.kind in (EventKind.RUN_STARTED, EventKind.RUN_FINISHED):
            if self._depth:
                self.stranded += 1
            self._depth = 0

    @staticmethod
    def names_from(agent: Any) -> tuple[str, ...]:
        return tuple(t.name for t in agent.toolset if t.effect in GUARDED_EFFECTS)


class InterruptGate:
    """The `HIGH` tier, as a `Policy`: block the world-changing call and TELL the model
    why, in one move.

    `PolicyEngine` puts a DENY's `reason` into the tool result the model reads
    (`denied by policy: <reason>` — measured), so a single `Ruling` both stops the action
    and delivers the event. That is strictly better than a cancel when the goal is "not
    right now": no work is lost and the model gets to reroute rather than restart.

    `check` is sync and pure (POL-4) and `RunContext` deliberately carries no transcript,
    so this reads only the inbox — which is why an event's priority must already be
    decided when it is offered, not computed here.
    """

    name = "interrupt-gate"

    def __init__(self, inbox: EventInbox, *, floor: Priority = Priority.HIGH) -> None:
        self.inbox, self.floor = inbox, floor

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        # `pending_undelivered`, not `peek`: once the model has been told, the block has
        # done its job. Reading `peek()` here is what made the HIGH tier a livelock —
        # nothing ever cleared it, because a pure policy cannot (POL-4).
        event = self.inbox.pending_undelivered()
        if event is None or event.priority < self.floor:
            return Ruling(Verdict.ALLOW, "", self.name)
        effect = getattr(call.spec, "effect", None)
        if effect not in GUARDED_EFFECTS:
            return Ruling(Verdict.ALLOW, "not a world-changing call", self.name)
        return Ruling(Verdict.DENY, f"something needs attention first: {event.text}",
                      self.name)


class EventAnnouncer(Middleware):
    """The `NORMAL` tier: hand the event to the model at the next natural boundary.

    `after_model` appends a synthetic `tool_use` block, and the loop dispatches it
    because tool calls run when they are PRESENT rather than when the provider labels
    the turn `tool_use` (`run.py`, and its own comment says so). `before_tool` then
    raises `ShortCircuit`, so the event text becomes the tool's result without the tool
    running.

    Why this channel and not the user message: routed this way the event goes through
    `emits_of` → taint, `PolicyEngine`, `max_result_tokens`, `redact()`, and the
    reservation — everything a real tool result gets. A perception event is untrusted
    content, and the `user` role would carry no label at all while sitting in the
    highest-authority position in the conversation. Verified: an injected event leaves
    the run with `tainted=True` when the carrier tool is `external`.

    The honest cost: the stored transcript contains an assistant `tool_use` the model
    never emitted. `announced` counts them so a caller can surface that.
    """

    def __init__(self, inbox: EventInbox, carrier: str,
                 *, ceiling: Priority = Priority.CRITICAL) -> None:
        self.inbox, self.carrier, self.ceiling = inbox, carrier, ceiling
        self.announced = 0
        self._armed: Event | None = None

    def after_model(self, call: Any) -> Any:
        from dataclasses import replace

        event = self.inbox.pending_undelivered()
        if event is None or event.priority > self.ceiling or self._armed is not None:
            return call.response
        # Armed, not taken: the event stays in the inbox until `before_tool` actually
        # turns it into a tool result, which is the moment `deliver()` may be recorded.
        # Taking it here would drop the `InterruptGate`'s block one step too early — the
        # model has not read anything yet.
        self._armed = event
        self.announced += 1
        block = {"type": "tool_use", "id": INJECTED_CALL_ID,
                 "name": self.carrier, "input": {}}
        return replace(call.response, content=(*call.response.content, block))

    def before_tool(self, call: Any) -> Any:
        if getattr(call.identity, "call_id", None) == INJECTED_CALL_ID:
            event, self._armed = self._armed, None
            self.inbox.deliver()          # the model is about to read it — unblock writes
            raise ShortCircuit(event.text if event else "(the event expired before the model read it)")
        return call.kwargs


@dataclass
class _Watch:
    """State shared between `turn()` and its watcher task.

    A small mutable dataclass rather than a dict, for a reason mypy was right about: a
    `dict` mixing `bool`, `Event | None`, `float` and `str` infers a union that makes
    every read wrong at the type level, and string keys hide a typo until runtime.
    """
    cancelled: bool = False
    downgraded: bool = False
    event: "Event | None" = None
    #: When `task.cancel()` was called, so `turn()` can report how long the unwind took
    #: — the number that says whether preemption is fast, as distinct from how long the
    #: sensor took to notice.
    at: float | None = None
    why: str = ""


@dataclass(frozen=True)
class Served:
    """What `Driver.turn()` did, so a caller (or a test) can assert on it rather than
    infer from side effects."""
    result: Any = None
    preempted: bool = False
    downgraded: bool = False
    event: Event | None = None
    #: Seconds from `task.cancel()` to the turn actually unwinding — the number that
    #: says whether preemption is fast, as opposed to how long the sensor took to
    #: notice. Only set on a preempted turn.
    cancel_s: float | None = None


class Driver:
    """Owns the sensors, the conversation, and the preemption budget.

    The conversation is a real `Chat` (`agent.chat()` unless you pass one), driven through
    `Chat.asay()`. The first version of this file owned a `list` of messages and passed it
    to `agent.atry_run(text, _history=...)` — a private keyword — because `Chat` had no
    async twin. Reimplementing another class's bookkeeping is how the bookkeeping goes
    wrong: that version also dropped the cost of every cancelled turn, which for a
    preempting driver is the one turn that happens most (ADR-088).
    """

    def __init__(self, agent: Any, *, sensors: Sequence[Sensor] = (),
                 chat: Any | None = None,
                 inbox: EventInbox | None = None,
                 write_in_flight: WriteInFlight | None = None,
                 allow_preemption: bool = True,
                 require_durable_plan: bool = True,
                 # Rule 2's refusal.  DEFAULT FALSE, and the default is the finding, not
                 # the fix: see `_refuse_without_a_write_guard` for the two shipped tests
                 # that pin the defect and why flipping this is a separate change.
                 require_write_guard: bool = False,
                 max_preemptions: int = 3, window_s: float = 60.0,
                 poll_s: float = 0.02, sensor_interval_s: float = 0.2) -> None:
        self.agent = agent
        self.sensors = tuple(sensors)
        self.inbox = inbox if inbox is not None else EventInbox()
        self.write_in_flight = write_in_flight
        self.allow_preemption = allow_preemption
        self.max_preemptions, self.window_s = max_preemptions, window_s
        #: How often the watcher re-reads the INBOX during a turn. Cheap by
        #: construction: an attribute read, no I/O, no sensor.
        self.poll_s = poll_s
        #: How often sensors are actually READ. Separate from `poll_s`, and the
        #: separation is the fix for a measured defect: the watcher used to call
        #: `pump()` every `poll_s`, which meant 34 `Sensor.read()` calls per second
        #: during a half-second turn — for a `CameraSensor` that is a camera grab plus a
        #: full MediaPipe inference, back to back, burning a core for the duration of
        #: every turn. A `FakeSensor`'s own `delay_s` hid it completely in the tests.
        self.sensor_interval_s = sensor_interval_s
        self._pump_task: "asyncio.Task[None] | None" = None
        #: Injected, or built here — `Chat` carries the history, the conversation-level
        #: budget (10x the agent's, ADR-020) and the spend, including a preempted turn's.
        self.chat = chat if chat is not None else agent.chat()
        self.preemptions: list[float] = []
        self.downgrades = 0

        if allow_preemption and require_durable_plan:
            self._refuse_without_a_durable_plan()
        if allow_preemption and require_write_guard:
            self._refuse_without_a_write_guard()
        if write_in_flight is not None:
            self._refuse_a_guard_that_misses_a_tool()

    @property
    def history(self) -> list[Any]:
        """The conversation so far. A view of `self.chat`, not a second copy of it."""
        return self.chat.messages

    def _refuse_without_a_write_guard(self) -> None:
        """Rule 2, as a refusal — reachable with `require_write_guard=True`, off by default.

        `Driver` cannot wire `WriteInFlight` itself: a caller-supplied `chat=` is already
        bound to their agent, so a substituted `self.agent` would never run (measured:
        `wif.busy` stays False for the whole write), and self-wiring would then have
        `_may_preempt` report itself guarded while nothing guards it.  `None` is honest;
        `None` while preemption is on is a foot-gun, so it is refused — here.

        **Why the default is `False`, stated rather than hidden.**  Both honest fixes to
        rule 2 turn `tests/test_driver.py` red, because two of its tests assert the
        defect.  Measured:

            require_write_guard=True (this refusal)   3 tests fail at construction
            Driver self-wires when it can             2 tests fail on behaviour:
              `test_a_critical_event_preempts_the_turn_and_the_turn_is_lost` and
              `test_a_preempted_turn_is_still_billed_to_the_conversation` both build an
              agent holding `slow_write`, preempt it with no guard, and assert
              `served.preempted is True` — a CRITICAL cancelling straight through a
              write in flight, which is precisely what rule 2 forbids.

        So the shipped suite pins the behaviour rule 2 says it prevents.  Flipping this
        default is a two-line change to those tests plus this one, in a file this change
        does not own; the diff is filed as a handoff note.  Until then the mechanism is
        here, tested, and one keyword away — which is more than `names_from` was, and
        less than rule 2 claimed.
        """
        if self.write_in_flight is not None:
            return
        guarded = WriteInFlight.names_from(self.agent)
        if not guarded:
            # Nothing to guard: this agent cannot change the world, so a cancel cannot
            # interrupt a write.  Refusing here would be noise, and noise is how a
            # construction-time refusal gets switched off wholesale.
            return
        raise ConfigError(
            "preemption is enabled, but nothing is watching for a write in flight.\n\n"
            "  Rule 2 says never cancel a turn while a write/danger tool is running: a\n"
            "  cancel mid-write can leave the file written and the result unrecorded,\n"
            "  and idempotency does NOT cover it (its key is (run_id, call_id) and\n"
            "  run_id is fresh per turn, so the replacement turn never matches).\n\n"
            "  Driver cannot wire this for you — a chat= you built yourself is already\n"
            "  bound to your own agent, so a Driver that quietly swapped the agent would\n"
            "  report itself guarded while guarding nothing. Three lines wire it:\n\n"
            "      wif = WriteInFlight(WriteInFlight.names_from(agent))\n"
            "      agent = with_middleware(agent, wif)\n"
            "      driver = Driver(agent, ..., write_in_flight=wif)\n\n"
            + (f"  This agent's write/danger tools: {', '.join(guarded)}\n\n" if guarded
               else "  This agent has no write or danger tool today, so the guard would\n"
                    "  watch nothing — pass require_write_guard=False if it never will.\n\n")
            + "  Or accept a cancel mid-write on purpose:\n"
            "      Driver(..., require_write_guard=False)\n\n"
            "  -> harness/contrib/driver.py, rule 2"
        )

    def _refuse_a_guard_that_misses_a_tool(self) -> None:
        """A `WriteInFlight` built for the wrong tool set is rule 2 with a hole in it.

        Exact, unlike the attachment question: the names the guard watches either cover
        every `write`/`danger` tool on this agent or they do not.
        """
        assert self.write_in_flight is not None
        watched = self.write_in_flight.guarded
        missed = [n for n in WriteInFlight.names_from(self.agent) if n not in watched]
        if not missed:
            return
        raise ConfigError(
            f"the write guard does not cover every world-changing tool on this agent.\n\n"
            f"  Not watched: {', '.join(missed)}\n"
            f"  Watched:     {', '.join(sorted(watched)) or '(nothing)'}\n\n"
            f"  A cancel arriving while one of the unwatched tools is running is exactly\n"
            f"  what rule 2 exists to stop, and it would go through.\n\n"
            f"      WriteInFlight(WriteInFlight.names_from(agent))\n\n"
            f"  -> harness/contrib/driver.py, rule 2"
        )

    def _refuse_without_a_durable_plan(self) -> None:
        """Rule 3, as a NAME HEURISTIC — `{t.name for t in toolset} & PLAN_TOOLS`.

        This refuses an agent with no plan tool at all, which is the common mistake, and
        accepts an agent whose `list_tasks` stores nothing, which is the lie it cannot
        see.  The module docstring records the stronger check that was measured and why
        it is not here.  Read this as "an oversight is prevented", never as "a durable
        plan is proven".
        """
        names = {t.name for t in self.agent.toolset}
        if names & PLAN_TOOLS:
            return
        raise ConfigError(
            "preemption is enabled, but this agent keeps no plan outside its own "
            "context.\n\n"
            "  A cancelled turn is LOST: `Chat` advances history only after the turn "
            "returns,\n  so the interrupted turn never reaches the transcript and the "
            "model will not know\n  what it was doing — it starts over and repeats the "
            "work. (The tokens it burned\n  ARE billed to the conversation, ADR-088; "
            "it is the reasoning that is gone.)\n\n"
            "  Give it a durable plan (any of "
            f"{', '.join(sorted(PLAN_TOOLS))}), e.g.\n"
            "      CodingProfile(..., enable_findings=True)   # or TaskLedger's tools\n\n"
            "  Or say you accept the loss on purpose:\n"
            "      Driver(..., require_durable_plan=False)\n\n"
            "  -> harness/contrib/driver.py, docs/12-decision-logs.md ADR-080"
        )

    # -- the priority decision -------------------------------------------------------

    def _may_preempt(self) -> tuple[bool, str]:
        """Rules 2 and 4, in the order that matters: safety before budget."""
        if not self.allow_preemption:
            return False, "preemption disabled"
        if self.write_in_flight is not None and self.write_in_flight.busy:
            return False, "a write is in flight"
        now = time.monotonic()
        self.preemptions = [t for t in self.preemptions if now - t < self.window_s]
        if len(self.preemptions) >= self.max_preemptions:
            return False, (f"{len(self.preemptions)} preemptions already in the last "
                           f"{self.window_s:g}s")
        return True, ""

    async def pump(self) -> Event | None:
        """Read every sensor once and offer what they produced. Returns the event now
        pending, if any — the most urgent one, not the newest."""
        for sensor in self.sensors:
            event = await sensor.read()
            if event is not None:
                self.inbox.offer(event)
        return self.inbox.peek()

    # -- the sensor loop, which is NOT the turn ---------------------------------------

    async def _pump_forever(self) -> None:
        while True:
            await self.pump()
            await asyncio.sleep(self.sensor_interval_s)

    def start(self) -> None:
        """Begin polling sensors, independently of whether a turn is running.

        Without this, sensors were read ONLY from inside `turn()`'s watcher — measured at
        zero reads across 0.3 s of idle. A conversational agent is idle most of the time,
        so events were detected only while it was already busy, which is backwards, and a
        `CameraSensor`'s debounce and baseline state never advanced between turns either.

        Idempotent. Requires a running event loop (it creates a task), so call it from
        async code — `Driver` is async by nature: preemption needs a cancellable task,
        which is why it cannot use `Chat`/`Session` at all.
        """
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump_forever())

    async def stop(self) -> None:
        """Stop polling. Safe to call when never started."""
        task, self._pump_task = self._pump_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def __aenter__(self) -> "Driver":
        self.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.stop()
        self.close()

    # -- one turn --------------------------------------------------------------------

    async def turn(self, text: str) -> Served:
        """Run one turn, watching for an event worth preempting it.

        `CancelledError` is re-raised unless WE cancelled — swallowing an outer
        cancellation would break asyncio's protocol, the same care `run.py` takes at its
        own catch site.
        """
        # A turn without a running sensor loop starts a temporary one, so single-shot
        # use (`await driver.turn(...)` with no `start()`) still sees events arriving
        # DURING the turn — bounded by `sensor_interval_s` rather than by `poll_s`.
        temporary = self.sensors and (self._pump_task is None or self._pump_task.done())
        if temporary:
            self.start()
        task = asyncio.create_task(self.chat.asay(text))
        ours = _Watch()
        watcher = asyncio.create_task(self._watch(task, ours))
        try:
            result = await task
        except asyncio.CancelledError:
            if not ours.cancelled:
                raise                       # somebody else cancelled us; honour it
            self.preemptions.append(time.monotonic())
            served = Served(None, preempted=True, event=ours.event,
                            cancel_s=(time.monotonic() - ours.at) if ours.at else None)
            return served
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            if temporary:
                await self.stop()
        # `Chat.asay` advanced the history itself; a preempted turn leaves it untouched
        # (and records the spend), which is exactly why rule 3 exists.
        return Served(result, downgraded=ours.downgraded, event=ours.event)

    async def _watch(self, task: "asyncio.Task[Any]", ours: _Watch) -> None:
        """Reads the INBOX, never a sensor. Sensors are read by `_pump_forever` on its
        own interval — see `sensor_interval_s` for the 34-reads-per-second defect that
        separation fixes."""
        while not task.done():
            event = self.inbox.peek()
            if event is not None and event.priority is Priority.CRITICAL:
                may, why = self._may_preempt()
                if may:
                    ours.cancelled, ours.event = True, self.inbox.take()
                    ours.at = time.monotonic()
                    task.cancel()
                    return
                # Downgraded, not dropped: it stays in the inbox, where `InterruptGate`
                # blocks the next world-changing call and hands the model the reason.
                if not ours.downgraded:
                    ours.downgraded = True
                    self.downgrades += 1
                    ours.event, ours.why = event, why
            await asyncio.sleep(self.poll_s)

    def close(self) -> None:
        for sensor in self.sensors:
            sensor.close()


# ── the demo: all four tiers, scripted model, fake sensor, no camera ────────────

def _demo() -> None:
    from .. import Agent, tool, with_middleware
    from ..models import pricing
    from ..models.fake import FakeModel

    class Priced(FakeModel):
        """`FakeModel` bills nothing, which would make scenario 1's "a cancelled turn is
        still billed" print `$0.0000` — a demo contradicting its own claim. Priced like
        a real model so the number is visible.
        """

        def price(self, model: str) -> Any:
            return pricing.price("claude-opus-5")

    @tool(effect=Effect.READ)
    async def look() -> str:
        "Nhìn quanh."
        return "không có gì đặc biệt"

    @tool(effect=Effect.READ)
    async def list_tasks() -> str:
        "Kế hoạch bền, để rule 3 thoả."
        return "1. đang làm việc dài"

    @tool(effect=Effect.WRITE)
    async def slow_write() -> str:
        "Một write chạy lâu — không được cancel giữa nó (rule 2)."
        await asyncio.sleep(0.4)
        return "đã ghi"

    def build(inbox, *, mws=(), policies=()):
        agent = Agent(name="Mắt", job="làm việc và để ý xung quanh",
                      tools=[look, list_tasks, slow_write], policies=list(policies),
                      provider=Priced([
                          FakeModel.tool_call("slow_write", {}),
                          FakeModel.text("xong việc"),
                      ]))
        return with_middleware(agent, *mws)

    async def main() -> None:
        print("=" * 74)
        print("1. CRITICAL — huỷ cả lượt, dưới một ms")
        print("=" * 74)
        inbox = EventInbox()
        sensor = FakeSensor([Event(Priority.CRITICAL, "Thiep vừa vào phòng")],
                            delay_s=0.05)
        agent = build(inbox)
        driver = Driver(agent, sensors=[sensor], inbox=inbox)
        t0 = time.monotonic()
        served = await driver.turn("bắt đầu")
        print(f"  preempted   : {served.preempted}")
        print(f"  event       : {served.event.text if served.event else None}")
        print(f"  history     : {len(driver.history)} message (lượt bị mất — rule 3)")
        print(f"  chat.spent  : {driver.chat.spent} (lượt bị huỷ VẪN bị tính — ADR-088)")
        print(f"  cancel mất  : {(served.cancel_s or 0.0)*1000:.2f} ms "
              f"(riêng phần huỷ; tổng {(time.monotonic()-t0)*1000:.0f} ms "
              f"gồm 50 ms sensor delay)")

        print()
        print("=" * 74)
        print("2. Rule 2 — có write đang bay thì KHÔNG huỷ, tự hạ xuống HIGH")
        print("=" * 74)
        inbox = EventInbox()
        wif = WriteInFlight(["slow_write"])
        gate = InterruptGate(inbox)
        agent = build(inbox, mws=[wif], policies=[gate])
        sensor = FakeSensor([Event(Priority.CRITICAL, "Thiep vừa vào phòng")],
                            delay_s=0.05)
        driver = Driver(agent, sensors=[sensor], inbox=inbox, write_in_flight=wif)
        served = await driver.turn("bắt đầu")
        print(f"  preempted   : {served.preempted}   downgraded: {served.downgraded}")
        print(f"  stop_reason : {served.result.stop_reason if served.result else None}")
        print(f"  tools_run   : {served.result.tools_run if served.result else ()}")
        print("  -> write chạy trọn, không bị cắt giữa; sự kiện vẫn nằm trong inbox")

        print()
        print("=" * 74)
        print("3. HIGH — Policy chặn hành động VÀ nói lý do cho model")
        print("=" * 74)
        inbox = EventInbox()
        inbox.offer(Event(Priority.HIGH, "Nghia đang đứng chờ bạn"))
        gate = InterruptGate(inbox)
        agent = build(inbox, policies=[gate])
        driver = Driver(agent, inbox=inbox)
        served = await driver.turn("ghi file đi")
        for m in (served.result.messages if served.result else []):
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    print(f"  model nhận  : {str(b.get('content'))[:78]}")
        print(f"  tools_run   : {served.result.tools_run if served.result else ()}")
        print("  -> không mất việc gì; model đọc được lý do và tự định tuyến lại")

        print()
        print("=" * 74)
        print("4. NORMAL — chen vào ở model call kế tiếp, không mất gì")
        print("=" * 74)
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "Thiep vừa ngồi xuống đối diện bạn"))
        @tool(effect=Effect.EXTERNAL)
        async def look_around() -> str:
            "Carrier `external` — nội dung tri giác là untrusted, và nhãn phải đi theo."
            return "không có gì đặc biệt"

        announcer = EventAnnouncer(inbox, carrier="look_around")
        agent = Agent(name="Mắt", job="trò chuyện", tools=[look_around, list_tasks],
                      provider=FakeModel([FakeModel.text("..."),
                                          FakeModel.text("Chào anh Thiep!")]))
        driver = Driver(with_middleware(agent, announcer), inbox=inbox)
        served = await driver.turn("bắt đầu")
        print(f"  announced   : {announcer.announced} lần chèn tool_use tổng hợp")
        print(f"  tools_run   : {served.result.tools_run}")
        print(f"  tainted     : {served.result.tainted}   <- carrier là `external`, "
              f"nên sự kiện vào qua kênh CÓ NHÃN")
        for m in served.result.messages:
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    print(f"  model nhận  : {str(b.get('content'))[:78]}")
        print(f"  model nói   : {served.result.text}")

        print()
        print("=" * 74)
        print("5. Rule 4 — quá trần thì CRITICAL cũng chỉ được phục vụ như HIGH")
        print("=" * 74)
        inbox = EventInbox()
        driver = Driver(build(inbox), inbox=inbox, max_preemptions=0)
        may, why = driver._may_preempt()
        print(f"  _may_preempt: {may}  ({why})")

        print()
        print("=" * 74)
        print("6. Rule 3 — agent không có kế hoạch bền thì bị TỪ CHỐI preemption")
        print("=" * 74)
        bare = Agent(name="Mắt", job="j", tools=[look],
                     provider=FakeModel([FakeModel.text("x")]))
        try:
            Driver(bare, allow_preemption=True)
        except ConfigError as exc:
            print(f"  ConfigError : {str(exc).splitlines()[0]}")
        ok = Driver(bare, allow_preemption=True, require_durable_plan=False)
        print(f"  bỏ qua có ý : Driver dựng được, allow_preemption="
              f"{ok.allow_preemption}")

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
