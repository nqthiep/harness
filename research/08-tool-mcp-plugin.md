# Tool API, MCP Integration, and Plugin Architecture

Sections §8, §9, §24 of the research brief.

**Data collected on: 2026-08-30.** All claims from package source of the
versions named. Method as elsewhere: measure, then verify every standout by
reading code.

This section closes the last gap in the study. It also records a **third
instance of the same measurement failure**, which by now is a finding about the
method rather than about any one package.

---

## §8.1 The measurement, and the third name collision

`probe3.py` over 23 Python packages, hits per kLOC, license headers already
excluded:

| package | kLOC | tooldef | schema | toolerr | parallel | plugin | abc | mcp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pydantic-ai-slim 2.36.0 | 119 | 2.4 | ~~34.8~~ | **1.9** | 1.6 | ~~37.0~~ | 5.7 | 6.7 |
| langchain 1.3.18 | 15 | **5.7** | 3.7 | 1.6 | 1.2 | **48.9** | 3.2 | 0.0 |
| agent-framework-core 1.16.0 | 61 | 2.6 | 1.3 | 0.6 | 1.0 | **27.3** | 4.4 | 13.1 |
| crewai 1.15.18 | 117 | 2.5 | 7.2 | 0.5 | 1.3 | 18.5 | 4.5 | 5.9 |
| mcp 2.1.1 | 27 | 0.2 | 7.2 | 0.1 | 0.3 | 15.9 | 13.0 | ~~46.5~~ |
| semantic-kernel 1.44.1 | 81 | 0.0 | 3.3 | 1.2 | 2.2 | 11.3 | 6.8 | 3.8 |
| dspy 3.3.1 | 34 | 0.6 | 7.3 | 0.2 | 3.3 | 10.8 | 1.7 | 0.7 |
| haystack-ai 3.1.0 | 54 | 0.1 | 2.2 | 0.3 | 1.6 | 10.3 | 3.1 | 0.4 |
| openai-agents 0.22.0 | 125 | 2.3 | 1.7 | 1.4 | 1.5 | 8.3 | 4.3 | 7.6 |
| letta-client 1.12.1 | 47 | 0.4 | 7.4 | 0.2 | **6.0** | 8.4 | 0.9 | 7.5 |
| langchain-core 1.6.1 | 70 | 1.2 | **8.8** | 0.6 | 1.5 | 3.6 | 5.7 | 0.8 |
| google-adk 2.8.0 | 167 | 2.6 | 4.7 | 1.2 | 1.5 | 6.0 | 3.2 | 3.2 |
| autogen-core 0.7.5 | 9 | 3.8 | 6.4 | 1.4 | 0.3 | 3.8 | **13.5** | 0.0 |
| langgraph 1.2.11 | 27 | 0.1 | 3.7 | 0.1 | 3.4 | 1.4 | 8.2 | 0.0 |

Struck-through cells failed verification:

- **pydantic-ai's 34.8 "schema" is its own package name.** Of the matches,
  2729 are `pydantic_ai` and 786 are `pydantic`; the real schema vocabulary is
  136 `json_schema` plus 72 `pydantic_core`. Corrected: **~1.7/kLOC**.
- **pydantic-ai's 37.0 "plugin" is `provider`** — 1738 `provider`, 704
  `provider_name`, 574 `provider_details`. That is *model provider* (OpenAI,
  Anthropic, Google), not an extension architecture. The real plugin signal is
  229 `hooks`: **1.9/kLOC**.
- **mcp's 46.5 "mcp" is the package's own name.** Trivially true, measures
  nothing.

### The method finding

This is the third distinct way the probe has produced a wrong headline in this
study:

| # | failure mode | example | overstatement |
|---|---|---|---|
| 1 | **package name matches probe** | crewai `orchestr` (2401 × `crewai`) | ~2× |
| 2 | **license header matches probe** | spring-ai `permission` (292 of 294) | 390× |
| 3 | **adjacent domain word matches probe** | pydantic-ai `plugin` (`provider` ≠ plugin) | 19× |

All three are the *same* underlying error: **a regex measures a string, and a
string is not a concept.** The safeguard that caught all three is the one rule
this study has applied without exception — *no standout number becomes a
finding until someone reads the code behind it*. Four numbers in §9, one in
§10, three here: eight rejected out of roughly sixty examined.

The honest way to read every density table in this study is as a **search
index** — a way to decide what to read next — never as a result.

---

## §8.2 Tool error handling: the sharpest design difference found

