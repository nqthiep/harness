"""A long-running coding agent, built on harness — see CODING_AGENT_BLUEPRINT.md.

Run:  python3 examples/coding_agent.py

No API key needed: defaults to a scripted fake model, enough to prove how the pieces
fit together. With a key it uses a real model instead:

    export ANTHROPIC_API_KEY=sk-ant-...
    pip install 'harness[graph]' langchain-anthropic

Six points, each a runnable block:
  1. Narrow coding tools, correctly effect-classified — not one `run_shell` that covers
     everything.
  2. `Workspace.confine()` — a file-touching tool only ever sees below one root.
  3. Lethal trifecta — external (reads the web) + danger (git_push) in the SAME agent is
     refused at construction, before any tool call happens.
  4/5. A "long-run" budget + checkpointer — ONE `invoke()` given enough steps/time for the
     agent to work through many tool calls on its own, surviving across multiple
     `graph.invoke()` calls by `thread_id` — plus a Trajectory contract to judge the
     result, instead of reading a transcript by eye.
  6. `TaskLedger` (durable across compaction) + `ProgressLedger` (mechanical stall
     detection, no extra tokens) — two additions AFTER the original blueprint, included
     here to prove they actually run, not just that they're documented.
"""
import asyncio
import os
import subprocess as _subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from harness import tool
from harness.errors import UnsafeToolSetError
from harness.sandbox import Subprocess
from harness.workspace import WorkspaceEscapeError, confine


def heading(title: str) -> None:
    print(f"\n{'═' * 70}\n{title}\n{'═' * 70}")


# ══════════════════════════════════════════════════════════════════════════
heading("1. NARROW tools, correctly classified — not one run_shell that covers everything")
# Each tool declares an effect() matching what it actually does. Folding everything into
# a single `run_shell(cmd: str)` forces the harness to classify it `danger` (safe for the
# worst case) — every command, even `git diff`, then needs a human's approval. Splitting
# by what each command can really do lets most of a coding session proceed without
# asking anyone.

WORKSPACE = Path(tempfile.mkdtemp(prefix="coding-agent-"))
SANDBOX = Subprocess()
_subprocess.run(["git", "init", "-q"], cwd=WORKSPACE, check=True)   # demo setup only


def _sync(coro):
    """Tools are always `async` (IDL-06) — this example calls `.fn(...)` directly,
    outside a real Agent, to illustrate each piece in isolation, so it has to drive the
    loop itself; inside a real Agent, the harness does this for you."""
    return asyncio.get_event_loop().run_until_complete(coro)


@tool(effect="read")
def list_files(subdir: str = ".") -> str:
    """List files in a subdirectory of the workspace."""
    root = confine(WORKSPACE, subdir)
    return "\n".join(sorted(p.name for p in root.iterdir())) or "(empty)"


@tool(effect="read")
def read_source(path: str) -> str:
    """Read a file's content from the workspace."""
    p = confine(WORKSPACE, path)
    return p.read_text(encoding="utf-8", errors="replace")[:200_000]


