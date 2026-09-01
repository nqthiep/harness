"""LangGraph backend — Round 35.

`build_agent()` compiles an enforcement graph.  The caller never touches the model node
directly, so the guarantees are structural rather than advisory: see `graph.unguarded_paths`.
"""
from __future__ import annotations

from typing import Any, Sequence


from ..budget.ledger import Budget, Ledger
from ..models import pricing
from ..errors import ConfigError
from ..policy.builtin import EffectPolicy, EgressPolicy, TaintPolicy
from ..policy.label import Grants
from ..tools.registry import ToolSet
from ..agent import _check_subagent_safety, _check_tool_set
from .graph import GUARDED, INTERRUPT, build, unguarded_paths
from .runtime import Runtime
from .state import AgentState

__all__ = ["build_agent", "unguarded_paths", "GUARDED", "INTERRUPT", "AgentState"]


def build_agent(*, model, tools: Sequence[Any] = (), budget: Any = None,
                model_name: str = "claude-opus-5", safety: str = "standard",
                # T-7.2 parity with Agent — `()` (deny all) is the default; `None`,
                # passed explicitly, is the unrestricted escape hatch.
                policies: Sequence[Any] = (), allowed_hosts: Sequence[str] | None = (),
                accepts_tainted: Sequence[str] = (), sensitive: Sequence[str] = (),
                approve=None, checkpointer=None, exporters: Sequence[Any] = (),
                max_asks_per_run: int = 20, tenant_id: str | None = None,
                returns: type | None = None,
                require_approval_evidence: bool = False):
    """Compile an agent graph.  Returns (compiled_graph, runtime).

    `exporters=` is the spelling `Agent` uses for the same seam (Round 35 parity). It used
    to build a single `EventBus` right here, held by the returned `Runtime` and shared by
    every thread that graph would ever serve — S-24 (corrected): the same shared-state
    mistake Round 37 found in `Ledger`/`TaintTracker` and S-15 found in `PolicyEngine`, a
    fourth time, undetected because nothing ever asserted `event.run_id` was the real
    thread rather than the constant it was actually stamped with. `Runtime` now builds one
    `EventBus` per thread lazily (`_bus_for`), so `exporters` is handed through unbuilt.

    `returns=` (N-3, closed): validates the final answer against a type, same as the
    classic backend's `Agent(returns=...)` — `finish()` parses it before `run.finished`
    fires. This only closes the PARSE half. The other half — actually asking the model
    for that shape — is `harness.agent._output_format(returns)`, reached through
    `Agent._asm` (`ProviderChatModel._generate()` builds its request from that same
    `ContextAssembler`, so `Agent(durable=True, returns=...)` gets both halves for free).
    Called through the raw escape hatch, `returns=` here validates the answer but does
    NOT itself constrain the model's output — a caller supplying their own LangChain
    `model=` wants that model's own structured-output mechanism
    (`model.with_structured_output(...)`) alongside it.

    `require_approval_evidence=` (S-11, closed): off by default. On, an `approve=`
    callback that resolves an ASK by reporting a `human` `Actor` with no `AuthEvidence`
    gets DENIED instead of trusted — see `policy/decision.py::AuthEvidence`,
    `design/07-risks-and-open-issues.md` S-11.
    """
    toolset = ToolSet(tools)
    # The construction-time refusals are part of the design, not of the loop: Round 35's
    # parity suite found `build_agent` accepted an external+danger tool set that `Agent`
    # refuses outright.  The taint policy would still have caught it mid-run, but that is
    # a demotion from Prevent to Detect on the Poka-Yoke ladder (docs/08 §1).
    grants = Grants(accepts_tainted=frozenset(accepts_tainted),
                    sensitive=frozenset(sensitive))
    _check_tool_set(toolset, grants)
    _check_subagent_safety(toolset, safety)
    ledger = Ledger(Budget.parse(budget))
    # S-15: KHÔNG dựng `PolicyEngine` một lần ở đây với các instance policy người dùng đưa
    # vào. `build_agent()` chạy đúng MỘT LẦN và `Runtime` nó tạo ra phục vụ MỌI thread sau
    # đó (docstring `Runtime` ở dưới) — nên một policy có state (đếm, cache theo tool) mà
    # người dùng lỡ truyền instance thay vì factory sẽ bị MỌI thread dùng chung, không
    # cách nào phát hiện được (không có ranh giới "hết một run" để so trước/sau như backend
    # cổ điển có, vì graph phục vụ nhiều thread đồng thời, không phải tuần tự).
    #
    # Backend cổ điển giải quyết bằng cách dò state thay đổi SAU MỖI run() — "dò ở đây
    # thay vì đoán lúc dựng" (agent.py, Round 34), vì một static check kiểu "có attribute
    # là từ chối" sẽ từ chối nhầm `EgressPolicy` (có cấu hình, không có state). Backend này
    # không có một ranh giới run() sạch để dò như thế, nên đổi chiến lược: BẮT BUỘC mọi
    # policy người dùng phải là factory, và mỗi THREAD (không phải mỗi node, không phải
    # mỗi lần build_agent) nhận đúng MỘT instance riêng — xem `Runtime._engine_for`.
    not_factory = [p for p in policies if not _is_factory(p)]
    if not_factory:
        names = ", ".join(type(p).__name__ for p in not_factory)
        raise ConfigError(
            f"build_agent() nhận một INSTANCE policy ({names}), không phải một class.\n"
            f"\n"
            f"  Trên backend LangGraph, build_agent() chạy đúng MỘT LẦN và agent nó trả về\n"
            f"  phục vụ MỌI cuộc hội thoại sau đó — một instance policy có state (đếm,\n"
            f"  cache) sẽ bị mọi khách hàng dùng chung, không cách nào phát hiện được\n"
            f"  (design/review-security.md S-15).\n"
            f"\n"
            f"  Truyền CLASS thay vì instance, để mỗi thread nhận một bản mới:\n"
            f"\n"
            f"      policies=[{names}]        ← không {names}()\n"
            f"\n"
            f"  -> docs/06-safety.md#4-least-privilege"
        )
    rt = Runtime(model=model.bind_tools([_lc_tool(s) for s in toolset]) if len(toolset) else model,
                 toolset=toolset, ledger=ledger,
                 builtins=(EffectPolicy(), TaintPolicy(grants), EgressPolicy(allowed_hosts)),
                 policy_factories=tuple(policies),
                 price=pricing.price(model_name),
                 max_output=pricing.MAX_OUTPUT.get(model_name, 8_000), model_name=model_name,
                 exporters=exporters, approve=approve, grants=grants,
                 max_asks_per_run=max_asks_per_run, tenant_id=tenant_id, returns=returns,
                 require_approval_evidence=require_approval_evidence)
    compiled = build(rt).compile(checkpointer=checkpointer)

    broken = unguarded_paths(compiled)
    if broken:                                   # cannot happen unless build() changed
        raise AssertionError(f"enforcement gate bypassable: {broken}")
    return compiled, rt


def _lc_tool(spec) -> dict:
    """ToolSpec → the provider-agnostic tool schema LangChain binds."""
    return {"name": spec.name, "description": spec.description,
            "parameters": dict(spec.input_schema)}


def _is_factory(p) -> bool:
    import inspect
    return not inspect.ismethod(getattr(p, "check", None))
