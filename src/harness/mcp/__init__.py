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

import hashlib
import json
import warnings
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
    #: G-13, design/review-architect.md — the K-12-compatible version of the
    #: `fingerprint` this class's own docstring says v1 deferred (deferred because
    #: nothing PINS one endpoint over its lifetime; this is narrower: pinning one
    #: TOOL's *shape* at the moment it was reviewed, not the server's identity). Keyed
    #: by the tool's PROTOCOL name, value a `tool_fingerprint()` hash the operator
    #: computed once, offline, after actually reading that tool's description,
    #: inputSchema and annotations. `None` (the default) changes nothing — `trusted`
    #: still governs hint-based classification exactly as before this field existed.
    #: Once set, it closes design/03 §5.4's remaining gap: classification is "chốt tại
    #: thời điểm bind" per RUN, but nothing stopped the SERVER from rug-pulling a tool's
    #: description or schema between two different runs, each of which re-classifies
    #: correctly against whatever `tools/list` says *at that bind*. A tool whose live
    #: shape no longer matches its pinned hash — or that was never reviewed at all —
    #: is treated as `trusted=False`: it falls back to `default_effect`, never to a
    #: hint the (possibly rug-pulled) server itself supplied. `effects` (M-1's explicit
    #: per-tool override) is untouched by this — an operator's own stated classification
    #: outranks a hint either way, pinned or not.
    reviewed_tools: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        # H-6, design/review-architect-round2.md (confirmed still open, round3): the
        # S-9/G-13 rug-pull window this field exists to close is exactly as open as
        # before the field existed whenever an operator leaves it at its default. That
        # default is easy to reach by doing nothing extra -- same shape as G-9's
        # allowed_hosts=None warning -- so warn loudly rather than let a `trusted=True`
        # policy look pinned when it silently isn't.
        if self.trusted and self.reviewed_tools is None:
            warnings.warn(
                f"McpServerPolicy(identity={self.identity!r}, trusted=True) has no "
                f"reviewed_tools -- every tool this server currently claims via its "
                f"annotations is classified from those hints, UNPINNED. A server that "
                f"changes its mind later (rug-pulls a tool's description, inputSchema, "
                f"or hints between two runs) is re-classified from whatever it claims "
                f"*at that later bind*, with nothing to catch the difference. This is "
                f"deliberate and supported (`reviewed_tools=None` is the default), but "
                f"easy to reach by accident on a policy an operator meant to pin. If "
                f"this is intentional, this warning is the only cost; if it isn't, set "
                f"reviewed_tools={{tool_name: tool_fingerprint(...), ...}} after "
                f"reviewing each tool once, offline.\n\n"
                f"  -> design/03-tools-and-mcp.md §5.4 / "
                f"design/review-architect-round2.md H-6",
                UserWarning, stacklevel=2)


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


def tool_fingerprint(tool: Any) -> str:
    """A canonical hash of everything about a tool's SHAPE that classification reads —
    `name`, `description`, `inputSchema`, `annotations` — G-13, design/review-architect.md.
    Deterministic given equal content, independent of dict/attribute iteration order.

    An operator calls this once, offline, on a tool they actually reviewed, and pins the
    result into `McpServerPolicy.reviewed_tools`. Never imports `mcp.types` (ADR-032):
    reads only the same duck-typed attributes `classify_mcp_tool`/`_effect_from_hints`
    already rely on, so it works on the real SDK's `mcp.types.Tool` and on a bare stub
    alike (`tests/test_m9_t91_mcp.py`'s `_tool()` fixture, among others).
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        ann_repr: Any = None
    elif hasattr(ann, "model_dump"):
        ann_repr = ann.model_dump(mode="json", exclude_none=True)
    else:
        ann_repr = {k: getattr(ann, k, None) for k in
                   ("title", "readOnlyHint", "destructiveHint",
                    "idempotentHint", "openWorldHint")}
    material = json.dumps(
        {"name": tool.name, "description": tool.description,
         "inputSchema": dict(tool.inputSchema), "annotations": ann_repr},
        sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()


def _effect_for(tool: Any, policy: McpServerPolicy) -> Effect:
    """M-1..M-2: policy override > (trusted AND (unpinned OR verified) ? hint : default)
    > default. The middle clause is G-13: once `reviewed_tools` is set, a hint is only
    trusted for a tool that was BOTH reviewed (`tool.name` is a key in it) AND has not
    changed shape since (`tool_fingerprint(tool)` still matches the pinned hash) —
    anything else, including a tool the operator never reviewed at all, is treated
    exactly like `trusted=False`."""
    if tool.name in policy.effects:
        return policy.effects[tool.name]
    if not policy.trusted:
        return policy.default_effect
    if policy.reviewed_tools is not None:
        pinned = policy.reviewed_tools.get(tool.name)
        if pinned is None or pinned != tool_fingerprint(tool):
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
    are not guaranteed to match this harness's `^[a-z][a-z0-9_]{0,63}$`.

    G-12, design/review-architect.md: `slug()` collapses repeated `_`, so two DIFFERENT
    `(server, tool)` pairs used to be able to land on the same slugged name (`"a"` +
    `"b_c"` and `"a_b"` + `"c"` both slugged to `a_b_c`) — and `tool.name` is chosen by
    the SERVER, not the caller, so this was reachable by a hostile or just carelessly
    named MCP server, not only "rare in practice." `ToolSet`'s duplicate-name guard
    catches it (raises `DuplicateToolError` rather than silently merging), but that
    turns an untrusted server's naming choice into a denial of service against every
    OTHER server's tool it happens to collide with — collision-resistance belongs here,
    not one guard downstream. A short `blake2b` digest of the pair, hashed with the same
    `\x00` domain separator `idempotency.py`'s `call_key` uses for this exact reason
    (S-23: joining two strings with a plain separator that could itself appear in either
    string reintroduces the same collision one level up) is appended after slugging, so
    the slug's own information loss can no longer cause two different pairs to agree.
    """
    digest = hashlib.blake2b(f"{policy.identity}\x00{tool.name}".encode("utf-8"),
                             digest_size=4).hexdigest()
    # Truncate the slugged base ourselves, to a length that leaves room for the
    # digest — `slug()`'s own `[:64]` truncates from the END and would otherwise be
    # free to cut the digest itself off for a long identity/tool.name pair, defeating
    # the whole point.
    base = slug(f"{policy.identity}__{tool.name}")[:55]
    name = f"{base}_{digest}"[:64]
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
