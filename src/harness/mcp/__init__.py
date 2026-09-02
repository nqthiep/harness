"""MCP client — a harness EMBEDS third-party MCP servers as tools; it never hosts one.

T-9.1, docs/17-research-alignment.md M9. Design lives at
`design/03-tools-and-mcp.md §5` — read before touching this file; the four rules below
(M-1..M-4) are that section's numbering, kept as-is because the code IS the citation.

This module is the real caller `design/07-risks-and-open-issues.md` (S-7, S-8, S-9,
S-10, S-17) said MCP classification was waiting for — none of those five could be fixed
before `ToolSpec` had a `server` field and something actually called `tools/list`.
Landing here, together, is the fix: not five separate patches.

Extra, not a dependency (`pyproject.toml`'s `mcp` group) — `import harness` must never
import the `mcp` SDK, same rule as `graph`/`viking`/`otel` (ADR-032).
"""
from __future__ import annotations

from dataclasses import field
from typing import Any, Awaitable, Callable, Mapping, NewType

from .._value import value
from ..errors import HarnessError
from ..tools import Effect, ToolSpec, slug

ServerLabel = NewType("ServerLabel", str)

#: Called with (protocol tool name, arguments) -> the tool's text result. `connect()`
#: supplies one bound to a real `mcp.ClientSession.call_tool`; `classify_mcp_tool` only
#: wires it through, so tests can pass a stub and check classification without a session.
McpCall = Callable[[str, Mapping[str, Any]], Awaitable[str]]


class McpToolError(HarnessError):
    """The server's own tool call reported `isError` — not a transport failure."""


@value
class McpServerPolicy:
    """Operator-held, keyed to a server LABEL — design/03 §5.2.

    v1 has no `fingerprint`: a first draft carried `ServerIdentity{label, fingerprint}`,
    and a reviewer found `fingerprint`'s format never settled (no SPKI equivalent for
    MCP stdio) — a field whose shape isn't settled has no business in a public type
    (K-12, `07-risks-and-open-issues.md`). `identity` alone is the confused-deputy
    defense the research actually found in the wild (Microsoft `server_label`); it does
    NOT stop a label being re-pointed at a different endpoint later — that needs
    `ServerIdentity`+`fingerprint`, deferred until a real re-pointing is observed.
    """
    identity: ServerLabel
    #: Whether `tools/list` annotations may set the DEFAULT classification (M-2). An
    #: override in `effects` always wins regardless of this flag — trust only changes
    #: what happens when there is no override.
    trusted: bool = False
    #: Used when `trusted` is False, or the server sent no hint at all (M-1).
    default_effect: Effect = Effect.DANGER
    #: Operator override, keyed by the tool's PROTOCOL name (before this module's
    #: server-prefixing). Always wins over both `trusted` and any hint (M-1, M-2).
    effects: Mapping[str, Effect] = field(default_factory=dict)
    #: NEVER from a hint — M-3. A malicious server can claim `readOnlyHint=true` on a
    #: tool that in fact exfiltrates; only the operator, offline, decides a tool may run
    #: against a tainted context. Empty means no tool on this server may.
    accepts_tainted: frozenset[str] = frozenset()
    #: `None` = every tool the server publishes. A non-`None` set is an allowlist: any
    #: `tools/list` entry not in it is silently skipped by `connect()`, never classified.
    allow: frozenset[str] | None = None


def _sdk_attr(obj: Any, snake: str, camel: str) -> Any:
    """The MCP Python SDK's Pydantic attribute names moved from the wire's camelCase (the
    SDK this module was originally written against) to snake_case, with camelCase kept
    only as the JSON alias, as of SDK 2.1.1 — `Tool.inputSchema`, `ToolAnnotations.
    readOnlyHint`/`destructiveHint`/`openWorldHint`, `CallToolResult.isError` among them.
    `pyproject.toml` pins `mcp>=1.9` with no upper bound, so read whichever name the
    installed SDK actually exposes rather than assuming one.
    """
    if hasattr(obj, snake):
        return getattr(obj, snake)
    return getattr(obj, camel, None)


def _effect_from_hints(ann: Any) -> Effect:
    """Fail-closed exactly like Microsoft's own `_map_mcp_annotations_to_labels`
    (design/03 §5.3 M-2) — chép lại nguyên cả chỗ dễ sai:

    `readOnlyHint is True` — không phải "khác False". Real servers (GitHub's MCP among
    them) set `readOnlyHint=True` on read tools but leave it UNSET, not `False`, on
    write tools; a strict `is False` check on the other branches would misclassify
    those as read. Every value other than exactly `True` — `False` *or missing* — falls
    through toward `DANGER`, the same polarity `mcp.types.ToolAnnotations` itself
    documents: hints are optional metadata that fails closed on every axis.

    One place this goes further than Microsoft: `readOnlyHint=True` with
    `openWorldHint` not `False` classifies as `external`, not `read` — a read-only tool
    that reaches the open world (a search, a fetch) is a prompt-injection SOURCE even
    though it cannot exfiltrate; `EFFECT_PROFILES[EXTERNAL].emits` already taints for
    exactly this reason (design/03 §5.3 M-2).
    """
    if ann is None:
        return Effect.DANGER
    if _sdk_attr(ann, "read_only_hint", "readOnlyHint") is True:
        open_world = _sdk_attr(ann, "open_world_hint", "openWorldHint")
        return Effect.READ if open_world is False else Effect.EXTERNAL
    if _sdk_attr(ann, "destructive_hint", "destructiveHint") is False:
        return Effect.WRITE
    return Effect.DANGER