This is where frameworks diverge most, and the difference has direct
consequences for a long-running agent.

### pydantic-ai has the only three-way taxonomy

`pydantic_ai/exceptions.py`:

```python
class ModelRetry(Exception):
    """Exception to raise to request a model retry.

    Can be raised from tool functions, output validators, and capability hooks
    ... to send a retry prompt back to the model asking it to try again.

    For a terminal failure the model should see but not retry, raise
    `ToolFailed` instead.
    """

class ToolFailed(Exception):
    """Exception to raise to report a terminal tool failure to the model.

    Raise this when a tool call is done and has failed — a missing resource, an
    unsupported operation, a definitive upstream error — and you want the model to
    see the failure and adapt rather than try the same call again.

    Like ModelRetry, this produces a failed tool result the model sees; unlike
    ModelRetry it does not prepend retry/correction instructions and **does not
    consume the tool's retry budget**.
    """
```

Three outcomes, cleanly separated:

| raise | model sees it | correction prompt | consumes retry budget | run continues |
|---|---|---|---|---|
| `ModelRetry` | yes | yes | **yes** | yes |
| `ToolFailed` | yes | no | **no** | yes |
| anything else | no | — | — | **no, run ends** |

The retry-budget distinction is the subtle part and it is right. A malformed
argument should burn the retry allowance — the model is guessing and must be
stopped from guessing forever. A definitive 404 should not: retrying cannot
help, the model needs to *adapt*, and charging it against a budget meant for
correction loops would end runs that are proceeding perfectly well. Repeated
`ToolFailed` is instead bounded at the run level by `UsageLimits`, which is the
correct scope for it.

**No other framework in the study makes this distinction.**

### LangChain's default is to kill the run

`langchain_core/tools/base.py:527`:

```python
handle_tool_error: (
    bool | str | Callable[[ToolException], ToolExceptionHandlerOutput] | None
) = False
"""If `False`, the exception is re-raised. If `True`, the exception message is
returned as tool output. ..."""
```

and the execution path at line 1118:

```python
except ToolException as e:
    if not self.handle_tool_error:
        error_to_raise = e
    else:
        content = _handle_tool_error(e, flag=self.handle_tool_error)
        status = "error"
except (Exception, KeyboardInterrupt) as e:
    error_to_raise = e

if error_to_raise:
    run_manager.on_tool_error(error_to_raise, tool_call_id=tool_call_id)
    raise error_to_raise
```

By default `handle_tool_error=False`, so **a tool that raises terminates the
agent run**. Any non-`ToolException` terminates it regardless of the setting.

This is a defensible default — a crash is louder than a silently degraded run,
and silent degradation is the worse failure for a system you cannot watch. But
it means one flaky HTTP call ends a fifty-step run that was otherwise fine, and
recovering requires the tool author to have opted in per tool. Catching
`KeyboardInterrupt` and re-raising it, rather than swallowing it, is correct and
worth crediting; a bare `except Exception` here would have made Ctrl-C
unreliable.

**The design lesson for a harness:** neither default is right for every tool,
which means the choice belongs to the tool's *classification*, not to a flag on
each tool. A `read` tool that fails is retryable and its failure is information;
an `external` tool that fails may have already had an effect and must not be
retried blindly. Deriving the error policy from the effect class gets
pydantic-ai's three-way behaviour without asking every tool author to choose
correctly.

---

## §8.3 Parallel tool execution: one framework treats it as a correctness problem

Every framework runs tool calls concurrently — `asyncio.gather` appears in all
of them. Only one asks whether that is *safe*.

**pydantic-ai** puts a barrier flag on the tool itself, `tools.py:583`:

```python
sequential: bool = False
"""Whether this tool acts as a barrier that runs alone, not overlapping with other tool calls.

A `sequential=True` tool acts as a barrier: it runs alone, with tools the model emitted before it
completing first and tools emitted after it starting only once it finishes. Other tools still run
in parallel around it.
"""
```

with three run-level modes (`_tool_execution.py:1112`): `'parallel'`,
`'sequential'`, and `'exhaustive'` — "run every tool in parallel, segmented only
by `sequential=True` barriers."

This is the right primitive. A tool that mutates shared state declares itself a
barrier, and the executor maintains ordering around it without serialising
everything.

Its `ToolKind` is similarly considered (`tools.py:594`):

- `'function'` — executed in-run, result returned to the model
- `'output'` — passes through an output value that ends the run
- `'external'` — "a tool whose result will be produced outside of the
  Pydantic AI agent run ... because it depends on an upstream service (or user)"

