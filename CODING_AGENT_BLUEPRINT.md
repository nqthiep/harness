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
`Trajectory` checkable in isolation (§4).

**What none of this gives you:** resuming *mid-tool-call*. If the process dies while a
shell command is running, that command's result is lost, and — because `write`/`danger`
tools are never auto-retried by design (a retried `git push` is not idempotent) — the
agent does not blindly re-run it either. On resume it sees a normal "was this actually
applied?" situation, the same one a human developer resuming someone else's half-finished
change faces.

**What you can get, on the LangGraph backend, for one argument:** protection against the
*other* half of that problem. LangGraph checkpoints after a node finishes, so a crash
inside the tools node re-runs the whole batch on resume — including the `git_push` that
already succeeded. Pass a store and a `write`/`danger` call is recorded under
`thread_id:call_id`, so the resumed run replays the recorded result instead of pushing
twice:

```python
graph, _ = build_agent(model=..., tools=[...], checkpointer=SqliteSaver...,
                       idempotency_store=SqliteStore("idempotency.db"))
```

`read` calls deliberately do **not** replay — after a crash you want the file as it is
now, not as it was (ADR-064). This is `docs/01-requirements.md`'s stated assumption (typical runs ≤ 50
steps / 10 minutes; **longer runs need durable execution, and that's an explicit
non-goal**) — the three mechanisms above get you real session-level durability, not
crash-safety inside a single tool call.

---

## 2. The tool set — narrow and correctly classified, not one `run_shell`

**`harness.tools.code.CodeTools` now ships this**, so the table below is what you get
rather than what you write:

```python
from harness.tools.code import CodeTools

code = CodeTools(root="/path/to/repo")          # every path confined to this root
lead = Agent(name="Lead", job="...", tools=[*code.tools(), git_push],
             budget="$5, 300 steps, 45m")
```

| tool | effect | why |
|---|---|---|
| `list_files`, `read_source`, `search_code`, `outline`, `git_status`, `git_diff` | `read` | look, don't change anything the agent can't just look at again |
| `write_source`, `edit_source`, `git_commit` | `write` | changes something, but undoable (`git reset`, overwrite again) |
| `run_tests`, `refresh_codebase_docs` | `danger` | **executes** code, not just files it — see below |
| `git_push` — **yours, not the module's** | `danger` | not reliably undoable once someone else has pulled |

Two of those deserve their reasons said out loud.

**`run_tests`/`refresh_codebase_docs` are `danger`, not `write` — fixed after an
adversarial review found the earlier classification let the model execute arbitrary code
with no approval (`design/review-architect.md` G-1).** Two prior drafts of this document
each got this wrong in a different direction: the first had `run_tests` as `read` (a test
run writes caches/artifacts, and two parallel runs fight over them — genuinely not
read-only); the fix for that landed on `write`, which is *closer* but still wrong, because
`write` auto-allows at `safety="standard"`. The real problem `write` doesn't solve:
`write_source` lets the model write *any* content to a `.py` file, and `run_tests` then
**imports** that file to run it — importing a test file is executing whatever is at its
module scope. `write_source` (writes a file) followed by `run_tests` (imports and runs it)
is unapproved code execution assembled from two `write`-classified tools, demonstrated
concretely: a test file whose body runs `os.system(...)`, executed with the harness
process's own privileges, with nobody asked. `danger`'s floor is `ASK` at *both* safety
levels — the one class that actually requires approval before a model-authored program
runs. `refresh_codebase_docs` shells out to an external binary for the same reason.
`run_tests`'s own `target`/`git_diff`'s own `path` arguments are also now confined to the
workspace root (`design/review-architect.md` G-2) — they used to reach `argv` unchecked,
unlike every other path-taking tool in this module.

**`edit_source` replaces an exact string and refuses when it matches more than once.**
Rewriting a whole file costs tokens proportional to the file and is the number-one source
of "fixed one line, silently deleted three functions." But a string that appears twice
means the model does not actually know which site it is editing — so that is an error with
instructions, not a silent edit of the first match.

`outline` and `search_code` are the "understands the code" half: a `read_source`-only
agent reads an entire file to find one function, paying for all of it, every time.
`outline` returns the class/def map with line numbers; `search_code` returns `file:line`
hits.

