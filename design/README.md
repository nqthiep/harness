# Harness Agent Design

This design is a **direct consequence** of the research in [`research/`](../research/):
30 packages, 3 ecosystems, read from source rather than docs, and 8 headline numbers that
fell apart once the code behind them was actually read.

Editorial rule: **every design decision must cite a specific finding.**
A decision that cites nothing is an opinion, and an opinion doesn't belong here.

> **This is a design draft, written BEFORE any code existed.** The living documentation,
> matching the real code today, lives in [`../docs/`](../docs/) — start at
> [`../README.md`](../README.md). This directory is kept because it is cited directly in
> hundreds of places (code comments, `docs/*.md`, `07`/`08` below) as the original
> evidence for one specific decision — deleting it would break every one of those
> citations. `00`–`06` are the original design (most of it still matches the real code;
> where a name or detail changed during implementation, `07` records exactly where);
> `07`/`08` are the LIVE risk log + roadmap, updated throughout the build; the two
> `review-*` files are two independent adversarial review rounds, the source of most of
> the findings `07` addresses.

---

## Reading order

| file | contents | status |
|---|---|---|
| [00 — Foundation](00-foundation.md) | **Read this first.** Vocabulary, the `Effect` class, the two lattices, the `Decision` record, the four architecture rules | Original design |
| [01 — Core API](01-core-api.md) | The public API, Zero-to-Agent, DX, the plugin API | Original design |
| [02 — Safety Engine](02-safety-engine.md) | `PolicyEngine`, the `Decision` lifecycle, the audit sink, taint enforcement | Original design |
| [03 — Tools & MCP](03-tools-and-mcp.md) | `ToolSpec`, the error taxonomy, the parallel barrier, idempotency, MCP classification | Original design |
| [04 — Runtime & Durability](04-runtime-durability.md) | The graph, checkpoints, `unguarded_paths()`, cancellation, subagents | Original design |
| [05 — Cost & Memory](05-cost-and-memory.md) | `Ledger`, `reserve/hold/release`, compaction, memory provenance | Original design |
| [06 — Poka-Yoke Matrix](06-poka-yoke-matrix.md) | Every failure class mapped to its blocking mechanism and Poka-Yoke tier | Original design |
| [07 — Risks & Open Issues](07-risks-and-open-issues.md) | Every review finding (the S-/K-/N- series): where it was fixed, why it was deferred, what's still open | **Live log** |
| [08 — Roadmap & Release Plan](08-roadmap-and-release-plan.md) | What's left, the order it runs in, release conditions — M6-M10 | **Live log, DONE** |
| [review-kiss.md](review-kiss.md) | Adversarial review round #1 — KISS/YAGNI, cutting excess | Independent review, addressed in `07` |
| [review-security.md](review-security.md) | Adversarial review round #2 — security attacks (S-1…S-29) | Independent review, addressed in `07` |

**What "original design" (00–06) means:** written before a single line of code existed,
and most of it still describes the real behavior accurately — `docs/*.md` is what
superseded it after implementation, and where the two disagree, **`docs/*.md` is right,
not these files.** `07` records every place an invariant code (`I-`, `P-`, `C-`, `R-`)
collided between these files and got renamed (K-13).

---

## Seven industry shortcomings this design fixes, and the evidence for each

This is why this design exists. Every row is a **measured** finding, not a hunch.

| # | industry shortcoming | evidence | fixed where |
|---|---|---|---|
| 1 | **Approval is a permission state, not an auditable decision** | 30 packages, 3 languages, no exception. openai-agents's `_ApprovalRecord` is `bool \| list[str]` with a permanent `always_approve`; Java's `ToolConfirmation` is a single boolean; the thickest audit signal found in Python is a tool **the model calls to log about itself** | [00 S4](00-foundation.md) · [02](02-safety-engine.md) -> `policy/decision.py::Decision` |
| 2 | **The best safety architecture exists but isn't turned on** | The only two-directional IFC lattice found anywhere lives in `agent_framework.security`; no file in `_harness/` imports it, and it's absent from the top-level `__init__` | [00 S5 R-1](00-foundation.md) · [04](04-runtime-durability.md) -> `policy/label.py::Label` |
| 3 | **Shared state leaks across concurrent runs** | `threading.local()` set across an `await` inside an async module — two tool calls silently read each other's slot, and **fail open**. Plus two module-level singletons | [00 S5 R-4](00-foundation.md) · [04](04-runtime-durability.md) -> one `Ledger`/`TaintTracker`/`EventBus`/`DecisionLog` per run |
| 4 | **Nobody caps MONEY, only step counts** | No project reserves budget before calling the model; `max_turns` bounds steps, not spend | [05](05-cost-and-memory.md) -> `budget/ledger.py::Ledger.reserve` |
| 5 | **No idempotency at the tool-call level** | Nobody has it; agno only protects *run submission* | [03](03-tools-and-mcp.md) -> `idempotency.py::execute_once` (T-6.1, built, NOT yet wired into dispatch — N-8) |
| 6 | **No memory write records provenance** | A "fact" pulled from a hostile web page gets stored exactly like something the user typed -> prompt injection survives across sessions | [05](05-cost-and-memory.md) -> `Store.put(..., provenance=)` |
| 7 | **The model holds its own safety switch** | `mode_set` has `approval_mode="never_require"`; the only thing keeping a model from leaving "plan" mode is an English sentence telling it to | [00 S5 R-3](00-foundation.md) · [02](02-safety-engine.md) -> `effect` has no default, `@tool(effect=...)` is required |