**Everyone else hand-writes the guard per tool.** The clearest example is
LangChain's todo middleware, `middleware/todo.py:289`:

```python
"""Check for parallel write_todos tool calls and return errors if detected."""
```

A bespoke check, for one tool, written because that particular tool broke. That
is what the absence of a general model costs: the guard exists only where
someone was bitten, and the twentieth tool has no guard at all.

> **Nothing in the study derives parallel-safety from a tool's declared
> effect.** pydantic-ai comes closest, and its flag is caller-asserted per tool
> rather than derived. A harness that classifies a tool once as
> `read`/`write`/`external`/`danger` gets parallel-safety, retryability, and the
> error policy of §8.2 from that single classification — none of which requires
> the tool author to reason about concurrency.

---

## §9 MCP: the protocol says its own annotations are not a security signal

`06-typescript.md` established that MCP's dense "permission" vocabulary is
entirely OAuth — it governs whether a client may *connect*, never whether a tool
call is *permitted*. The remaining question was whether MCP's tool annotations
(`readOnlyHint`, `destructiveHint`) could serve as the missing signal.

They cannot, and the protocol says so. From `mcp_types` 2.1.1 — the wire-type
package MCP 2.x split out of the SDK — `ToolAnnotations`:

```python
class ToolAnnotations(WireModel):
    """
    Additional properties describing a Tool to clients.

    NOTE: all properties in ToolAnnotations are **hints**.
    They are not guaranteed to provide a faithful description of
    tool behavior (including descriptive properties like `title`).

    Clients should never make tool use decisions based on ToolAnnotations
    received from untrusted servers.
    """
    destructive_hint: bool | None = None   # Default: true
    idempotent_hint:  bool | None = None   # Default: false
    open_world_hint:  bool | None = None   # Default: true
    read_only_hint:   bool | None = None
```

Two things follow.

**First, the defaults are correct and worth copying.** Absent annotations mean
*destructive*, *open-world*, *non-idempotent* — the spec fails closed on every
axis. A server that says nothing is treated as dangerous. That is the right
polarity, and it is the opposite of how most optional metadata is designed.

**Second, an annotation is an unverified claim by the party being governed.**
The server describes its own tool. A malicious or compromised server sets
`readOnlyHint=true` on an exfiltration tool, and any client that treats the hint
as authorisation has handed over the decision. The spec's warning is explicit.

### Who reads them anyway

Only three packages read the hints at all: **agent-framework-core** (15 hits),
**agno** (14), **browser-use** (5). Twenty of twenty-three ignore them.

Microsoft's `_map_mcp_annotations_to_labels` (`security.py:3018`) is the most
careful use, and it is genuinely well engineered:

```python
def _map_mcp_annotations_to_labels(
    annotations: Any | None,
    *,
    default_integrity: IntegrityLabel = IntegrityLabel.UNTRUSTED,
) -> tuple[IntegrityLabel, ConfidentialityLabel | None, bool]:
```

- No annotations at all → `(UNTRUSTED, PUBLIC, False)`. Fail closed.
- `readOnlyHint` **anything other than `True`** — false *or missing* — is
  treated as a write sink. The docstring explains why, from field experience:
  "real-world servers (e.g. GitHub's MCP) declare `readOnlyHint=True` on read
  tools but leave the field unset on write tools, so a strict
  `readOnlyHint=False` check would miss them."
- `openWorldHint=True` → `UNTRUSTED` integrity.

That comment is the mark of code tested against reality rather than against the
spec. The conservative-on-absence policy is right.

**The residual gap is deception, not absence.** `readOnlyHint=True` →
`accepts_untrusted=True`, meaning the tool may run in a tainted context. The
mapping is conservative about a server that *stays silent* and trusting of a
server that *speaks*. It takes no server-trust parameter — only
`default_integrity` — even though the same codebase has a `server_label` trust
boundary in `_tool_approval.py` (see `09-…` §14). Connecting those two would
close it: honour hints from servers on an operator-maintained trust list, ignore
them everywhere else.

> **Conclusion for §9.** MCP standardises *discovery and transport*, and does it
> well. It deliberately does not standardise authorisation, and its own types
> tell you not to pretend otherwise. **A harness must classify third-party tools
> itself** — from an operator-controlled policy keyed to the server's identity,
> with the MCP hints used at most as a default for servers already trusted, and
> never as the decision.

---