**`refresh_codebase_docs` shells out to [OpenWiki](https://github.com/langchain-ai/openwiki)
"code mode"** (`openwiki --init`/`--update`, not a harness dependency — install it yourself
if you want this tool to do anything) to regenerate a `openwiki/` wiki whose claims cite
exact `repo://path#Lx-Ly` evidence, so it can't go stale the way hand-written docs do. It is
a tool exactly like `run_tests` or `git_commit`: nothing calls it but the model, on purpose —
auto-running it before every session would spend a whole extra model call of OpenWiki's own
on every run, whether or not anyone needed the wiki refreshed.

A single `run_shell(cmd: str)` has to be classified `danger` for the worst command it
could ever run — every `git diff`, every `ls`, every test run then needs a human's "yes."
Classifying by what each command in your task actually needs to do turns most of a coding
session into work the harness lets proceed on its own, and reserves human attention for
the part that deserves it.

**Two library primitives, wired in yourself — they are not automatic:**

- **`harness.workspace.confine(root, path)`** — resolves a model-supplied path against a
  root and refuses (does not "sanitize") anything that would land outside it, including
  absolute paths and `../` escapes. `CodeTools` calls it on every path. `read_file` and
  `write_file` in `harness.tools.builtin` now call it too, against the process's current
  working directory — they used to hand a model-supplied path straight to `Path`, which
  made `read_file(path="../../.ssh/id_rsa")` one `tool_use` block wide on the very tools
  the beginner path hands out. Use `CodeTools(root=...)` when the root is not the CWD.
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

The same mechanism protects an MCP-sourced coding tool exactly the same way: connect a
filesystem or git MCP server through `harness.mcp.connect(session, McpServerPolicy(
identity=ServerLabel(...), trusted=False))` (the default) and every tool it exposes is
classified `danger` unless you explicitly say otherwise — the server's own hints about
itself are never trusted for an unmarked server, by design.

---

## 4. Evaluating a coding agent, not just running one

A coding agent's failure modes are exactly what `harness.eval.Trajectory`/
`check_trajectory` and `harness.eval.run_golden_set` were built to check declaratively
instead of by reading a transcript:

```python
from harness.eval import Trajectory, check_trajectory

report = check_trajectory(
    Trajectory(
        must_call=frozenset({"write_source", "git_commit"}),   # it has to actually do the work
        must_not_call=frozenset({"git_push"}),                 # this task was never approved to push
        no_duplicate_side_effects=True,                        # no re-committing the same change twice
    ),
    result, events, effect_of={...})
```

`events` is the run's own event stream — attach a small collector as an `exporters=[...]`
entry on the `Agent` under test and read `.events` back off it afterward
(`examples/coding_agent.py`'s evaluation section shows the exact idiom); `harness.eval`'s
own `run_golden_set` does this internally so a caller never has to wire it by hand for a
golden-set run.

Run a fixed set of coding tasks through `harness.eval.run_golden_set` and you get a pass
rate with a 95% confidence interval, plus total tokens and cost — the same regression
suite shape you'd want for any agent that changes over time as you adjust its prompt or
its tool set. `harness.eval.cost_per_success` turns "how much does this coding agent cost"
into the number that actually matters (spend divided by the probability a task succeeds,
not spend divided by attempts) — a coding agent that fails often but cheaply per attempt
is not automatically the cheaper one.

---

## 5. Keeping the plan when the context window can't — `harness.tasks.TaskLedger`

An hour into a session the conversation no longer fits, and `context/window.py` starts
clearing old tool results at 60% of the window. If "what I've done and what's left" only
existed in those results, the agent forgets its own task list at exactly the moment the
task gets long. `TaskLedger` is the fix, and it costs **zero extra tokens** — it is
durable state, not a planning model call:

```python
from harness.memory import SqliteStore
from harness.tasks import TaskLedger

tasks = TaskLedger(SqliteStore("session.db"))
lead = Agent(name="Lead", job="Work through the task list; keep it updated.",
             tools=[read_source, write_source, run_tests, *tasks.tools()],
             budget="$5, 300 steps, 45m")
```

The model gets five tools — `list_tasks` (`read`), `add_task`, `start_task`,
`finish_task`, `block_task` (`write`) — and one cheap `list_tasks` call rebuilds the whole
picture after a compaction. Backed by a `SqliteStore` the list also survives a process
restart, which the LangGraph checkpointer alone does not give you for anything outside
graph state. Planning stays the model's job (ADR-023 is unchanged: no second model call
for reflection); *remembering* the plan is the harness's. See ADR-061.

`examples/coding_agent.py §6` runs this end to end — adds a task, starts it, writes the
fix, finishes it, and prints the real `TaskLedger.summary()` afterward.

## 6. Going in circles is its own failure — and it's caught for free

The expensive way a long coding session fails is not "ran out of budget." It's the agent
that keeps calling tools for another 200 steps while repeating what it just did — the
budget still stops it, at the last possible moment, and reports `step_limit`, which
describes the wrong thing.

`harness.progress` watches for that mechanically, from the tool calls the harness already
sees, so it costs **no extra tokens and no configuration** — it is on in both backends:

```python
result = lead.try_run("Fix the failing tests")
if result.stop_reason is StopReason.STALLED:
    print(result.detail)   # "6 consecutive steps with no new tool call — stopping ..."
```

A step counts as no-progress only when *every* call in it repeats a `tool+args` signature
already seen this run; six such steps in a row stop it. A normal edit → test → edit loop
never trips it, because each `write_source` carries a different body and that resets the
counter. See ADR-062, and `tests/test_progress_stall.py` for the twelve-lap case that
proves honest work is not killed.

`examples/coding_agent.py §6` reproduces the stall directly: a scripted model calling the
same tool with the same arguments eight times in a row stops at step 6 with
`StopReason.STALLED`, not at the 300-step budget ceiling.

## 7. Who approved the `git push` — and can you still prove it tomorrow?

A long coding session accumulates approvals: a push here, a migration there, each one a
human's decision that an auditor may ask about weeks later. `DecisionLog` records them —
`Scope` locks a grant to the **argument values**, so approving `push(branch="main")` does
not approve `push(branch="release")`, which is the part most frameworks skip.

```python
from harness import Agent
from harness.policy.decision import DecisionLog   # not re-exported from `harness` itself

log = DecisionLog(journal="approvals.jsonl")     # append-only, created 0600
lead = Agent(name="Lead", job="...", tools=[...], approve=my_callback, decisions=log)
...
for d in log.all():
    print(d.decided_at, d.actor, d.verdict, d.scope.tool, d.scope.args)
```

Without `journal=`, the book is in memory and dies with the process — fine for one
session, not for an audit trail. With it, the record survives a restart, and a grant a
human gave "for the next hour" is still there when the process comes back. Denials are
recorded too, including the approval-fatigue cap. Revoking is appending a `DENY`, never
editing a row. The same `decisions=` argument works on `build_agent()`. See ADR-063 — and
note that the journal holds real argument values (it must, to match a scope), so treat the
file as credential-adjacent.

## 8. What happens when the conversation stops fitting

At 60% of the model's window the harness blanks old tool-result content; at 80% it drops
the oldest whole steps. Neither costs a model call, and the second is what makes a
multi-hour coding session finish instead of ending in a provider rejection — a coding
agent's context weight is mostly `edit_source(path, old, new)` **arguments**, which live
in assistant turns and are never blanked, so a run like that climbs no matter how much
result content you clear.

What compaction drops is the agent's own earlier reasoning. That is survivable only
because the things that matter are kept somewhere else:

| what survives | where it lives |
|---|---|
| the task itself | `messages[0]`, never dropped |
| what's done and what's left | `TaskLedger` → a `Store` (§5) |
| who approved what | `DecisionLog` → an append-only journal (§7) |
| what has already been executed | the idempotency store (§1) |

If nothing can be cleared and nothing dropped, the run stops with `StopReason.ERROR` and
a `detail` telling you the window is full — rather than the provider rejecting the next
request for a reason you have to reverse-engineer. See ADR-066.

## 9. If you want it reachable over HTTP — `harness[server]`

```python
from harness.server import create_app
app = create_app(lead, authenticate=your_auth_check)   # POST /v1/runs {"message": "..."}
```

`create_app` serves ONE `Agent` (not a name-keyed dict of several — an earlier draft of
this example showed one, and it never matched the real signature). `authenticate` is
required, no default (`design/review-architect.md` G-3): called on every route before
anything else runs, a falsy/raising result is a `401`. Put your own check there — a
verified API key, a session lookup — or `authenticate=lambda request: True` if this app
already sits behind a gateway that authenticates for you; the point is that no deployment
gets an open Service API by omission.

`GET /v1/runs/{id}/events` streams the canonical event feed (SSE) for a live view of a
long session; `POST /v1/runs/{id}/approvals/{id}` lets a remote human approve a `danger`
call (a `git_push`, say) without being in the same process — useful once "long-running"
means "hours, watched by someone who isn't sitting at the terminal that started it." This
is an `extra`, not core — `import harness` never pulls it in.

---

## Honest summary of what you still have to build

- Anything `CodeTools` does not cover — it ships file navigation, search, a Python
  outline, precise editing, a test runner and read-only git plus commit. A different
  language's structural outline, your build system, your linter, `git_push` (deliberately
  left out: this module ships nothing `effect="danger"`, so importing it can never
  create the lethal-trifecta refusal by itself) are yours to declare.
- A real sandbox plugged into the `Sandbox` protocol, if `Subprocess`'s clean-env
  subprocess isn't enough isolation for what you're running (untrusted or generated code
  especially).
- Whatever drives the loop across a whole feature (multiple sessions, multiple
  `thread_id`s, a task queue) — the harness durably runs *one* session; sequencing many of
  them into a larger unit of work is your orchestration layer.