@tool(effect="write")
def write_source(path: str, text: str) -> str:
    """Write content to a file in the workspace, creating it if it doesn't exist."""
    p = confine(WORKSPACE, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"wrote {len(text)} characters to {path}"


async def _sh(argv: list[str], *, timeout: float = 30.0):
    """`await`ed directly, never spinning its own loop — unlike `_sync()` above, which is
    only for driving a tool outside an Agent. A tool inside an Agent runs in the
    harness's own event loop; a tool that does real I/O should be `async def` and
    `await` it directly, not nest a loop inside the one already running — nesting like
    that is why `Subprocess.run` was once "never awaited" in an early draft of this
    example."""
    # `env=` is used VERBATIM, never merged with the parent process's os.environ (T-7.4)
    # — leaking an environment variable (say, the harness's own API key) is not the
    # default; each one has to be asked for explicitly. `HOME` is needed so `git commit`
    # can find ~/.gitconfig (identity).
    return await SANDBOX.run(argv, cwd=str(WORKSPACE),
                             env={"PATH": os.environ.get("PATH", "/usr/bin"),
                                 "HOME": os.environ.get("HOME", "")},
                             timeout=timeout)


@tool(effect="read")
async def run_tests() -> str:
    """Run the workspace's test suite, returning the result."""
    r = await _sh(["python3", "-m", "pytest", "-q"], timeout=120.0)
    return f"returncode={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-500:]}"


@tool(effect="write")
async def git_commit(message: str) -> str:
    """Commit every current change in the workspace — undoable (git reset)."""
    await _sh(["git", "add", "-A"])
    r = await _sh(["git", "commit", "-m", message])
    return r.stdout or r.stderr


@tool(effect="danger")
async def git_push(remote: str, branch: str) -> str:
    """Push to a real remote — NOT easily undoable once someone else has pulled."""
    r = await _sh(["git", "push", remote, branch], timeout=60.0)
    return r.stdout or r.stderr


print("  6 tools: list_files/read_source/run_tests (read), write_source/git_commit "
     "(write), git_push (danger)")
print(f"  temp workspace: {WORKSPACE}")


# ══════════════════════════════════════════════════════════════════════════
heading("2. Workspace.confine() — the model cannot escape the root directory itself")
# The model can PROPOSE any path it likes as an argument — this is where the harness
# never trusts it, whatever the effect. It does not try to "escape" the input (stripping
# `..`, decoding then re-checking) — it resolves to an absolute path and checks
# containment exactly once.
(WORKSPACE / "hello.py").write_text("print('hi')\n")
print("  reading a valid file:", repr(_sync(read_source.fn(path="hello.py"))))

try:
    _sync(read_source.fn(path="../../etc/passwd"))
    print("  UNEXPECTED: read a file outside the workspace")
except WorkspaceEscapeError as e:
    print(f"  correctly blocked (../..): {e}")

try:
    _sync(read_source.fn(path="/etc/passwd"))
    print("  UNEXPECTED: read an absolute path")
except WorkspaceEscapeError as e:
    print(f"  correctly blocked (absolute path): {e}")


# ══════════════════════════════════════════════════════════════════════════
heading("3. Reading + browsing the internet in the SAME agent as git_push — refused at construction")
# This is exactly the "lethal trifecta" QUICKSTART.md describes, applied directly to a
# coding agent: an external tool (say, looking up docs on the web) plus a danger tool
# (git_push) in the SAME agent means a malicious page can talk the agent into pushing
# bad code.
from harness.tools.web import fetch                      # noqa: E402
from harness import Agent                                # noqa: E402

try:
    Agent(name="Bad", job="Code and look things up.", tools=[fetch, git_push])
    print("  UNEXPECTED: not blocked")
except UnsafeToolSetError as e:
    print(f"  correctly blocked at construction: {str(e).splitlines()[0]}")
print("  Fix: split 'research' (reads the web) and 'execute' (git_push) into two agents —")
print("  or accepts_tainted=['git_push'] if the operator takes explicit responsibility for it.")


# ══════════════════════════════════════════════════════════════════════════
heading("4 & 5. Long-run budget + checkpointer — one session across many invoke() calls")
try:
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import HumanMessage
    from harness.lg import build_agent
except ImportError:
    print("  needs `pip install 'harness[graph]'` — skipping this part")
else:
    def get_model(script):
        if os.environ.get("ANTHROPIC_API_KEY"):
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model="claude-opus-5")
        from fake_chat import FakeChat
        return FakeChat(script=script)

    from fake_chat import FakeChat

    def approve_git_push(call, ctx) -> bool:
        print(f"    [needs human approval] {call.name}({call.arguments}) -> defaults to DENY in this example")
        return False

    graph, _rt = build_agent(
        model=get_model([FakeChat.call("write_source", {"path": "hello.py",
                                                        "text": "print('hi v2')\n"}, "c1"),
                         FakeChat.call("run_tests", {}, "c2"),
                         FakeChat.call("git_commit", {"message": "update hello.py"}, "c3"),
                         FakeChat.text("Fixed, tests ran, committed.")]),
        tools=[list_files, read_source, write_source, run_tests, git_commit],
        # "Long-run" here is not many calls to run() — it's ONE invoke() allowed enough
        # steps/time for the agent to loop on its own (edit -> run tests -> read the
        # failure -> edit again -> ...) until it's done or the budget runs out. The
        # default (20 steps, 5 minutes) suits a question; a coding task needs far more.
        budget="$5, 300 steps, 45m",
        approve=approve_git_push,
        checkpointer=MemorySaver(),          # for real use, SqliteSaver/PostgresSaver —
                                             # survives the process exiting, not just
                                             # multiple invoke() calls in ONE process
    )

    config = {"configurable": {"thread_id": "task-42"}}
    result = graph.invoke({"messages": [HumanMessage("Fix hello.py to print 'hi v2', run "
                                                      "the tests, then commit.")]}, config)
    print(f"  {result['messages'][-1].content}")

    state = graph.get_state(config).values
    print(f"  checkpoint: {state['step']} steps, spent ${state['spent_usd']}")
    print("  calling invoke() again with the same thread_id 'task-42' (even after this")
    print("  process exits, if the checkpointer is SqliteSaver) resumes this exact session.")