## §24 Plugin architecture: LangChain 1.x has the best extension model

Corrected for the `provider` collision, the real plugin density is:

| package | mechanism | hits | per kLOC |
|---|---|---:|---:|
| **langchain 1.3.18** | `middleware` | 374 | **24.3** |
| **agent-framework-core 1.16.0** | `middleware` | 648 | **10.6** |
| pydantic-ai-slim 2.36.0 | `hooks` | 229 | 1.9 |

LangChain 1.x's `AgentMiddleware` (`langchain/agents/middleware/types.py`)
exposes six extension points:

| hook | shape | can it short-circuit? |
|---|---|---|
| `before_agent(state, runtime)` | observe / patch state | returns a state update |
| `before_model(state, runtime)` | observe / patch state | returns a state update |
| `wrap_model_call(request, handler)` | **around** | **yes** — may replace, retry, or skip the call |
| `after_model(state, runtime)` | observe / patch state | returns a state update |
| `wrap_tool_call(request, handler)` | **around** | **yes** |
| `after_agent(state, runtime)` | observe / patch state | returns a state update |

The `wrap_*` pair is what makes this the strongest design in the study. A
before/after hook can only observe and mutate state; an *around* hook receives
the `handler` and decides whether to call it, call it differently, call it
twice, or not at all. Retry policies, caching, approval gates, fallback models,
and budget enforcement are all expressible as `wrap_model_call` — without the
framework needing a dedicated feature for any of them. That is the Open/Closed
principle actually achieved: LangChain does not need to ship a budget feature,
because a user can write one.

Microsoft's `AgentMiddleware` / `FunctionMiddleware` is the same shape at half
the density, and `ToolApprovalMiddleware` (`09-…` §14) is a good demonstration —
approval implemented *as* a plugin rather than as a core feature.

### The caveat that matters for a harness

**An extension point is also an attack surface, and a bypass.**
`09-memory-context-multiagent-hitl.md` §16bis showed the cost concretely: the
Microsoft security lattice is middleware, and because it is middleware, *not
installing it* is the default — nothing under `_harness/` imports it. An
approval gate implemented as a plugin protects only the agents someone
remembered to wrap.

The two properties are in direct tension, and the resolution is not to choose
one:

- **Plugins are right for policy** — retries, caching, logging, model fallback,
  cost accounting. Things that vary per deployment and whose absence degrades
  quality, not safety.
- **Plugins are wrong for invariants** — budget ceilings, taint propagation,
  the permission check itself. Things whose absence is a security failure.

An invariant belongs in the path that cannot be composed away, with the plugin
system layered above it. `unguarded_paths()` in this repository — a reachability
proof over the compiled graph that no path reaches `model` without `budget` or
`tools` without `policy` — exists precisely because "we shipped the middleware
but the harness didn't install it" is a real, observed failure mode in a
production framework from a major vendor, not a hypothetical.

---

## §45 Evidence limits

- Tool-definition ergonomics (§8's decorator-vs-class question) are covered from
  real signatures in `02-api-comparison.md` §6 and not repeated.
- **`mcp-types` 2.1.1 is the Python wire-type package.** The MCP specification
  itself is the authority on protocol semantics; this section quotes the Python
  types because §45 ranks source above prose docs, and the two agree here. The
  TypeScript SDK was measured separately in `06-typescript.md`.
- The plugin comparison covers Python only. Vercel's AI SDK middleware was not
  measured for this section: **Chưa đủ evidence**.
- `langchain` 1.3.18 is 15 kLOC — a small package where a single subsystem
  dominates any density figure. Its 24.3 means "middleware is most of what this
  package is", which is true, and not "more middleware than Microsoft has".
  Absolute counts (374 vs 648) tell the other half.
- Claims about default behaviour (LangChain terminating on tool error) are read
  from the default field value and the execution path, not observed at runtime.

---

## Sources

| package | version | repo |
|---|---|---|
| pydantic-ai-slim | 2.36.0 | https://github.com/pydantic/pydantic-ai |
| langchain, langchain-core | 1.3.18, 1.6.1 | https://github.com/langchain-ai/langchain |
| agent-framework-core | 1.16.0 | https://github.com/microsoft/agent-framework |
| mcp, mcp-types | 2.1.1 | https://github.com/modelcontextprotocol/python-sdk |
| openai-agents | 0.22.0 | https://github.com/openai/openai-agents-python |

Remaining package URLs: `09-memory-context-multiagent-hitl.md` §Sources.
