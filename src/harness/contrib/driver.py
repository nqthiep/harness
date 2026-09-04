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
| `LOW`      | the next `Driver.turn()` after this one returns | nothing              | end of turn |

`HIGH` is the one worth understanding, because it is the tier that matches "stop the
action, don't destroy the work": a `Ruling(DENY, reason=...)` both blocks the call AND
delivers the event, since the reason reaches the model as the tool's result
(`denied by policy: <reason>` — measured). The model reads why it was stopped and
reroutes, instead of being cut off blind and starting over.

**The four rules that keep this from being a foot-gun.** Each is enforced here, not
just documented:

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
3. **Only an agent with a durable plan may be preempted.** `Chat.say()` assigns
   `self._messages = list(r.messages)` AFTER `try_run` returns, so a cancelled turn
   raises and the whole turn vanishes from history — the model will not know what it was
   doing. `TaskLedger` on a `Store` (ADR-061) is what survives, so `Driver` REFUSES to
   enable preemption for an agent with no task-list tool unless you say
   `require_durable_plan=False` on purpose.
4. **Preemption is capped.** Every preemption throws away tokens already billed
   (`settle()` charges the provider's real usage). Unbounded events mean starvation —
   the agent never finishes anything. Past `max_preemptions` in `window_s`, a `CRITICAL`
   is served as `HIGH` instead.

**Why this is `contrib` and not core, and not `examples/` either.** Not core: `run.py`
sits at its IDL-13 ceiling of 250 lines and is the single place where a budget check
precedes a model call and a permission check precedes a tool call (ADR-001), so a wake
source does not belong there; and `Sensor` has not earned a seam — `docs/02-architecture.md`
§4's plugin test wants "two genuinely different implementations TODAY — not
hypothetically" and this repo has ONE real one (`examples/vision_sensor.CameraSensor`)
plus the test double below, a test double not being an implementation. A `Driver` also
owns a thread, an event loop and the conversation history, none of which a frozen `Agent`
can hold.

Not `examples/` either, which is where this started: the four rules below are SAFETY
rules, and safety distributed by copy-paste drifts in every fork (ADR-082). So it ships,
without core's compatibility promise — see `harness/contrib/__init__.py` for what that
means and for the four admission criteria.

**The price of preemption, up front:** `Chat`/`Session` cannot be used. `say()` is sync
and `_guard_sync()` raises inside a running loop, so there is no way to cancel it. This
`Driver` calls `agent.atry_run(text, _history=...)` and owns the history list itself.

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
    """One pending event: the most urgent one offered since it was last taken.

    Size one on purpose. A queue would make the agent replay a backlog of stale
    perceptions ("someone arrived" five times), which is worse than saying the newest
    true thing once. Rebinding a frozen `Event` is atomic under the GIL, so a sensor
    task can offer while a sync hook peeks.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: Event | None = None

    def offer(self, event: Event) -> None:
        current = self._pending
        if current is None or event.priority >= current.priority:
            self._pending = event

    def peek(self) -> Event | None:
        return self._pending

    def take(self) -> Event | None:
        event, self._pending = self._pending, None
        return event

    def clear(self) -> None:
        self._pending = None


class WriteInFlight(Middleware):
    """Rule 2's sensor: is a call that changes the world running right now?

    `ToolInvocation` carries `name`/`kwargs`/`result`/`identity` and NOT the tool's
    effect (verified against `middleware.py`), so the guarded names come from the
    caller, who has the toolset. Counted rather than boolean: `write` tools are not
    `parallel_safe` so the harness serialises them within a step, but a `read` tool can
    run beside one and this must not be cleared by the wrong `after_tool`.
    """

    def __init__(self, guarded: Sequence[str]) -> None:
        self.guarded = frozenset(guarded)
        self._depth = 0

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
        event = self.inbox.peek()
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
                 *, ceiling: Priority = Priority.NORMAL) -> None:
        self.inbox, self.carrier, self.ceiling = inbox, carrier, ceiling
        self.announced = 0
        self._armed: Event | None = None

    def after_model(self, call: Any) -> Any:
        from dataclasses import replace

        event = self.inbox.peek()
        if event is None or event.priority > self.ceiling or self._armed is not None:
            return call.response
        self._armed = self.inbox.take()
        self.announced += 1
        block = {"type": "tool_use", "id": INJECTED_CALL_ID,
                 "name": self.carrier, "input": {}}
        return replace(call.response, content=(*call.response.content, block))

    def before_tool(self, call: Any) -> Any:
        if getattr(call.identity, "call_id", None) == INJECTED_CALL_ID:
            event, self._armed = self._armed, None
            raise ShortCircuit(event.text if event else "(sự kiện đã hết hiệu lực)")
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
    """Owns the sensors, the conversation history, and the preemption budget.

    History lives here because `Chat`/`Session` cannot be preempted: `say()` is sync and
    `_guard_sync()` raises inside a running event loop, so a cancellable turn has to be
    `agent.atry_run(text, _history=...)` driven directly. That is the real cost of
    preemption, and it is paid here rather than hidden.
    """

    def __init__(self, agent: Any, *, sensors: Sequence[Sensor] = (),
                 inbox: EventInbox | None = None,
                 write_in_flight: WriteInFlight | None = None,
                 allow_preemption: bool = True,
                 require_durable_plan: bool = True,
                 max_preemptions: int = 3, window_s: float = 60.0,
                 poll_s: float = 0.02) -> None:
        self.agent = agent
        self.sensors = tuple(sensors)
        self.inbox = inbox if inbox is not None else EventInbox()
        self.write_in_flight = write_in_flight
        self.allow_preemption = allow_preemption
        self.max_preemptions, self.window_s = max_preemptions, window_s
        self.poll_s = poll_s
        self.history: list[Any] = []
        self.preemptions: list[float] = []
        self.downgrades = 0

        if allow_preemption and require_durable_plan:
            self._refuse_without_a_durable_plan()

    def _refuse_without_a_durable_plan(self) -> None:
        """Rule 3, as a mechanism rather than a warning in a docstring."""
        names = {t.name for t in self.agent.toolset}
        if names & PLAN_TOOLS:
            return
        raise ConfigError(
            "preemption is enabled, but this agent keeps no plan outside its own "
            "context.\n\n"
            "  A cancelled turn is LOST: `Chat.say()` assigns history only after "
            "`try_run`\n  returns, so the interrupted turn never reaches the "
            "transcript and the model\n  will not know what it was doing — it starts "
            "over and repeats the work.\n\n"
            "  Give it a durable plan (any of "
            f"{', '.join(sorted(PLAN_TOOLS))}), e.g.\n"
            "      CodingProfile(..., enable_findings=True)   # or TaskLedger's tools\n\n"
            "  Or say you accept the loss on purpose:\n"
            "      Driver(..., require_durable_plan=False)\n\n"
            "  -> examples/driver.py, docs/12-decision-logs.md ADR-080"
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

    # -- one turn --------------------------------------------------------------------

    async def turn(self, text: str) -> Served:
        """Run one turn, watching for an event worth preempting it.

        `CancelledError` is re-raised unless WE cancelled — swallowing an outer
        cancellation would break asyncio's protocol, the same care `run.py` takes at its
        own catch site.
        """
        task = asyncio.create_task(
            self.agent.atry_run(text, _history=tuple(self.history)))
        ours = _Watch()
        watcher = asyncio.create_task(self._watch(task, ours))
        try:
            result = await task
        except asyncio.CancelledError:
            if not ours.cancelled:
                raise                       # somebody else cancelled us; honour it
            self.preemptions.append(time.monotonic())
            return Served(None, preempted=True, event=ours.event,
                          cancel_s=(time.monotonic() - ours.at) if ours.at else None)
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
        # History is only advanced on a turn that actually finished. A preempted turn
        # leaves it untouched, which is exactly why rule 3 exists.
        self.history = list(result.messages)
        return Served(result, downgraded=ours.downgraded, event=ours.event)

    async def _watch(self, task: "asyncio.Task[Any]", ours: _Watch) -> None:
        while not task.done():
            event = await self.pump()
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
    from ..models.fake import FakeModel

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
                      provider=FakeModel([
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