def _effect_for(tool: Any, policy: McpServerPolicy) -> Effect:
    """M-1..M-2: policy override > (trusted ? hint : default) > default."""
    if tool.name in policy.effects:
        return policy.effects[tool.name]
    if not policy.trusted:
        return policy.default_effect
    return _effect_from_hints(getattr(tool, "annotations", None))


def _input_schema(tool: Any) -> Mapping[str, Any]:
    """`mcp.types.Tool`'s input-schema field is `inputSchema` on the wire (MCP is JSON-RPC,
    camelCase) — see `_sdk_attr` above for why the Python attribute name can't be assumed.
    """
    return _sdk_attr(tool, "input_schema", "inputSchema")


def classify_mcp_tool(tool: Any, policy: McpServerPolicy, call: McpCall) -> ToolSpec:
    """Build the `ToolSpec` for one `mcp.types.Tool` entry — pure given `call`; never
    touches the network itself.  `call` is a closure `connect()` binds to the real
    session so this function stays testable with a stub.

    Name is prefixed `f"{policy.identity}__{tool.name}"`: two servers publishing a tool
    of the same protocol name (`search`, say) get two DISTINCT `ToolSpec.name`s, so
    `tools.registry.ToolSet`'s duplicate-name guard can never silently merge them, and
    the model sees which server each is on. `slug()` (same helper `as_tool()` uses) then
    makes the result a valid tool name regardless of what the server sent — MCP names
    are not guaranteed to match this harness's `^[a-z][a-z0-9_]{0,63}$`. `slug()`
    collapses repeated `_`, so two DIFFERENT `(server, tool)` pairs can in theory land on
    the same slugged name (`"a"`+`"b_c"` and `"a_b"`+`"c"` both become `a_b_c`) — rare in
    practice, and when it happens `ToolSet`'s own duplicate-name guard raises loudly
    rather than silently merging the two, same as any other tool-naming collision.
    """
    name = slug(f"{policy.identity}__{tool.name}")
    description = tool.description or tool.name

    async def _fn(**kwargs: Any) -> str:
        return await call(tool.name, kwargs)          # protocol name, not the prefixed one

    return ToolSpec(
        name=name, description=description, input_schema=dict(_input_schema(tool)),
        effect=_effect_for(tool, policy), fn=_fn, source=f"mcp:{policy.identity}",
        server=str(policy.identity),
    )


def _unwrap(result: Any) -> str:
    """`mcp.types.CallToolResult` -> the text a harness tool result must be
    (`Dispatcher._invoke`, `dispatch.py`, only accepts `str` or something JSON-encodable).

    `isError` is the protocol's own way to report a FAILED call that still round-tripped
    normally (`docs/04-interfaces.md §7`'s taxonomy) — raising here, rather than
    returning the error text as if it were a result, lets it flow into the same
    `classify()`/retry machinery any other tool exception does.
    """
    if _sdk_attr(result, "is_error", "isError") is True:
        raise McpToolError(_text(result))
    return _text(result)


def _text(result: Any) -> str:
    parts = [b.text for b in getattr(result, "content", ()) if getattr(b, "type", None) == "text"]
    return "\n".join(parts)


async def connect(session: Any, policy: McpServerPolicy) -> list[ToolSpec]:
    """`harness.mcp.connect(session, policy) -> list[ToolSpec]` — the tool boundary.

    `session` is an already-connected `mcp.ClientSession` (stdio, SSE, streamable-HTTP —
    this module does not own transport lifecycle, same seam shape as `Sandbox`: the
    harness defines what it needs, the caller supplies a live connection). Calls
    `tools/list` exactly ONCE and returns a plain list — no live re-listing, no
    background refresh.

    That is deliberate, not an oversight: design/03 §5.4 requires a rug-pull (`tools/list`
    answering differently between two calls) never silently reclassify a tool a run is
    already using — "phân loại được chốt tại thời điểm bind". The simplest mechanism that
    is actually TRUE rather than merely documented is to never call `tools/list` again on
    this module's own initiative; call `connect()` again for a fresh snapshot (a new run,
    an operator-triggered refresh), and it produces entirely new `ToolSpec`s — a stale one
    is never mutated in place, because `ToolSpec` is frozen (`@value`).
    """
    listed = await session.list_tools()
    out: list[ToolSpec] = []
    for t in listed.tools:
        if policy.allow is not None and t.name not in policy.allow:
            continue
        out.append(classify_mcp_tool(t, policy, lambda n, a: _call(session, n, a)))
    return out


async def _call(session: Any, name: str, arguments: Mapping[str, Any]) -> str:
    return _unwrap(await session.call_tool(name, dict(arguments)))