# ══════════════════════════════════════════════════════════════════════════
heading("Evaluation — a Trajectory contract, not reading a transcript by eye")
from harness.eval import Trajectory, check_trajectory                    # noqa: E402
from harness import Agent as ClassicAgent                                # noqa: E402
from harness.models.fake import FakeModel                                # noqa: E402


class _Collect:
    """Same seam every other per-call collector in this codebase uses — an exporter
    bound to the agent so a run's events are captured without mutating it."""

    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


collector = _Collect()
eval_agent = ClassicAgent(
    name="Coder", job="Fix bugs and commit.",
    tools=[write_source, run_tests, git_commit],
    budget="$5, 300 steps, 45m", exporters=[collector],
    provider=FakeModel([FakeModel.tool_call("write_source",
                                            {"path": "hello.py", "text": "print('hi v3')"}),
                        FakeModel.tool_call("run_tests", {}),
                        FakeModel.tool_call("git_commit", {"message": "fix"}),
                        FakeModel.text("Done.")]))
eval_result = eval_agent.try_run("Fix hello.py and commit.")
report = check_trajectory(
    Trajectory(must_call=frozenset({"write_source", "git_commit"}),
              must_not_call=frozenset({"git_push"}),      # this task was never approved to push
              no_duplicate_side_effects=True),
    eval_result, collector.events,
    effect_of={"write_source": "write", "git_commit": "write",
              "git_push": "danger", "run_tests": "read"})
print(f"  trajectory.ok = {report.ok}  (tools_run = {eval_result.tools_run})")


# ══════════════════════════════════════════════════════════════════════════
heading("6. TaskLedger + ProgressLedger — a durable task list, and mechanical stall detection")
# Both were added AFTER the blueprint's original five points (§5/§6 in
# CODING_AGENT_BLUEPRINT.md). TaskLedger is OPT-IN — you build it and wire its `.tools()`
# in yourself. ProgressLedger is the OPPOSITE — every Agent, every run, both backends,
# has one automatically; there is nothing to configure.
from harness.tasks import TaskLedger                                     # noqa: E402
from harness.memory.inmemory import InMemoryStore                        # noqa: E402
from harness.result import StopReason                                    # noqa: E402

task_store = InMemoryStore()
task_ledger = TaskLedger(task_store)
task_tools = task_ledger.tools()
list_tasks, add_task, start_task, finish_task, block_task = task_tools

tracked_agent = ClassicAgent(
    name="Coder-with-ledger", job="Track work as you go.",
    tools=[*task_tools, write_source],
    budget="$1, 30 steps",
    provider=FakeModel([
        FakeModel.tool_call("add_task", {"title": "fix hello.py"}),
        FakeModel.tool_call("start_task", {"task_id": "t1"}),
        FakeModel.tool_call("write_source", {"path": "hello.py", "text": "print('hi v4')"}),
        FakeModel.tool_call("finish_task", {"task_id": "t1", "note": "fixed"}),
        FakeModel.text("Done."),
    ]))
tracked_agent.try_run("Fix hello.py, track progress.")
print("  " + asyncio.run(task_ledger.summary()).replace("\n", "\n  "))
print("  -> durable across compaction: progress lives in the Store, not in the "
     "conversation history — clearing old tool results doesn't make the agent forget "
     "what it's doing.")

print()
looping_agent = ClassicAgent(
    name="Coder-stuck", job="List the files.",
    tools=[list_files],
    budget="$5, 300 steps, 45m",         # a very generous step ceiling — without
                                         # ProgressLedger this would have to run all
                                         # 300 steps before stopping
    provider=FakeModel([FakeModel.tool_call("list_files", {})] * 8
                       + [FakeModel.text("(never reached)")]))
loop_result = looping_agent.try_run("List the files (deliberately scripted to repeat).")
print(f"  list_files() called identically 8 times in a row -> stopped at step "
     f"{loop_result.steps}, stop_reason={loop_result.stop_reason.value!r}")
print(f"  == StopReason.STALLED: {loop_result.stop_reason is StopReason.STALLED}")
print("  -> not the budget (300-step ceiling) — ProgressLedger recognized 6 consecutive "
     "steps with no new tool+args signature and stopped EARLY, at no extra model-call "
     "cost to notice it.")

print(f"""
{'─' * 70}
Architecture summary — see CODING_AGENT_BLUEPRINT.md for the full picture.
""")