---

## Eight strengths this design borrows, and from whom

This design doesn't reinvent anything. It brings together what individual projects got right.

| strength | from | used where |
|---|---|---|
| Approval keyed on **parameter values** + an MCP server boundary, serialized across resume | Microsoft's `ToolApprovalRule` | [00 S4.1](00-foundation.md) · `policy/decision.py::Scope` |
| A **two-directional** information lattice (integrity x confidentiality) + a quarantine model | Microsoft's `security.py` | [00 S3.2](00-foundation.md) · [02](02-safety-engine.md) |
| A **three-branch** tool error taxonomy, distinguishing which kind spends retry budget | pydantic-ai's `ModelRetry`/`ToolFailed` | [03](03-tools-and-mcp.md) |
| A **barrier** for tools that cannot run in parallel | pydantic-ai's `sequential=True` | [03](03-tools-and-mcp.md) |
| **Graph -> durability**: checkpoints at 51.6/kLOC, reproduced even in TypeScript | LangGraph | [04](04-runtime-durability.md) · `src/harness/lg/` |
| An **around-hook** (`wrap_model_call`/`wrap_tool_call`) that receives a `handler` | LangChain 1.x middleware | [01](01-core-api.md) |
| Turn budget that does **not** reset across a handoff | openai-agents | [04](04-runtime-durability.md) · [05](05-cost-and-memory.md) |
| `tool_call`/`tool_result` pairing that's **enforced**, not just documented | Microsoft's `_unambiguous_function_call_result_pairs` | [05](05-cost-and-memory.md) |
| A fail-closed default when annotations are missing | MCP's `ToolAnnotations` | [03](03-tools-and-mcp.md) · `harness/mcp/` |
| An explicit `CancellationToken` threaded through every API boundary | autogen (26.9/kLOC — the highest in the research) | [04](04-runtime-durability.md) |
| Idempotency that handles races by re-reading, **without swallowing** `IntegrityError` | agno | [03](03-tools-and-mcp.md) |

---

## The central idea

Three sentences, and the whole design is a consequence of them.

**1. Classify once, derive five behaviors.**
A tool author declares exactly one thing — `effect` in `read`/`write`/`external`/`danger`.
Parallel safety, retryability, whether it taints context, the default verdict, and the
audit level are all *derived*. The research shows the cost of not having this model:
LangChain had to hand-write a dedicated guard for `write_todos` because that one tool was
broken — the guard exists only where someone got bitten, and the twentieth tool has no
guard at all.

**2. Invariants sit on a path nothing can bypass; plugins are only for policy.**
Because "middleware shipped but the harness doesn't install it" is a **real, observed**
failure at a major vendor, not a hypothesis. Budget, taint, and permission checks sit on
the mandatory path. Retry, cache, logging, and model fallback live in plugins.

**3. Provable, not just reviewable.**
23 review rounds found 20 bugs and **0 security bugs**. 16 *runtime* rounds found 38+ bugs
and **4 security bugs**. That's why `unguarded_paths()` is a reachability proof over the
compiled graph, and P-2 (a policy can only ever tighten) is a property-based test — not a
line in a review checklist. The same principle applies again, exactly as stated, across
the growth roadmap M6-M10: every milestone closes with a real test, and no fewer than
three real bugs (N-2, N-4, N-9) surfaced only while *writing* the test/doc, never while
reviewing prose.
