"""LangGraph backend — Round 35.

`build_agent()` compiles an enforcement graph.  The caller never touches the model node
directly, so the guarantees are structural rather than advisory: see `graph.unguarded_paths`.
"""
from __future__ import annotations

from typing import Any, Sequence


from ..budget.ledger import Budget, Ledger
from ..memory.base import Store
from ..models import pricing
from ..errors import ConfigError
from ..policy.builtin import builtins_for
from ..policy.decision import DecisionLog
from ..policy.label import Grants
from ..tools.registry import ToolSet
# From `guards`, not from `agent`: importing the facade to reach the guards was the
# last real import cycle in core (ADR-098).
from ..guards import _check_subagent_safety, _check_tool_set, _is_factory
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
                require_approval_evidence: bool = False,
                idempotency_store: Store | None = None,
                decisions: DecisionLog | None = None):
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

    `idempotency_store=` (T-6.1, closed): `None` by default — a `write`/`danger` tool
    call whose fn() already succeeded before a process crash inside `run_tools` gets
    re-executed on resume, same as always. Supply a real `Store` (`memory.SqliteStore`,
    say) and it gets replayed instead: `thread_id`/`call_id` both survive that restart,
    which is what makes `idempotency_key(run_id, call_id)` durable on this backend in a
    way it never can be on the classic loop (see `idempotency.py`'s module docstring).

    `decisions=` (ADR-063, closed): `None` builds a fresh in-memory `DecisionLog` per
    compiled graph — `Runtime` has accepted this parameter since S-29, but nothing here
    ever forwarded it. Supply your own (`DecisionLog(journal="approvals.jsonl")`, say)
    to keep the approval book across a restart, same as `Agent(decisions=...)` on the
    classic backend.
    """
    toolset = ToolSet(tools)
    # The construction-time refusals are part of the design, not of the loop: Round 35's
    # parity suite found `build_agent` accepted an external+danger tool set that `Agent`
    # refuses outright.  The taint policy would still have caught it mid-run, but that is
    # a demotion from Prevent to Detect on the Poka-Yoke ladder (docs/08 §1).
    grants = Grants(accepts_tainted=frozenset(accepts_tainted),
                    sensitive=frozenset(sensitive))
    _check_tool_set(toolset, grants)
    _check_subagent_safety(toolset, safety, approve)
    ledger = Ledger(Budget.parse(budget))
    # S-15: no `PolicyEngine` is built here from caller-supplied policies. Every entry in
    # `policies=` must be a FACTORY, and each thread gets exactly one instance of its own
    # (`Runtime._engine_for`). Every entry is validated here, at construction — see
    # `_check_policy_factories` for why calling each one is the validation, and for the
    # three defects the old instance-only check left open.
    _check_policy_factories(policies)
    rt = Runtime(model=model.bind_tools([_lc_tool(s) for s in toolset]) if len(toolset) else model,
                 toolset=toolset, ledger=ledger,
                 builtins=builtins_for(grants, allowed_hosts),
                 policy_factories=tuple(policies),
                 price=pricing.price(model_name),
                 max_output=pricing.MAX_OUTPUT.get(model_name, 8_000), model_name=model_name,
                 exporters=exporters, approve=approve, grants=grants,
                 max_asks_per_run=max_asks_per_run, tenant_id=tenant_id, returns=returns,
                 require_approval_evidence=require_approval_evidence,
                 idempotency_store=idempotency_store, decisions=decisions)
    compiled = build(rt).compile(checkpointer=checkpointer)

    broken = unguarded_paths(compiled)
    if broken:                                   # cannot happen unless build() changed
        raise AssertionError(f"enforcement gate bypassable: {broken}")
    return compiled, rt


#: The two spellings that actually work for a policy that takes arguments — the ones
#: `docs/06-safety.md:206` and `tests/test_advisor_gate.py:211` use, and the ones the
#: error message did NOT contain until this was fixed.
_WORKING_SPELLINGS = (
    "      policies=[lambda: {name}({args})]\n"
    "      policies=[functools.partial({name}, {args})]"
)


def _factory_name(p: Any) -> str:
    """The name to print for something the caller passed as a policy factory."""
    inner = getattr(p, "func", p)                      # unwrap functools.partial
    return getattr(inner, "__name__", None) or type(inner).__name__


def _required_args(p: Any) -> list[str]:
    """Best-effort list of the arguments a factory still needs.  Empty if unknowable."""
    import inspect
    try:
        params = inspect.signature(p).parameters.values()
    except (TypeError, ValueError):                    # C types, exotic callables
        return []
    return [q.name for q in params
            if q.default is inspect.Parameter.empty
            and q.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]


def _as_call(names: Sequence[str]) -> str:
    """`['tool', 'requires']` -> `tool=..., requires=...`.

    Keyword form, always: `RequireBeforePolicy`'s parameters are keyword-ONLY, so a
    message that printed `RequireBeforePolicy(tool, requires)` would be handing the
    caller a second thing that does not run — which is the whole defect this message
    was rewritten to stop repeating.
    """
    return ", ".join(f"{n}=..." for n in names) or "..."


def _check_policy_factories(policies: Sequence[Any]) -> None:
    """Refuse a bad `policies=` entry HERE, at construction — never on the first tool call.

    S-15 is why every entry must be a factory rather than an instance: `build_agent()`
    runs exactly once and the `Runtime` it returns serves EVERY thread afterwards
    (`Runtime` docstring), so one instance with state — a counter, a per-tool cache —
    would be shared by every customer with no way to notice.  The classic backend probes
    for changed state after each `run()` ("detect here rather than guess at construction",
    `agent.py`) because a static "has attributes, therefore refuse" check would wrongly
    refuse `EgressPolicy`, which is configured but stateless.  A graph serving many
    threads at once has no clean run() boundary to probe across, so this backend takes
    the other strategy: require a factory, and give each THREAD exactly one instance
    (`Runtime._engine_for`).

    This message is English.  Measured across `src/`: 108 raise sites carry a literal
    message, 99 English and 9 Vietnamese, and this was one of the 9 — a caller who hits
    it is reading the rest of the package in English.

    The instance check alone was not enough, and got the advice wrong on top of it.
    Measured before this function existed, on the shipped parameterised built-in
    `RequireBeforePolicy(tool=..., requires=...)`:

        instance                          ConfigError — telling you to pass the CLASS
        bare class (following that advice) TypeError from library internals, mid-run
        functools.partial(...)            works
        lambda: RequireBeforePolicy(...)  works

    Three separate defects.  The advice was wrong for every parameterised policy the
    library ships; the failure it produced was a raw `TypeError` rather than a
    `ConfigError`; and it was LAZY — `_engine_for` builds factories on the first tool
    request, so a run that happens to call no tool completes normally and the
    misconfiguration is never seen.  That last one inverts `docs/02-architecture.md:311`:
    "Configuration error — raised at `Agent(...)` construction or at `@tool` import.
    Never at run time."

    So every factory is CALLED once, right here.  Calling it is the validation: a
    signature check would miss `lambda: RequireBeforePolicy()` and anything else that
    only fails once invoked.  The instance is then discarded — `_engine_for` builds each
    thread's own, and reusing this one would put back the sharing S-15 forbids.  A
    factory with side effects therefore runs one extra time at construction; that is the
    price of the guarantee, and a policy factory that cannot be called twice is already
    broken on a backend that calls it once per thread.

    [Inference] What this still cannot catch is a factory that succeeds here and fails
    later — one that raises only on its second call, or only under a condition this
    build-time call does not reproduce.  Nothing short of calling it per thread would,
    and that is `_engine_for`'s job.
    """
    for p in policies:
        name = _factory_name(p)
        if not _is_factory(p):
            takes_nothing = not _required_args(type(p))
            bare = (f"  {name} takes no arguments, so the bare class works too:\n"
                    f"\n      policies=[{name}]\n" if takes_nothing else
                    f"  A bare class works only when the policy takes no arguments —\n"
                    f"  {name} takes {', '.join(_required_args(type(p)))}.\n")
            raise ConfigError(
                f"build_agent() got a policy INSTANCE ({name}), not something that "
                f"builds one.\n"
                f"\n"
                f"  On the LangGraph backend build_agent() runs exactly ONCE, and the\n"
                f"  agent it returns serves EVERY later conversation. One instance would\n"
                f"  be shared by every customer, so any state it keeps — a counter, a\n"
                f"  per-tool cache — would leak between them with nothing able to notice\n"
                f"  (design/review-security.md S-15).\n"
                f"\n"
                f"  Pass something that BUILDS one, so each thread gets its own:\n"
                f"\n"
                + _WORKING_SPELLINGS.format(
                    name=name, args=_as_call(_required_args(type(p)))) + "\n"
                "\n"
                + bare
                + "\n"
                "  -> docs/06-safety.md#4-least-privilege"
            )
        try:
            made = p()
        except TypeError as exc:
            needs = _required_args(p)
            raise ConfigError(
                f"build_agent() got a policy factory that cannot be called with no "
                f"arguments: {name}.\n"
                f"\n"
                f"  Every factory here is called once per conversation, with no\n"
                f"  arguments"
                + (f", and {name} still needs: {', '.join(needs)}.\n" if needs else ".\n")
                + "  Bake the arguments in:\n"
                "\n"
                + _WORKING_SPELLINGS.format(name=name, args=_as_call(needs)) + "\n"
                f"\n"
                f"  Python said: {exc}\n"
                f"\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            ) from exc
        except Exception as exc:                       # a factory that raises for its own reasons
            raise ConfigError(
                f"build_agent() called the policy factory {name} once, to check it, and "
                f"it raised {type(exc).__name__}: {exc}\n"
                f"\n"
                f"  Every factory is called once per conversation, so this would have\n"
                f"  failed on the first tool call of every run. It is refused here\n"
                f"  instead — a configuration error belongs at construction.\n"
                f"\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            ) from exc
        if not callable(getattr(made, "check", None)):
            raise ConfigError(
                f"build_agent() called the policy factory {name} and got back "
                f"{type(made).__name__}, which has no check() method.\n"
                f"\n"
                f"  A policy is anything with check(call, ctx) -> Ruling\n"
                f"  (harness.Policy). Without it the engine has nothing to ask.\n"
                f"\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            )


def _lc_tool(spec) -> dict:
    """ToolSpec → the provider-agnostic tool schema LangChain binds."""
    return {"name": spec.name, "description": spec.description,
            "parameters": dict(spec.input_schema)}



