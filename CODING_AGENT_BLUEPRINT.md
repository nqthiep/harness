# Blueprint: a long-running coding agent on top of `harness`

This is a design, not a product — the library gives you the primitives named below; it
does not ship a coding agent. Everything here is backed by
[`examples/coding_agent.py`](examples/coding_agent.py), which runs end to end (no API key
needed — it uses a scripted fake model, and *every* side effect it claims — file writes,
git commits, a rejected agent, a rejected file path — actually happens; run it and check
`git log` in the printed workspace path yourself). Where this document names a real
limitation of the library, it says so rather than working around it silently.

```
python3 examples/coding_agent.py
```

---

## 1. What "long-running" actually means here — three separate mechanisms

Frameworks that don't distinguish these end up either capped at "one short exchange" or
promising a durability guarantee they don't have. This library gives you three real,
separately-tunable knobs — use the one that matches what's actually failing.

### a) One session, many tool calls — raise the budget, not the API surface

A coding task is not "one user message → one tool call → one answer." It's "edit → run
tests → read the failure → edit again → …", all inside what the harness considers a
*single* run. The default budget (`$0.50, 20 steps, 5 min`) is sized for a question, not a
task:

```python
budget="$5, 300 steps, 45m"
```

One `agent.run(...)` / `graph.invoke(...)` call is now allowed to autonomously loop
through hundreds of tool calls before it has to stop. This is the mechanism that matters
for "the agent keeps working without me typing anything else" — not a bigger context
window, not a special "autonomous mode" flag. `examples/coding_agent.py` §4 runs exactly
this, and prints the real step count and real spend afterward.

### b) Across restarts — the LangGraph backend, checkpointed by `thread_id`

```python
from langgraph.checkpoint.sqlite import SqliteSaver   # MemorySaver in the example; use this for real
from harness.lg import build_agent

graph, _ = build_agent(model=..., tools=[...], budget="$5, 300 steps, 45m",
                       checkpointer=SqliteSaver.from_conn_string("coding_sessions.db"))

graph.invoke({"messages": [...]}, {"configurable": {"thread_id": "task-42"}})
```

Same `thread_id`, called again — even after the process restarted — resumes the same
conversation, the same accumulated spend, the same step count. This is what "long-running"
should mean across a crash or a redeploy, and it is the classic backend's `Agent` that
*cannot* do this (`Agent.resume(transcript)` replays interrupted `read`/`external` calls
from a transcript file — real, but roughly "90% of the value at 5% of the cost" by the
project's own accounting, not full node-level durability). **Use the LangGraph backend for
this reason alone if for no other.**

### c) A task too big for one agent — delegate, don't grow the budget without limit

```python
reader = Agent(name="Reader", job="Summarize a diff.", tools=[read_source],
               model="claude-haiku-4-5", budget="$0.05")
lead = Agent(name="Lead", job="Plan the change, delegate reading to Reader.",
            tools=[read_source, write_source, reader.as_tool()], budget="$2, 100 steps")
```

A sub-agent is a tool with its own budget, carved out of the parent's remaining budget —
not a second orchestration framework. For a task that would otherwise need an enormous
single budget (and an enormous, expensive context window to match), splitting into a
planner + narrowly-scoped workers keeps each individual agent auditable and its own
`Trajectory` checkable in isolation (§5).

**What none of this gives you:** resuming *mid-tool-call*. If the process dies while a
shell command is running, that command's result is lost, and — because `write`/`danger`
tools are never auto-retried by design (a retried `git push` is not idempotent) — the
agent does not blindly re-run it either. On resume it sees a normal "was this actually
applied?" situation, the same one a human developer resuming someone else's half-finished
change faces. This is `docs/01-requirements.md`'s stated assumption (typical runs ≤ 50
steps / 10 minutes; **longer runs need durable execution, and that's an explicit
non-goal**) — the three mechanisms above get you real session-level durability, not
crash-safety inside a single tool call.

---

## 2. The tool set — narrow and correctly classified, not one `run_shell`

`examples/coding_agent.py §1` builds six tools instead of one catch-all shell tool:

| tool | effect | why |
|---|---|---|
| `list_files`, `read_source`, `run_tests` | `read` | look, don't change anything the agent can't just look at again |
| `write_source`, `git_commit` | `write` | changes something, but undoable (`git reset`, overwrite again) |
| `git_push` | `danger` | not reliably undoable once someone else has pulled |

A single `run_shell(cmd: str)` has to be classified `danger` for the worst command it
could ever run — every `git diff`, every `ls`, every test run then needs a human's "yes."
Classifying by what each command in your task actually needs to do turns most of a coding
session into work the harness lets proceed on its own, and reserves human attention for
the part that deserves it.

**Two library primitives, wired in yourself — they are not automatic:**

