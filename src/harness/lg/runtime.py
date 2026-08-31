"""Node implementations — Round 35.

Every invariant the hand-written loop enforced is preserved, in the same order, but as
graph nodes: the 87% of the package that is enforcement ports unchanged (Ledger,
PolicyEngine, TaintTracker, Secret, effect classes), and only the 13% that was the loop
is replaced.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from ..budget.ledger import Ledger
from ..dispatch import MAX_ATTEMPTS, RETRY_BACKOFF_MAX_S, RETRY_BACKOFF_S
from ..errors import BudgetExceeded, ToolContractError
from ..idempotency import idempotency_key, once_if_unsafe
from ..observe.events import EventBus, EventKind
from ..policy.base import Ruling, ToolCall, Verdict
from ..policy.builtin import emits_of
from ..policy.decision import POLICY_ENGINE_VERSION, Actor, Decision, DecisionLog, Scope
from ..policy.engine import PolicyEngine
from ..progress import STALL_AFTER, ProgressLedger, stall_reason
from ..policy.label import Grants, Integrity, Label
from ..result import Money, StopReason, Usage
from ..run import CONTINUE, _MAP, parse_returns
from ..secrets import redact, redaction_scope
from ..context.window import CLEARED, COMPACT_AT, EDIT_AT, KEEP_RECENT_STEPS
from ..models.pricing import MAX_CONTEXT
from ..tools import EFFECT_PROFILES
from .graph import INTERRUPT, MAX_PAUSES

#: Bao nhiêu message CUỐI được giữ nguyên khi nén. Đếm bằng message chứ không bằng
#: "bước", vì ở backend này một bước là một `AIMessage` cộng N `ToolMessage` (mỗi lời gọi
#: một cái) — không phải hai như ở vòng lặp classic, nơi mọi `tool_result` gộp vào MỘT
#: message (I-4). Ba bước × (1 + trung bình 2 lời gọi) là khoảng con số này.
_KEEP_RECENT_MESSAGES = KEEP_RECENT_STEPS * 3


class Runtime:
    def __init__(self, *, model, toolset, ledger: Ledger, builtins=(), policy_factories=(),
                 price, max_output: int, model_name: str = "claude-opus-5",
                 exporters=(), approve=None, decisions: DecisionLog | None = None,
                 grants: Grants | None = None, max_asks_per_run: int = 20,
                 tenant_id: str | None = None, idempotency_store=None,
                 returns: type | None = None) -> None:
        # T-8.1 — deployment-level config, like `_grants` just below: fixed for this
        # compiled graph, not per-thread. `session_id` needs no separate field here —
        # LangGraph's own `thread_id` (== `run_id` per `_run_id(state)`) already IS the
        # closest thing this backend has to a session identity (a thread spans many
        # `invoke()` calls, the same shape T-8.6's future `Session` resource names).
        self._tenant_id = tenant_id
        self._model, self._tools = model, toolset
        self._model_name = model_name
        self._budget = ledger.budget          # the spec; the spend lives per turn
        self._builtins = tuple(builtins)
        # S-15: `build_agent()` từ chối bất kỳ policy nào KHÔNG phải factory (xem
        # lg/__init__.py), nên mọi thứ ở đây là callable, chưa gọi. `_engine_for` gọi mỗi
        # cái đúng MỘT LẦN cho mỗi thread, cache theo `run_id` — cùng thread thấy lại đúng
        # instance của chính nó qua các lượt (state hợp lệ, đếm/rate-limit trong MỘT cuộc
        # hội thoại vẫn đúng), một thread khác không bao giờ thấy state của thread này.
        #
        # Cache này KHÔNG vi phạm R-4 theo cách nguy hiểm: nó không phải nguồn sự thật
        # (mất qua restart chỉ khiến policy đó "quên" tiến độ, tự tái tạo sạch ở lần gọi
        # kế — khác `Ledger`/nhãn, nơi mất là sai lệch tiền hoặc bảo mật thật).
        self._policy_factories = tuple(policy_factories)
        self._policy_cache: dict[str, tuple] = {}
        self._price, self._max_output = price, max_output
        # S-24 (corrected): a single `EventBus` built once in `build_agent()` and held
        # here was Round 37's Ledger/TaintTracker bug a third time, never caught for
        # observability — every thread wrote into the SAME bus, so `event.run_id` was the
        # literal string `"run"` for every conversation and `event.seq` was one counter
        # shared across all of them (an audit trail that cannot tell two customers'
        # events apart is not an audit trail). Same fix shape as `_policy_cache`/
        # `_engine_for` (S-15): cache one `EventBus` per thread, keyed and stamped with
        # the real `run_id`, built lazily on first use.
        self._exporters = tuple(exporters)
        self._bus_cache: dict[str, EventBus] = {}
        self._approve = approve
        # Đọc bởi `_run_tools` khi gắn nhãn L-1 lên kết quả tool — S-16/S-3. Cấu hình
        # mức deployment, không đổi giữa các run, nên sống trên object này là an toàn
        # (khác `Ledger`/nhãn của một run, phải sống trong state — xem R-4 ở dưới).
        self._grants = grants if grants is not None else Grants()
        # S-25(b): cấu hình mức deployment, giống `_grants` ngay trên — không đổi giữa các
        # run nên sống trên object này an toàn. Số ĐẾM (asks) thì KHÔNG — nó phải sống
        # trong state đã checkpoint (`AgentState.asks`), thread-scoped, theo đúng R-4.
        self._max_asks_per_run = max_asks_per_run
        # Sổ quyết định. Nó KHÔNG phải trạng thái của một run — nó là audit sink, chung
        # cho graph, và mọi tra cứu đều keyed theo run_id, nên R-4 vẫn giữ.
        self._decisions = decisions if decisions is not None else DecisionLog()
        # T-6.1, finally wired. A crash inside `run_tools` loses the checkpoint for the
        # whole node, so a resumed thread re-executes every call in that batch — a
        # `git_push` that already succeeded included. `thread_id` and the checkpointed
        # `call_id` both survive that restart, which is what makes `f"{run_id}:{call_id}"`
        # worth anything here and worth nothing in the classic loop (see
        # `idempotency.py`'s docstring). Deployment-level config like `_grants`, so it
        # lives on the Runtime; the per-key record lives in the Store the caller supplies.
        self._idempotency_store = idempotency_store
        # N-3 — deployment-level config like `_grants`/`_max_asks_per_run` above: the
        # TYPE never changes between runs, so it lives on the Runtime; the parsed VALUE
        # is per-thread and lives in state (`AgentState.value`).
        self._returns = returns

    # ── everything mutable is derived from graph state ───────────────────────
    #
    # Nothing about a run may live on this object.  LangGraph runs every node in its own
    # copied context, so an attribute set in one node is not there in the next; and a
    # Runtime is built once per compiled graph, so anything it does hold is shared by
    # every conversation that graph serves.  Round 37 found all three consequences at
    # once: a shared ledger billed customer B for customer A's tokens, a shared taint
    # tracker leaked A's taint to B and lost it across a restart, and a checkpointed
    # `stop_reason` made every turn after the first do nothing at all.
    #
    # The rule this replaced them with: **the thread's state is the only memory.**
    def _engine_for(self, run_id: str) -> PolicyEngine:
        """`PolicyEngine` riêng cho thread này — S-15.

        Builtin luôn dùng chung (đã kiểm không mutate `self` trong `check()`); mỗi factory
        policy người dùng được gọi đúng MỘT LẦN cho `run_id` này rồi cache lại, nên các
        lượt sau của CÙNG thread thấy lại đúng instance cũ (rate-limit/đếm trong một cuộc
        hội thoại vẫn đúng) mà một thread khác không bao giờ thấy được.
        """
        if run_id not in self._policy_cache:
            self._policy_cache[run_id] = tuple(f() for f in self._policy_factories)
        return PolicyEngine(self._builtins, self._policy_cache[run_id])

    def _ledger(self, state) -> Ledger:
        """This conversation's ledger, rebuilt from state on every node.

        Parity with `Chat` (docs/03): USD accumulates across the conversation, the step
        ceiling is per turn — accumulating steps too would kill a long conversation
        permanently, every later turn starting already over the limit.
        """
        snap = dict(state.get("ledger") or {})
        if _is_new_turn(state):
            snap["steps"] = 0
        return Ledger(self._budget).restore(snap)

    def _effective_label(self, state) -> Label:
        """L-3, design/00-foundation.md §3.2 — nhãn hiệu dụng là `join` của MỌI message
        còn trong context, TÍNH LẠI mỗi lần gọi, không phải một biến tích luỹ.

        Thay `_tainter(state)` cũ (một `TaintTracker` sticky dựng lại từ một bool duy
        nhất `state["tainted"]`). Bool đó MIỄN NHIỄM với rửa taint qua compaction, nhưng
        chỉ vì nó không bao giờ giảm — cái giá là một run bị coi UNTRUSTED vĩnh viễn sau
        đúng một `web_fetch`, điều 00-foundation gọi là "biến harness thành vô dụng". Mô
        hình per-message này đổi lấy khả năng nhãn giảm hợp lệ (khi message UNTRUSTED
        cuối cùng rời context) bằng việc phải tự phòng rửa taint — xem L-2 ở `call_model`
        và cách `_manage` giữ nhãn khi xoá nội dung.
        """
        eff = Label()
        for m in state.get("messages") or []:
            eff = eff.join(_msg_label(m))
        return eff

    # ── gate 1: nothing reaches the model without a reservation ──────────────
    def budget_gate(self, state) -> dict:
        led = self._ledger(state)
        # S-25(b): reset here, at the one point in the step where `_is_new_turn` is
        # actually true — by the time `approval_gate` runs later in this same step,
        # `call_model` has already appended an AIMessage, so `_is_new_turn(state)` would
        # read False even on a turn's very first step. Same reasoning as `_ledger`'s
        # `steps` reset just above: detect newness once, here, and let every later node
        # in this step read the already-reset value straight from state.
        #
        # The mechanical stall detector (`progress.py`, ADR-062) needs the identical
        # per-turn reset and had been missing it: `stalled_steps`/`seen_calls` were read
        # straight from checkpointed state with no turn boundary at all, so a `Chat`-
        # style conversation that ended one turn at (say) 4-of-6 toward STALL_AFTER, or
        # with `seen_calls` full of that turn's tool signatures, carried that count
        # into the NEXT turn — a routine "check status" step at the start of a brand
        # new turn could repeat a signature from a completely different turn and fire
        # `PROGRESS_STALLED` a few steps in. Found on review; no test had exercised more
        # than one turn against the stall detector.
        new_turn = _is_new_turn(state)
        asks = 0 if new_turn else state.get("asks", 0)
        stalled_steps = 0 if new_turn else state.get("stalled_steps", 0)
        seen_calls = [] if new_turn else list(state.get("seen_calls", ()))
        if state.get("step", 0) == 0:
            # `step` lives in checkpointed state (thread-scoped), so this fires once per
            # THREAD — not once per compiled graph. A `self._started` instance flag here
            # was the same R-4 mistake `_bus_cache` above just fixed: it made
            # `RUN_STARTED` fire once EVER across every conversation this Runtime serves,
            # not once per conversation.
            self._emit(state, EventKind.RUN_STARTED, model=self._model_name,
                       tool_names=[t.name for t in self._tools],
                       safety=self._safety(state))
            # S-20: same one-time-per-thread warning as the classic loop (run.py) —
            # `Budget(usd=None)` is permitted (free providers, IDL-36) but must not be
            # silent (docs/04-interfaces.md, docs/07-cost.md).
            if self._budget.usd is None:
                self._emit(state, EventKind.BUDGET_UNLIMITED, reason="budget.usd is None")
        self._emit(state, EventKind.STEP_STARTED, step=state.get("step", 0))
        if led.remaining_steps() <= 0:
            return {"stop_reason": "step_limit", "detail": "reached the step limit",
                    "ledger": led.snapshot(), "asks": asks,
                    "stalled_steps": stalled_steps, "seen_calls": seen_calls}
        if led.remaining_wall_clock() <= 0:
            return {"stop_reason": "timeout", "detail": "ran out of time",
                    "ledger": led.snapshot(), "asks": asks,
                    "stalled_steps": stalled_steps, "seen_calls": seen_calls}
        # Checked HERE and not in `run_tools`, even though that is where the observation
        # is made: this gate is the only node that clears `stop_reason` (see the comment
        # at the end of this method), so a stop set anywhere upstream of it is wiped
        # before `_after_budget` ever reads it. Sitting beside the step and wall-clock
        # ceilings is also where it belongs — it is a ceiling, on a different axis.
        if stalled_steps >= STALL_AFTER:
            self._emit(state, EventKind.PROGRESS_STALLED, stalled_steps=stalled_steps)
            return {"stop_reason": StopReason.STALLED.value,
                    "detail": stall_reason(stalled_steps),
                    "ledger": led.snapshot(), "asks": asks,
                    "stalled_steps": stalled_steps, "seen_calls": seen_calls}
        text = json.dumps([m.content for m in state["messages"]],
                          ensure_ascii=False, default=str)
        input_tokens = max(1, len(text) // 4)
        try:
            max_tokens = led.size_call(input_tokens, self._price, self._max_output)
            res = led.reserve(input_tokens, max_tokens, self._price,
                              hard_max_input=len(text))
        except BudgetExceeded as exc:
            self._emit(state, EventKind.BUDGET_EXHAUSTED, axis="usd", spent=str(led.spent))
            return {"stop_reason": "budget_exhausted", "detail": str(exc),
                    "ledger": led.snapshot(), "asks": asks,
                    "stalled_steps": stalled_steps, "seen_calls": seen_calls}
        self._emit(state, EventKind.BUDGET_RESERVED, estimate_usd=str(res.estimate),
                   spent_usd=str(led.spent))
        # `stop_reason` is cleared here, and only here.  It is checkpointed like every
        # other state key, so a thread that finished a turn came back carrying
        # "completed" — and `_after_budget` routed the next turn straight to `finish`.
        # Multi-turn was silently dead: the model was called once per thread, ever, and
        # the caller got their own message echoed back (Round 37).
        return {"spent_usd": str(led.spent.decimal), "ledger": led.snapshot(), "asks": asks,
                "max_tokens": max_tokens, "stop_reason": None, "detail": "",
                "stalled_steps": stalled_steps, "seen_calls": seen_calls}

    def call_model(self, state) -> dict:
        led = self._ledger(state)
        # L-2, design/00-foundation.md §3.2 — nhãn của message model TRƯỚC KHI nó tồn
        # tại, tính trên context NÓ THẤY. Đây là luật "không được quên": câu trả lời tự
        # nhiên "model của ta sinh ra nên TRUSTED" biến ClearToolResults thành đường rửa
        # taint hoàn hảo — xem giải thích đầy đủ ở 00-foundation §3.2 và test
        # `test_e2e_five_invariants.py`/`test_label_l2_l3.py`.
        label_at_generation = self._effective_label(state)
        self._emit(state, EventKind.MODEL_REQUEST, max_tokens=state.get("max_tokens", 0))
        # T-6.4 (chaos test "provider timeout"), ported here for parity after the same
        # gap was found and fixed in run.py — nothing here ever caught a provider
        # failure either, so it would have propagated straight out of `graph.invoke()`.
        t0 = time.monotonic()
        try:
            msg = self._model.invoke(state["messages"])
        except Exception as exc:
            self._emit(state, EventKind.ERROR_RAISED, where="provider",
                       type=type(exc).__name__, message=str(exc), retryable=False)
            return {"stop_reason": "error", "detail": f"{type(exc).__name__}: {exc}"}
        _stamp_label(msg, label_at_generation)
        usage = _usage_of(msg)
        led.settle(_RESERVED(state.get("max_tokens", 0)), usage, self._price)
        led.count_step()
        raw = _provider_stop(msg)
        # N-6 parity with run.py — same flat field names OtelExporter (docs/10 §2)
        # already renames; see run.py's comment on this event for the full reasoning.
        self._emit(state, EventKind.MODEL_RESPONSE, stop_reason=raw, cost_usd=str(led.spent),
                   input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                   cache_read_tokens=usage.cache_read_input_tokens,
                   cache_write_tokens=usage.cache_creation_input_tokens,
                   latency_ms=(time.monotonic() - t0) * 1000)
        out = {"messages": [msg], "step": state.get("step", 0) + 1,
               "spent_usd": str(led.spent.decimal), "ledger": led.snapshot()}
        out.update(_classify(raw, bool(getattr(msg, "tool_calls", None)),
                             state.get("paused", 0)))
        return out

    # ── gate 2: nothing reaches a tool without a verdict ─────────────────────
    def policy_gate(self, state) -> dict:
        calls = getattr(state["messages"][-1], "tool_calls", []) or []
        ctx = _Ctx(label=self._effective_label(state), safety=self._safety(state))
        pending, denied = [], []
        for c in calls:
            self._emit(state, EventKind.TOOL_REQUESTED, tool=c["name"], call_id=c["id"])
            spec = self._tools.get(c["name"])
            if spec is None:
                denied.append(ToolMessage(
                    content=f"no tool called {c['name']!r} is available",
                    tool_call_id=c["id"], status="error"))
                continue
            d = self._engine_for(_run_id(state)).decide(
                ToolCall(c["id"], c["name"], c.get("args", {}), spec,
                        idempotency_key(_run_id(state), c["id"])), ctx)
            self._emit(state, EventKind.POLICY_DECIDED, tool=c["name"], call_id=c["id"],
                       verdict=d.verdict.name, reason=d.reason, policy=d.policy)
            if d.verdict is Verdict.DENY:
                denied.append(ToolMessage(content=f"denied by policy: {d.reason}",
                                          tool_call_id=c["id"], status="error"))
            else:
                # The name, never the ToolSpec: everything in graph state is
                # checkpointed, and a ToolSpec holds a callable that no serializer can
                # write.  The spec is runtime configuration, looked up on use (Round 35).
                pending.append({"call": c, "tool": c["name"],
                                "verdict": int(d.verdict), "reason": d.reason})
        return {"_pending": pending, "messages": denied}

    def approval_gate(self, state) -> dict:
        """A surviving ASK becomes ALLOW or DENY — resolved by the same engine the
        hand-written loop uses (ADR-021), never by a second copy of the rule.

        Round 35's parity suite caught two forks here: this node called
        ``approve(question)`` while the documented contract is
        ``approve(ToolCall, RunContext)``, and with no callback it blocked on
        ``interrupt()`` where the loop applies the safety rule.  `approve=INTERRUPT`
        is the one thing only this backend offers: a durable wait that survives a
        process restart.  It is an extra mode, not a different rule.
        """
        out = []
        # S-25(b): the reset (a new turn is a fresh allowance) happens in `budget_gate`,
        # earlier in this same step — see its comment for why it can't happen here.
        asks = state.get("asks", 0)
        for p in state.get("_pending", []):
            if Verdict(p["verdict"]) is not Verdict.ASK:
                out.append(p); continue
            asks += 1
            spec = self._tools.get(p["tool"])
            call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec,
                           idempotency_key(_run_id(state), p["call"]["id"]))
            ctx = _Ctx(label=self._effective_label(state), safety=self._safety(state))
            reported_actor: Actor | None = None
            if asks > self._max_asks_per_run:
                # Approval fatigue is a channel the model controls — deny once the cap is
                # crossed rather than let the (asks+1)-th request get approved on reflex.
                d = Ruling(Verdict.DENY,
                          f"more than {self._max_asks_per_run} approval requests this "
                          f"turn — refusing rather than risk reflex-approval fatigue",
                          "ask-cap")
            elif self._approve is INTERRUPT:
                # S-11: `interrupt()`'s resume payload is a raw value handed back through
                # `Command(resume=...)` — LangGraph's own mechanism, not `resolve()` — so
                # it has no `Approval` channel to report an actor through. Left as the
                # generic placeholder below; extending THIS path would need its own design
                # (07-risks), since `Command(resume=...)`'s shape isn't this harness's.
                ok = bool(interrupt({"tool": p["tool"],
                                     "arguments": p["call"].get("args", {}),
                                     "reason": p["reason"]}))
                d = Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                             "approved" if ok else "declined by approver", "approval")
            else:
                d, reported_actor = asyncio.run(self._engine_for(_run_id(state)).resolve(
                    Ruling(Verdict.ASK, p["reason"], "policy"), call, ctx, self._approve))
            self._emit(state, EventKind.POLICY_DECIDED, tool=p["tool"], call_id=p["call"]["id"],
                       verdict=d.verdict.name, reason=d.reason, policy=d.policy)
            # Phê duyệt là một SỰ KIỆN, không phải một cờ. Ghi nó ra sổ, scoped tới đúng
            # lời gọi này: `call_id` khác None nên grant không sống quá lượt — "duyệt vĩnh
            # viễn" không biểu diễn được (policy/decision.py).
            self._decisions.record(Decision(
                id=f"dec-{p['call']['id']}", verdict=d.verdict,
                scope=Scope(tool=p["tool"], args=dict(p["call"].get("args", {})),
                            server=spec.server if spec is not None else None,
                            call_id=p["call"]["id"]),
                # S-11: `reported_actor` is real identity ONLY when the `approve=`
                # callback returned `Approval(ok, actor=...)` instead of a plain `bool` —
                # the common case still falls back to this placeholder, which is a
                # self-declared "someone called the callback", not verified identity
                # (07-risks: the full fix needs AuthEvidence, not built here).
                actor=(reported_actor if reported_actor is not None else
                       (Actor.human("approver", via="callback")
                        if self._approve is not None else Actor.policy(d.policy))),
                decided_at=_now(), expires_at=None, run_id=_run_id(state), reason=d.reason,
                policy_version=POLICY_ENGINE_VERSION))
            out.append({**p, "verdict": int(d.verdict), "reason": d.reason})
        denied = [ToolMessage(content=f"declined: {p['call']['name']}",
                              tool_call_id=p["call"]["id"], status="error")
                  for p in out if Verdict(p["verdict"]) is Verdict.DENY]
        return {"asks": asks,
                "_pending": [p for p in out if Verdict(p["verdict"]) is Verdict.ALLOW],
                "messages": denied}

    def run_tools(self, state) -> dict:
        """The one place tool output becomes bytes — so the one place redaction must hold.

        Round 35: the port dropped `redaction_scope()` and RT-13 came back.  A tool that
        builds a short-lived Secret, reveals it, and returns a string derived from it
        sent that string to the model in cleartext: the weak registry had already lost
        the Secret by the time `redact()` ran.  The scope is opened here rather than
        around the whole graph because a caller invokes the compiled graph directly —
        a guarantee that depends on the caller remembering something is not a guarantee.
        """
        with redaction_scope():
            return self._run_tools(state)

    def _regate(self, p, state, label: Label | None = None) -> Ruling:
        """I-1 — gate là TIỀN ĐIỀU KIỆN TẠI CHỖ TIÊU THỤ, không phải một cạnh trong graph.

        `unguarded_paths()` chứng minh mọi đường TỪ START tới `tools` đều qua `policy`.
        Nhưng resume nạp checkpoint và chạy tiếp từ node bất kỳ: một run dừng sau
        `approve` vào thẳng đây với `_pending` mang sẵn ALLOW, `policy` bị nhảy qua, và
        grant có thể đã hết hạn hoặc đã bị thu hồi trong lúc pause
        (design/review-security.md S-2, design/04 §3.5).

        Nên node này KHÔNG tin `_pending`. Nó tính lại: engine thuần chạy lại (rẻ, không
        I/O), và bất cứ cái gì còn ASK phải có một grant SỐNG trong sổ. Không có ⇒ từ chối.

        `label`: nhãn TẠI THỜI ĐIỂM NÀY trong vòng lặp `_run_tools`, không phải
        `self._effective_label(state)` tính lại từ `state` — S-27. `state["messages"]`
        chưa hề thấy kết quả của các call ĐÃ chạy TRƯỚC trong CÙNG batch này (chúng chỉ
        được gộp vào `state` ở cuối `_run_tools`, sau khi cả batch xong), nên tính lại từ
        `state` ở đây cho một nhãn CŨ hơn nhãn thật — đúng cửa sổ fail-open mà `fetch_url`
        (external, gây taint) rồi `run_shell` (danger) CÙNG một batch minh hoạ. Gọi nơi
        này với biến `label` đang được cập nhật sống trong vòng lặp mới đóng đúng cửa sổ
        đó; `self._effective_label(state)` vẫn đúng cho MỌI nơi khác gọi nó (đầu batch,
        trước khi bất kỳ call nào trong batch này chạy).
        """
        spec = self._tools.get(p["tool"])
        call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec,
                        idempotency_key(_run_id(state), p["call"]["id"]))
        if label is None:
            label = self._effective_label(state)
        ctx = _Ctx(label=label, safety=self._safety(state))
        r = self._engine_for(_run_id(state)).decide(call, ctx)
        if r.verdict is not Verdict.ASK:
            return r
        v = self._decisions.lookup(p["tool"], p["call"].get("args", {}),
                                   run_id=_run_id(state), now=_now(),
                                   call_id=p["call"]["id"],
                                   server=spec.server if spec is not None else None)
        if v is Verdict.ALLOW:
            # S-29: một `Decision` (TTL 1 giờ chẳng hạn) có thể phủ N lần thực thi khác
            # nhau — trước bản vá, tái dùng một grant sống không ghi gì thêm vào sổ, nên
            # câu "ai cho phép" trả lời được nhưng "chuyện gì đã xảy ra dưới quyền đó" thì
            # không: N lần thực thi thật chỉ để lại đúng MỘT bản ghi audit. Ghi một
            # `Decision` riêng cho LẦN NÀY, khoá theo đúng `call_id` này (không phải
            # `ForeverAllow` — `Decision.__post_init__` đòi `scope.call_id` khi không có
            # `expires_at`) — sổ giờ có một hàng cho mỗi lần thực thi, không chỉ một hàng
            # cho lần cấp gốc.
            self._decisions.record(Decision(
                id=f"dec-{p['call']['id']}-reuse", verdict=Verdict.ALLOW,
                scope=Scope(tool=p["tool"], args=dict(p["call"].get("args", {})),
                            server=spec.server if spec is not None else None,
                            call_id=p["call"]["id"]),
                actor=Actor.policy("decision-log-reuse"), decided_at=_now(),
                expires_at=None, run_id=_run_id(state),
                reason="grant còn sống trong sổ, tái dùng cho lời gọi này",
                policy_version=POLICY_ENGINE_VERSION))
            return Ruling(Verdict.ALLOW, "grant còn sống trong sổ", "decision-log")
        return Ruling(Verdict.DENY,
                      "không có grant còn hiệu lực cho lời gọi này "
                      "(hết hạn, bị thu hồi, hoặc chưa từng được cấp)", "decision-log")

    def _run_tools(self, state) -> dict:
        led = self._ledger(state)
        # L-3 trước vòng lặp — dùng để biết TAINT_RAISED có phải lần đầu không, và để
        # "tainted" ở cuối hàm là quan sát thuần tuý: không node nào ĐỌC LẠI nó để quyết
        # định (00-foundation §3.2 — nhãn hiệu dụng luôn tính lại, không tích luỹ).
        label = self._effective_label(state)
        msgs: list = []
        for p in state.get("_pending", []):
            gate = self._regate(p, state, label)
            if gate.verdict is not Verdict.ALLOW:
                self._emit(state, EventKind.POLICY_DECIDED, tool=p["tool"],
                           call_id=p["call"]["id"], verdict=gate.verdict.name,
                           reason=gate.reason, policy=gate.policy)
                msgs.append(ToolMessage(content=f"declined: {gate.reason}",
                                        tool_call_id=p["call"]["id"], status="error"))
                continue
            call = p["call"]
            spec = self._tools.get(p["tool"])
            if spec is None:                    # tool set changed under a resumed run
                msgs.append(ToolMessage(content=f"tool {p['tool']!r} is no longer available",
                                        tool_call_id=call["id"], status="error"))
                continue
            # T-6.3, parity with dispatch.py::_invoke — read/external retry on failure
            # up to MAX_ATTEMPTS, backed off; write/danger get exactly one attempt, ever
            # (retrying a call with an unknown side-effect outcome is the double-effect
            # class S-4/idempotency exists to guard against — see ADR-042).
            retryable = EFFECT_PROFILES[spec.effect].retryable
            attempts = MAX_ATTEMPTS if retryable else 1
            ok, reason, payload = False, "", ""
            for attempt in range(attempts):
                self._emit(state, EventKind.TOOL_STARTED, tool=spec.name, call_id=call["id"],
                           attempt=attempt)
                # N-1 (design/07-risks-and-open-issues.md): before this, nothing here
                # bounded a single call — only the run's wall-clock, checked once at the
                # START of a step, never WHILE a call is in flight. A `read` tool with no
                # timeout of its own (a hung HTTP call, say) blocked this node forever.
                # `tool_timeout` clamps to whatever wall-clock the run has left, same as
                # `dispatch.py::_invoke` — a per-call timeout longer than the run itself
                # has left is not a real bound.
                timeout = led.tool_timeout(spec.timeout_s)
                try:
                    args = {k: v for k, v in call.get("args", {}).items()
                            if not k.startswith("_")}
                    async def _call(spec=spec, args=args, led=led):
                        if spec.subagent is None:
                            return await spec.fn(**args)
                        # `_run_subagent` is synchronous and spins its own `asyncio.run`,
                        # so it cannot be called from inside the loop `once_if_unsafe`
                        # runs on — a thread hop is what lets a subagent tool get the
                        # same idempotency guard as every other one, and it costs
                        # nothing next to launching a whole child agent.
                        return await asyncio.to_thread(_run_subagent, spec, args, led)

                    async def _guarded(_call=_call, timeout=timeout):
                        async with asyncio.timeout(timeout):
                            return await once_if_unsafe(
                                self._idempotency_store,
                                idempotency_key(_run_id(state), call["id"]),
                                _call, retryable=retryable)
                    value, replayed = asyncio.run(_guarded())
                    payload = value if isinstance(value, str) else json.dumps(
                        value, sort_keys=True, ensure_ascii=False, default=str)
                    ok = True
                    break
                except TimeoutError:
                    reason = ("timed out: run wall-clock budget reached"
                             if timeout < spec.timeout_s else f"timed out after {spec.timeout_s}s")
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                more_left = attempt + 1 < attempts
                time_left = led.remaining_wall_clock() > 0
                if more_left and time_left:
                    self._emit(state, EventKind.ERROR_RAISED, where="tool",
                               type="retrying", message=reason, retryable=True,
                               attempt=attempt)
                    time.sleep(min(RETRY_BACKOFF_S * (2 ** attempt), RETRY_BACKOFF_MAX_S))
            if not ok:
                self._emit(state, EventKind.ERROR_RAISED, where="tool", type=spec.name,
                           message=reason, retryable=retryable)
                msgs.append(ToolMessage(content=redact(reason),
                                        tool_call_id=call["id"], status="error"))
                continue
            limit = spec.max_result_tokens * 4
            if len(payload) > limit:
                payload = payload[:limit] + "\n[truncated]"
            # L-1, design/00-foundation.md §3.2 — nhãn mà KẾT QUẢ tool này mang, sau
            # override `sensitive` của operator nếu có (S-3). `emits_of` hợp nhất ba
            # ý tưởng nghiên cứu tìm được rời rạc: ToolKind của pydantic-ai, tách
            # read/write approval của Microsoft, readOnlyHint/destructiveHint của MCP.
            emitted = emits_of(spec, self._grants, payload)
            before = label
            label = label.join(emitted)
            if label != before:
                self._emit(state, EventKind.TAINT_RAISED, source_tool=spec.name)
            result_msg = ToolMessage(content=redact(payload), tool_call_id=call["id"])
            _stamp_label(result_msg, emitted)
            msgs.append(result_msg)
            self._emit(state, EventKind.TOOL_FINISHED, tool=spec.name, call_id=call["id"],
                       is_error=False, replayed=replayed)
        self._emit(state, EventKind.STEP_FINISHED, step=state.get("step", 0),
                   stop_reason="tool_use", tool_calls=[p["tool"] for p in state.get("_pending", [])])
        # Observed on what the MODEL asked for, not on `_pending`: a model that keeps
        # re-requesting a tool policy keeps denying is stalled in exactly the sense this
        # detects, and `_pending` has already had those calls removed.
        prog = ProgressLedger(seen=state.get("seen_calls", ()),
                              stalled_steps=state.get("stalled_steps", 0))
        prog.observe(_last_tool_calls(state["messages"]))
        return {"messages": msgs + self._manage(state["messages"] + msgs, state),
                "seen_calls": prog.seen, "stalled_steps": prog.stalled_steps,
                "_pending": [], "tainted": label.integrity is Integrity.UNTRUSTED,
                # A subagent settles into THIS ledger, so its spend has to reach state or
                # the parent's ceiling leaks exactly as it did in Round 28.
                "spent_usd": str(led.spent.decimal), "ledger": led.snapshot()}

    def finish(self, state) -> dict:
        """The single exit.  Every path out of the graph passes here, so `run.finished`
        cannot be forgotten by a branch (Round 35; the same rule as the two gates)."""
        stop = state.get("stop_reason") or "completed"
        detail = state.get("detail", "")
        value = None
        # N-3 — parity with `run.py`'s identical placement: only on a clean finish, and
        # only when the caller asked for `returns=`. Reusing `parse_returns` (not a
        # second copy of the rule, R-17) means a malformed answer is diagnosed with the
        # exact same message on both backends.
        if stop == "completed" and self._returns is not None:
            try:
                value = _returns_as_state(parse_returns(_final_text(state["messages"]),
                                                        self._returns))
            except ToolContractError as exc:
                stop, detail = "error", str(exc)
                self._emit(state, EventKind.ERROR_RAISED, where="returns",
                           type="ToolContractError", message=detail, retryable=False)
        self._emit(state, EventKind.STEP_FINISHED, step=state.get("step", 0),
                   stop_reason=stop, tool_calls=[])
        led = self._ledger(state)
        # Tính lại từ message, không đọc `state["tainted"]" — L-3. Cái key đó chỉ còn là
        # quan sát cho người gọi ngoài (parity, event), không node nào trong graph đọc nó
        # để ra quyết định nữa.
        label = self._effective_label(state)
        self._emit(state, EventKind.RUN_FINISHED, stop_reason=stop, steps=state.get("step", 0),
                   cost_usd=str(led.spent), tainted=label.integrity is Integrity.UNTRUSTED,
                   confidentiality=label.confidentiality.name)
        return {"stop_reason": stop, "detail": detail, "value": value,
                "spent_usd": str(led.spent.decimal)}

    def _manage(self, messages, state) -> list:
        """Context growth, ported from T-2.6 (docs/07-cost.md §3).

        Without this a long run walks into the model's context window and the provider
        rejects the request — the cost invariant, not a nicety.  `add_messages` replaces
        a message whose id it already holds, so clearing an old tool result is expressed
        as re-emitting that same message with emptied content: no rewrite of the list,
        and the tool_use/tool_result pairing stays intact (invariant I-3).
        """
        window = MAX_CONTEXT.get(self._model_name, 200_000)
        used = _context_chars(messages) // 4
        if used / window < EDIT_AT:
            return []
        # Ngưỡng nén đọc theo TỈ LỆ, không theo "xoá nội dung đã hết chỗ để xoá": mỗi
        # bước lại làm đúng một kết quả tool cũ đi, nên nhánh xoá LUÔN có việc để làm và
        # nhánh nén sẽ không bao giờ chạy — trong khi cửa sổ vẫn phình, vì một message đã
        # xoá nội dung vẫn tốn phần vỏ và các `AIMessage` mang tool_calls thì không bao
        # giờ được xoá. Ở 80% cửa sổ, xoá thêm một kết quả cũ không phải một phương án.
        if used / window >= COMPACT_AT:                       # noqa: SIM102
            return self._compact(messages, state, used=used, window=window)
        results = [m for m in messages if isinstance(m, ToolMessage)]
        stale = results[:-KEEP_RECENT_STEPS] if len(results) > KEEP_RECENT_STEPS else []
        # `additional_kwargs=dict(m.additional_kwargs)` giữ nguyên nhãn L-1 của message
        # gốc. Bản trước KHÔNG làm điều này — dựng một ToolMessage mới chỉ với content/
        # tool_call_id/id đã âm thầm làm rớt additional_kwargs, tức xoá nhãn UNTRUSTED
        # cùng lúc với xoá nội dung. Một reviewer chỉ ra đó là đường rửa taint hoàn hảo
        # bằng đúng thao tác mà tài liệu này gọi là an toàn (design/review-security.md
        # S-19): message rỗng vẫn phải mang nhãn cũ để còn tham gia `join` ở L-3.
        edited = [ToolMessage(content=CLEARED, tool_call_id=m.tool_call_id, id=m.id,
                              additional_kwargs=dict(m.additional_kwargs))
                  for m in stale if m.content != CLEARED and m.id]
        if not edited:
            return []
        self._emit(state, EventKind.CONTEXT_MANAGED, step=state.get("step", 0),
                   strategy="edited", tokens_before=used, messages=len(messages))
        return edited

    def _compact(self, messages, state, *, used: int, window: int) -> list:
        """Bỏ hẳn những bước cũ nhất khi không còn gì để xoá nội dung — nhưng KHÔNG được
        bỏ nhãn theo (S-19).

        Nhãn hiệu dụng ở backend này được TÍNH LẠI từ các message còn trong context
        (`_effective_label`, L-3), nên xoá một `ToolMessage` UNTRUSTED khỏi state chính là
        hạ nhãn của cả run xuống — một đường rửa taint hoàn hảo, bằng đúng thao tác mà
        việc nén context gọi là dọn dẹp. Bản cài này vì thế không xoá trắng: nó gộp nhãn
        của MỌI message bị bỏ vào một `ToolMessage` bia mộ duy nhất, rỗng nội dung nhưng
        mang `join` của các nhãn đó, và giữ bia mộ ấy lại trong context.

        Cặp `tool_use`/`tool_result` luôn đi cùng nhau (I-3): một `AIMessage` mang
        tool_calls chỉ bị bỏ khi mọi `ToolMessage` trả lời nó cũng bị bỏ trong cùng lượt.
        """
        from langchain_core.messages import RemoveMessage

        # `messages[1:...]` — chỉ số 1 là chỗ nhiệm vụ gốc được giữ lại, không phải một
        # lát cắt tuỳ tiện: bỏ nó đi thì model mất luôn việc nó đang làm.
        keep_from = len(messages) - _KEEP_RECENT_MESSAGES
        droppable = [m for m in messages[1:keep_from] if getattr(m, "id", None)]
        if not droppable:
            return []
        label = Label()
        for m in droppable:
            label = label.join(_msg_label(m))
        tomb = ToolMessage(content=CLEARED, tool_call_id="compacted",
                           id="harness-compaction-tombstone")
        _stamp_label(tomb, label)
        self._emit(state, EventKind.CONTEXT_MANAGED, step=state.get("step", 0),
                   strategy="compacted", tokens_before=used, messages=len(messages),
                   messages_dropped=len(droppable))
        return [RemoveMessage(id=m.id) for m in droppable] + [tomb]

    # ── helpers ──────────────────────────────────────────────────────────────
    def _safety(self, state) -> str:
        return state.get("workflow", {}).get("safety", "standard")

    def _bus_for(self, run_id: str) -> EventBus:
        if run_id not in self._bus_cache:
            self._bus_cache[run_id] = EventBus(run_id, self._exporters,
                                               tenant_id=self._tenant_id,
                                               session_id=run_id)
        return self._bus_cache[run_id]

    def _emit(self, state, kind, **data) -> None:
        self._bus_for(_run_id(state)).emit(kind, **data)


def _is_new_turn(state) -> bool:
    """True when the newest message came from the caller rather than from the loop."""
    msgs = state.get("messages") or []
    return bool(msgs) and type(msgs[-1]).__name__ == "HumanMessage"


def _context_chars(messages) -> int:
    """Kích thước context, tính cả THAM SỐ của tool_call.

    Bản trước cộng đúng `len(str(m.content))`. Trên LangChain, một `AIMessage` chỉ mang
    tool_calls có `content == ""` — tham số nằm ở `.tool_calls`, không nằm ở `.content`.
    Nên phép đo cũ bỏ sót đúng cái phần KHÔNG BAO GIỜ được xoá nội dung: một agent code
    gọi `edit_source(path, old, new)` bốn mươi lần được tính là ~0 ký tự, và việc nén
    context không bao giờ chạy — cho tới lúc provider từ chối request. Vòng lặp classic
    không có lỗi này vì nó đo bằng `canonical(message)`, tức cả khối `tool_use`.
    """
    total = 0
    for m in messages:
        total += len(str(m.content))
        calls = getattr(m, "tool_calls", None)
        if calls:
            total += len(str(calls))
    return total


def _final_text(messages) -> str:
    """The model's final answer as plain text — N-3. `AIMessage.content` is a `str` for
    most chat models but a list of content blocks for some (Anthropic's own SDK shape),
    same ambiguity `run.py` already resolves for `resp.content`; handled the identical
    way here so a malformed `returns=` answer is diagnosed off the same text on both
    backends."""
    content = messages[-1].content if messages else ""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") == "text")


def _returns_as_state(value: Any) -> Any:
    """`parse_returns` may hand back a dataclass INSTANCE (the classic loop's
    `Result.value` holds exactly that) — state is checkpointed, and a class instance is
    not guaranteed to round-trip through a checkpointer the way a `dict` is (IDL-42's
    same reasoning, one level up). Convert only when needed; the parsed JSON for a
    non-dataclass `returns=` is already a plain `dict`/`list`/scalar and passes through
    unchanged."""
    import dataclasses
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


def _last_tool_calls(messages) -> list:
    """The most recent batch of tool calls the model asked for. Walked backwards rather
    than read off `messages[-1]`: the policy gate appends a `ToolMessage` for every
    denied call, so the last message is often not the model's."""
    for m in reversed(list(messages)):
        calls = getattr(m, "tool_calls", None)
        if calls:
            return list(calls)
    return []


def _provider_stop(msg) -> str:
    """The provider's own stop reason, as LangChain hands it back."""
    meta = getattr(msg, "response_metadata", None) or {}
    return str(meta.get("stop_reason") or meta.get("finish_reason") or "")


def _classify(raw: str, has_tool_calls: bool, paused: int = 0) -> dict:
    """Map the provider's stop reason with the SAME table the hand-written loop uses.

    Round 38: this node never looked at the stop reason at all — it only checked whether
    the message carried tool calls.  A `refusal` (HTTP 200) and a `max_tokens` truncation
    both have no tool calls, so both routed to `finish` and were reported as
    **completed**: the caller got half an answer labelled as a whole one, on the mandated
    backend.  IDL-30 says an unrecognised stop reason maps to ERROR and never to a
    success; here two *recognised* failures were mapped to success.

    Imported rather than re-listed, because two copies of one table is how the two
    backends drift (R-17).
    """
    if not raw or raw == "tool_use" or has_tool_calls:
        return {"paused": 0}
    if raw in CONTINUE:
        n = paused + 1
        if n > MAX_PAUSES:              # parity with the loop: loud, not a quiet success
            return {"paused": n, "stop_reason": "error",
                    "detail": f"the model paused {n} times in a row without finishing; "
                              f"stopping rather than paying for a loop"}
        return {"paused": n}            # not finished; routed back to the budget gate
    mapped = _MAP.get(raw)
    if mapped is None:
        return {"stop_reason": "error", "detail": f"unknown stop reason {raw!r}"}
    if mapped is StopReason.COMPLETED:
        return {"paused": 0}
    detail = ("the answer got cut off because it reached its token ceiling"
              if mapped is StopReason.TRUNCATED else "the model declined this request")
    return {"stop_reason": mapped.value, "detail": detail}


def _RESERVED(max_tokens: int):
    """`settle` needs a reservation's id only, to close it on the ledger it was opened
    on.  Here every node builds its own ledger, so the open set is always empty and the
    id is free — the accounting that matters is the snapshot in state."""
    from ..budget.ledger import Reservation
    return Reservation("state", Money.ZERO, 0, max_tokens, 0.0)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _msg_label(msg) -> Label:
    """L-1/L-2 — đọc nhãn đã gắn trên một message. Không có gì gắn ⇒ mặc định
    TRUSTED/PUBLIC: `HumanMessage`/`SystemMessage` không bao giờ được stamp và đó chính
    là gốc tin cậy — người vận hành gõ nó, không phải một tool.
    """
    kw = getattr(msg, "additional_kwargs", None) or {}
    i = kw.get("label_integrity")
    c = kw.get("label_confidentiality")
    from ..policy.label import Confidentiality
    return Label(Integrity[i] if i else Integrity.TRUSTED,
                Confidentiality[c] if c else Confidentiality.PUBLIC)


def _stamp_label(msg, label: Label) -> None:
    """Gắn nhãn LÊN CHÍNH message (mutate `additional_kwargs`, không tạo bản sao) —
    message vừa dựng, chưa vào `state["messages"]`, nên đây là nơi rẻ nhất để gắn.
    Lưu bằng TÊN enum (`"UNTRUSTED"` chứ không phải `1`) để checkpoint đọc được bằng mắt,
    cùng quy ước với `spent_usd` là `str(Decimal)` chứ không phải một float.
    """
    msg.additional_kwargs["label_integrity"] = label.integrity.name
    msg.additional_kwargs["label_confidentiality"] = label.confidentiality.name


def _run_id(state) -> str:
    """Danh tính run.

    KHÔNG đọc từ `state["run_id"]` — không có khoá đó trong `AgentState`, và LangGraph âm
    thầm bỏ mọi khoá không khai báo trong schema (docs/state.py, IDL-41). Bản đầu của hàm
    này đọc `state.get("run_id")` và LUÔN trả về "-": test đơn vị tự tạo state dict thì
    không lộ ra (chính người viết state dict điền đúng khoá "run_id"), nhưng một
    `graph.invoke()` thật thì lộ ngay — mọi run bị gộp vào một sổ chung
    (design/review-security.md S-2 loại phụ, tìm thấy ở bước 2 chứ không phải bước 1).

    `thread_id` từ config là danh tính đúng: nó ổn định suốt vòng đời một cuộc hội thoại,
    kể cả qua resume, và đó chính xác là phạm vi mà một `Decision` phải được cô lập theo
    (một khách hàng không được dùng grant của khách hàng khác).
    """
    from langgraph.config import get_config
    try:
        cfg = get_config()
    except RuntimeError:                  # gọi ngoài một node đang chạy (test đơn vị)
        return str(state.get("run_id") or "-")
    return str(cfg.get("configurable", {}).get("thread_id") or "-")


class _Ctx:
    __slots__ = ("label", "safety")
    def __init__(self, *, label: Label, safety: str) -> None:
        self.label, self.safety = label, safety


def _run_subagent(spec, args: dict, led: Ledger) -> str:
    """§06.4 / ADR-030, ported to the graph backend (Round 41).

    A subagent tool is not `spec.fn` — calling it directly raises, which is what the
    graph did: the AssertionError went to the model **as a tool result**, so the agent
    read "subagent tools are dispatched, not called directly" and carried on. Built,
    documented, and broken on the mandated backend.

    The budget rule is the part that matters: the child is capped by the parent's
    remaining headroom, and the headroom is **held**, not read — parallel children each
    reading `remaining_usd()` all claimed the whole of it (Round 28).

    S-13: đó chỉ đúng cho trục `usd`. Trước bản vá này, `steps`/`wall_clock_s` của con
    được kế thừa NGUYÊN VẸN từ `Budget` con tự khai — bốn sub-agent spawn trong một lượt,
    mỗi đứa tự khai `steps=20`, có thể tiêu tới 80 step trong khi trần của run gốc chỉ có
    20. `hold_steps()`/`release_steps()` áp đúng lý luận TOCTOU của `hold()` sang trục
    step; `wall_clock_s` không cần hold/release (không phải hồ tài nguyên bị chia — hai
    con chạy song song không cộng dồn thời gian của nhau), chỉ cần một cận trên tại thời
    điểm spawn (`child_wall_clock`).
    """
    from dataclasses import replace as _replace

    child = spec.subagent
    remaining = led.remaining_usd()
    want = Money(child.budget.usd) if child.budget.usd is not None else None
    held = led.hold(want) if remaining is not None and want is not None else None
    held_steps = led.hold_steps(child.budget.steps)
    child_wc = led.child_wall_clock(child.budget.wall_clock_s)
    run_child = child.with_(budget=_replace(
        child.budget, usd=(held.decimal if held is not None else child.budget.usd),
        steps=held_steps, wall_clock_s=child_wc))
    r = asyncio.run(run_child.atry_run(args.get("task", "")))
    if held is not None:
        led.release(held, r.cost)
    else:
        led.charge(r.cost)
    led.release_steps(held_steps, r.steps)
    return r.text if r.ok else f"{child.name} stopped: {r.stop_reason.value}. {r.text}"


def _usage_of(msg) -> Usage:
    u = getattr(msg, "usage_metadata", None) or {}
    details = u.get("input_token_details", {}) or {}
    return Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                 details.get("cache_read", 0), details.get("cache_creation", 0))
