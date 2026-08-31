"""Node implementations — Round 35.

Every invariant the hand-written loop enforced is preserved, in the same order, but as
graph nodes: the 87% of the package that is enforcement ports unchanged (Ledger,
PolicyEngine, TaintTracker, Secret, effect classes), and only the 13% that was the loop
is replaced.
"""
from __future__ import annotations

import asyncio
import json

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from ..budget.ledger import Ledger
from ..errors import BudgetExceeded
from ..observe.events import EventBus, EventKind
from ..policy.base import Ruling, ToolCall, Verdict
from ..policy.builtin import emits_of
from ..policy.decision import Actor, Decision, DecisionLog, Scope
from ..policy.engine import PolicyEngine
from ..policy.label import Grants, Integrity, Label
from ..result import Money, StopReason, Usage
from ..run import CONTINUE, _MAP
from ..secrets import redact, redaction_scope
from ..context.window import CLEARED, EDIT_AT, KEEP_RECENT_STEPS
from ..models.pricing import MAX_CONTEXT
from ..tools import EFFECT_PROFILES
from .graph import INTERRUPT, MAX_PAUSES


class Runtime:
    def __init__(self, *, model, toolset, ledger: Ledger, builtins=(), policy_factories=(),
                 price, max_output: int, model_name: str = "claude-opus-5",
                 exporters=(), approve=None, decisions: DecisionLog | None = None,
                 grants: Grants | None = None) -> None:
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
        # Sổ quyết định. Nó KHÔNG phải trạng thái của một run — nó là audit sink, chung
        # cho graph, và mọi tra cứu đều keyed theo run_id, nên R-4 vẫn giữ.
        self._decisions = decisions if decisions is not None else DecisionLog()

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
        if state.get("step", 0) == 0:
            # `step` lives in checkpointed state (thread-scoped), so this fires once per
            # THREAD — not once per compiled graph. A `self._started` instance flag here
            # was the same R-4 mistake `_bus_cache` above just fixed: it made
            # `RUN_STARTED` fire once EVER across every conversation this Runtime serves,
            # not once per conversation.
            self._emit(state, EventKind.RUN_STARTED, model=self._model_name,
                       tool_names=[t.name for t in self._tools],
                       safety=self._safety(state))
        self._emit(state, EventKind.STEP_STARTED, step=state.get("step", 0))
        if led.remaining_steps() <= 0:
            return {"stop_reason": "step_limit", "detail": "reached the step limit",
                    "ledger": led.snapshot()}
        if led.remaining_wall_clock() <= 0:
            return {"stop_reason": "timeout", "detail": "ran out of time",
                    "ledger": led.snapshot()}
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
                    "ledger": led.snapshot()}
        self._emit(state, EventKind.BUDGET_RESERVED, estimate_usd=str(res.estimate),
                   spent_usd=str(led.spent))
        # `stop_reason` is cleared here, and only here.  It is checkpointed like every
        # other state key, so a thread that finished a turn came back carrying
        # "completed" — and `_after_budget` routed the next turn straight to `finish`.
        # Multi-turn was silently dead: the model was called once per thread, ever, and
        # the caller got their own message echoed back (Round 37).
        return {"spent_usd": str(led.spent.decimal), "ledger": led.snapshot(),
                "max_tokens": max_tokens, "stop_reason": None, "detail": ""}

    def call_model(self, state) -> dict:
        led = self._ledger(state)
        # L-2, design/00-foundation.md §3.2 — nhãn của message model TRƯỚC KHI nó tồn
        # tại, tính trên context NÓ THẤY. Đây là luật "không được quên": câu trả lời tự
        # nhiên "model của ta sinh ra nên TRUSTED" biến ClearToolResults thành đường rửa
        # taint hoàn hảo — xem giải thích đầy đủ ở 00-foundation §3.2 và test
        # `test_e2e_five_invariants.py`/`test_label_l2_l3.py`.
        label_at_generation = self._effective_label(state)
        self._emit(state, EventKind.MODEL_REQUEST, max_tokens=state.get("max_tokens", 0))
        msg = self._model.invoke(state["messages"])
        _stamp_label(msg, label_at_generation)
        led.settle(_RESERVED(state.get("max_tokens", 0)), _usage_of(msg), self._price)
        led.count_step()
        raw = _provider_stop(msg)
        self._emit(state, EventKind.MODEL_RESPONSE, stop_reason=raw, cost_usd=str(led.spent))
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
                ToolCall(c["id"], c["name"], c.get("args", {}), spec), ctx)
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
        for p in state.get("_pending", []):
            if Verdict(p["verdict"]) is not Verdict.ASK:
                out.append(p); continue
            spec = self._tools.get(p["tool"])
            call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec)
            ctx = _Ctx(label=self._effective_label(state), safety=self._safety(state))
            if self._approve is INTERRUPT:
                ok = bool(interrupt({"tool": p["tool"],
                                     "arguments": p["call"].get("args", {}),
                                     "reason": p["reason"]}))
                d = Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                             "approved" if ok else "declined by approver", "approval")
            else:
                d = asyncio.run(self._engine_for(_run_id(state)).resolve(
                    Ruling(Verdict.ASK, p["reason"], "policy"), call, ctx, self._approve))
            self._emit(state, EventKind.POLICY_DECIDED, tool=p["tool"], call_id=p["call"]["id"],
                       verdict=d.verdict.name, reason=d.reason, policy=d.policy)
            # Phê duyệt là một SỰ KIỆN, không phải một cờ. Ghi nó ra sổ, scoped tới đúng
            # lời gọi này: `call_id` khác None nên grant không sống quá lượt — "duyệt vĩnh
            # viễn" không biểu diễn được (policy/decision.py).
            self._decisions.record(Decision(
                id=f"dec-{p['call']['id']}", verdict=d.verdict,
                scope=Scope(tool=p["tool"], args=dict(p["call"].get("args", {})),
                            call_id=p["call"]["id"]),
                actor=(Actor.human("approver", via="callback")
                       if self._approve is not None else Actor.policy(d.policy)),
                decided_at=_now(), expires_at=None, run_id=_run_id(state), reason=d.reason))
            out.append({**p, "verdict": int(d.verdict), "reason": d.reason})
        denied = [ToolMessage(content=f"declined: {p['call']['name']}",
                              tool_call_id=p["call"]["id"], status="error")
                  for p in out if Verdict(p["verdict"]) is Verdict.DENY]
        return {"_pending": [p for p in out if Verdict(p["verdict"]) is Verdict.ALLOW],
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

    def _regate(self, p, state) -> Ruling:
        """I-1 — gate là TIỀN ĐIỀU KIỆN TẠI CHỖ TIÊU THỤ, không phải một cạnh trong graph.

        `unguarded_paths()` chứng minh mọi đường TỪ START tới `tools` đều qua `policy`.
        Nhưng resume nạp checkpoint và chạy tiếp từ node bất kỳ: một run dừng sau
        `approve` vào thẳng đây với `_pending` mang sẵn ALLOW, `policy` bị nhảy qua, và
        grant có thể đã hết hạn hoặc đã bị thu hồi trong lúc pause
        (design/review-security.md S-2, design/04 §3.5).

        Nên node này KHÔNG tin `_pending`. Nó tính lại: engine thuần chạy lại (rẻ, không
        I/O), và bất cứ cái gì còn ASK phải có một grant SỐNG trong sổ. Không có ⇒ từ chối.
        """
        spec = self._tools.get(p["tool"])
        call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec)
        ctx = _Ctx(label=self._effective_label(state), safety=self._safety(state))
        r = self._engine_for(_run_id(state)).decide(call, ctx)
        if r.verdict is not Verdict.ASK:
            return r
        v = self._decisions.lookup(p["tool"], p["call"].get("args", {}),
                                   run_id=_run_id(state), now=_now(),
                                   call_id=p["call"]["id"])
        if v is Verdict.ALLOW:
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
            gate = self._regate(p, state)
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
            self._emit(state, EventKind.TOOL_STARTED, tool=spec.name, call_id=call["id"])
            try:
                args = {k: v for k, v in call.get("args", {}).items()
                        if not k.startswith("_")}
                if spec.subagent is not None:
                    value = _run_subagent(spec, args, led)
                else:
                    value = asyncio.run(spec.fn(**args))
                payload = value if isinstance(value, str) else json.dumps(
                    value, sort_keys=True, ensure_ascii=False, default=str)
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
                           is_error=False)
            except Exception as exc:
                self._emit(state, EventKind.ERROR_RAISED, where="tool", type=spec.name,
                           message=str(exc), retryable=EFFECT_PROFILES[spec.effect].retryable)
                msgs.append(ToolMessage(content=redact(f"{type(exc).__name__}: {exc}"),
                                        tool_call_id=call["id"], status="error"))
        self._emit(state, EventKind.STEP_FINISHED, step=state.get("step", 0),
                   stop_reason="tool_use", tool_calls=[p["tool"] for p in state.get("_pending", [])])
        return {"messages": msgs + self._manage(state["messages"] + msgs, state),
                "_pending": [], "tainted": label.integrity is Integrity.UNTRUSTED,
                # A subagent settles into THIS ledger, so its spend has to reach state or
                # the parent's ceiling leaks exactly as it did in Round 28.
                "spent_usd": str(led.spent.decimal), "ledger": led.snapshot()}

    def finish(self, state) -> dict:
        """The single exit.  Every path out of the graph passes here, so `run.finished`
        cannot be forgotten by a branch (Round 35; the same rule as the two gates)."""
        stop = state.get("stop_reason") or "completed"
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
        return {"stop_reason": stop, "spent_usd": str(led.spent.decimal)}

    def _manage(self, messages, state) -> list:
        """Context growth, ported from T-2.6 (docs/07-cost.md §3).

        Without this a long run walks into the model's context window and the provider
        rejects the request — the cost invariant, not a nicety.  `add_messages` replaces
        a message whose id it already holds, so clearing an old tool result is expressed
        as re-emitting that same message with emptied content: no rewrite of the list,
        and the tool_use/tool_result pairing stays intact (invariant I-3).
        """
        window = MAX_CONTEXT.get(self._model_name, 200_000)
        used = sum(len(str(m.content)) for m in messages) // 4
        if used / window < EDIT_AT:
            return []
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
        self._emit(state, EventKind.CONTEXT_MANAGED, step=state.get("step", 0), strategy="edited",
                   tokens_before=used, messages=len(messages))
        return edited

    # ── helpers ──────────────────────────────────────────────────────────────
    def _safety(self, state) -> str:
        return state.get("workflow", {}).get("safety", "standard")

    def _bus_for(self, run_id: str) -> EventBus:
        if run_id not in self._bus_cache:
            self._bus_cache[run_id] = EventBus(run_id, self._exporters)
        return self._bus_cache[run_id]

    def _emit(self, state, kind, **data) -> None:
        self._bus_for(_run_id(state)).emit(kind, **data)


def _is_new_turn(state) -> bool:
    """True when the newest message came from the caller rather than from the loop."""
    msgs = state.get("messages") or []
    return bool(msgs) and type(msgs[-1]).__name__ == "HumanMessage"


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