- **`harness.workspace.confine(root, path)`** — resolves a model-supplied path against a
  root and refuses (does not "sanitize") anything that would land outside it, including
  absolute paths and `../` escapes. `read_file`/`write_file` in `harness.tools.builtin`
  do **not** call this — if you use them as-is, there is no root confinement. Build your
  own file tools around `confine()` (as the example does) or wrap the built-in ones.
- **`harness.sandbox.Subprocess`** — a clean-environment child process: `argv`, never a
  shell string (nothing to inject through with `;`/`&&`), a fixed `cwd`, a hard timeout,
  and `env=` used *exactly* as given — it is never merged with the parent's `os.environ`,
  so your own provider API key can't leak into a child process by accident (you'll notice
  this the first time `git commit` fails with "unknown identity" until you explicitly pass
  `HOME` through — that's the safety working, not a bug to route around). This is **not**
  container isolation (§01.5's stated non-goal: no bundled container runtime) — a
  determined command still sees the host filesystem outside `cwd` and the host network.
  It's the seam a real sandbox (Docker/Firecracker/gVisor) plugs into without touching
  anything above it, and shipping an honest "no isolation, said plainly" default is what
  makes that seam provable rather than aspirational.

Tool functions that call `Sandbox.run` (or do any real I/O) should be `async def` and
`await` it directly. A `def` (sync) tool is auto-wrapped onto a worker thread, and that
thread does not have an event loop of its own to run a nested `asyncio.run()` inside — the
first draft of `examples/coding_agent.py` hit exactly this (`RuntimeWarning: coroutine
'Subprocess.run' was never awaited`) before switching the shell-calling tools to `async
def`.

---

## 3. The one safety interaction specific to a coding agent

`examples/coding_agent.py §3` reproduces it directly: an agent with both `fetch` (reads
the web, `effect="external"`) and `git_push` (`effect="danger"`) is **refused at
construction**, before any code runs:

```
This helper can read things from the internet AND do something it can't undo.
```

This is the "lethal trifecta" applied to coding specifically: a docs-lookup tool plus a
push tool in the same agent means a page the agent fetches (a compromised package's
README, a prompt-injected Stack Overflow answer, a malicious `CONTRIBUTING.md`) can talk
the agent into pushing something it shouldn't. The fix isn't a special coding-agent
feature — it's the same rule every agent gets: split "research" and "execute" into two
agents, or opt in explicitly (`accepts_tainted=["git_push"]`) if you've decided that
specific combination really is safe for your setup and want that decision visible in a
code review rather than implicit.

The same mechanism protects an MCP-sourced coding tool exactly the same way: bind a
filesystem or git MCP server (`harness.mcp.bind_mcp_server`) with `trusted=False` (the
default), and every tool it exposes is classified `danger` unless you explicitly say
otherwise — the server's own hints about itself are never trusted for an unmarked server,
by design.

---

## 4. Evaluating a coding agent, not just running one

A coding agent's failure modes are exactly what `harness.testing.Trajectory` and
`harness.eval` were built to check declaratively instead of by reading a transcript:

```python
from harness.testing import Trajectory

Trajectory(
    must_call=frozenset({"write_source", "git_commit"}),   # it has to actually do the work
    must_not_call=frozenset({"git_push"}),                 # this task was never approved to push
    no_duplicate_side_effects=True,                        # no re-committing the same change twice
).check(result, effect_of={...})
```

Run a fixed set of coding tasks through `harness.eval.run_golden_set` and you get a pass
rate with a 95% confidence interval, plus total tokens and cost — the same regression
suite shape you'd want for any agent that changes over time as you adjust its prompt or
its tool set. `harness.eval.cost_per_success` turns "how much does this coding agent cost"
into the number that actually matters (spend divided by the probability a task succeeds,
not spend divided by attempts) — a coding agent that fails often but cheaply per attempt
is not automatically the cheaper one.

---

## 5. If you want it reachable over HTTP — `harness[server]`

```python
from harness.server import create_app
app = create_app({"coder": lead})   # POST /v1/runs {"agent": "coder", "message": "..."}
```

`GET /v1/runs/{id}/events` streams the canonical event feed (SSE) for a live view of a
long session; `POST /v1/runs/{id}/approvals/{id}` lets a remote human approve a `danger`
call (a `git_push`, say) without being in the same process — useful once "long-running"
means "hours, watched by someone who isn't sitting at the terminal that started it." This
is an `extra`, not core — `import harness` never pulls it in.

---

## Honest summary of what you still have to build

- The coding-specific tools themselves (file ops, git, test runner, whatever your stack
  needs) — the library gives you the classification/confinement/sandbox primitives, not a
  coding toolset. `examples/coding_agent.py` is a starting shape, not a finished one.
- A real sandbox plugged into the `Sandbox` protocol, if `Subprocess`'s clean-env
  subprocess isn't enough isolation for what you're running (untrusted or generated code
  especially).
- Whatever drives the loop across a whole feature (multiple sessions, multiple
  `thread_id`s, a task queue) — the harness durably runs *one* session; sequencing many of
  them into a larger unit of work is your orchestration layer.
