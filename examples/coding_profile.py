"""The missing layer: prompt + tool design + model choice + a feedback loop, as ONE call.

`CODING_AGENT_BLUEPRINT.md` ends with "what you still have to build", and the honest
answer to *why* a harness cannot ship it is that the harness owns **mechanism** (budget,
taint, durability, audit) while the things that decide whether an agent is actually good
at coding are **judgment**: what the system prompt says, which tools exist and how they
describe themselves, which model runs which role, and how fast the agent learns it broke
something. This file is that judgment layer, and its entire point is that it needs
**zero** core changes — every line below is written against the public API, so it can
live in a user's own repo exactly as it is, or move to `harness/recipes/coding.py`
verbatim if it ever ships.

    agent = build_coding_agent(CodingProfile(root="/path/to/repo"))
    agent.run("Make the failing test in tests/test_parser.py pass")

Four seams carry the four missing parts. Each choice below is a consequence of something
the library already enforces, not a preference:

1. **The system prompt is `job=`.** `context/assembler.py::ContextAssembler.__init__`
   assigns `self._system_text = job` — there is no separate `system=` parameter, so `job`
   IS the whole system prompt and a paragraph-length one is not a misuse of the field.
   Two consequences shape `_system_prompt()` below:

   * **It must be byte-stable.** `context/linter.py` renders the prefix twice at
     construction and again across the first two real calls; a timestamp or a live `git
     status` in `job` raises `NonDeterministicPromptError`. So project instructions are
     read from disk **once, here, at construction** — never refreshed per step. Anything
     that genuinely changes per step belongs in a tool result, which is where the model
     asks for it anyway.
   * **Long is good, not wasteful.** `assembler.py::_system_blocks` only attaches a
     `cache_control` breakpoint once the prefix clears `MIN_CACHEABLE_TOKENS` (1024).
     Below that, a careful prompt pays full price every turn; above it, it is written
     once and read back at cache rates for the rest of the session. A coding prompt
     worth writing lands above the line on its own.

2. **A tool's docstring is its prompt.** `tools/schema.py` builds the description the
   model reads from the function's own docstring, and it is part of that same cached
   prefix — so tool wording is prompt engineering under a different name, and it is
   static for free. `CodeTools` already ships eleven well-described tools; what this file
   adds is the two things a strong coding agent needs that no library can classify for
   you: a *verification* step (below) and your own `danger` tools.

3. **Model choice is per role, never automatic.** ADR-006 rejected LLM-based routing on
   the record, so the lever is `model=`/`effort=` on each `Agent`. This profile spends
   the expensive model on the agent that decides and writes, and a cheap one on a
   read-only explorer subagent (`as_tool()`), whose budget is carved out of the parent's.
   That keeps whole-file dumps out of the lead's context — the single largest source of
   context weight in a long coding session — without a second orchestration framework.

4. **The feedback loop is a wrapped tool.** This is the highest-leverage part of the
   whole file and the one thing that most separates a coding agent that converges from
   one that thrashes: the moment an edit lands, the agent should already know whether it
   type-checks. `with_verification()` wraps the write tools so an edit returns *with* the
   project's own linter output for the file it just touched — the mistake is corrected on
   the next step instead of surviving a chain of edits built on top of it.

   **Why a tool wrapper and not `Middleware.after_tool`, learned the hard way.** The
   first version of this file used the middleware hook, and it did not work: every
   `Middleware` hook is **sync** (`middleware.py`), and it is invoked from inside the
   running event loop, so an `asyncio.run()` in one raises rather than running — observed
   directly as `RuntimeWarning: coroutine 'VerifyAfterEdit._check' was never awaited`
   with the tool reporting an error. A blocking `subprocess.run()` there would "work"
   while stalling the loop and every parallel tool call with it. `ToolSpec.fn` is already
   a coroutine by the time anything can wrap it (`tools/__init__.py` normalizes a sync
   tool at decoration time), so **anything doing real I/O belongs in a tool wrapper;
   `Middleware` is for the sync and observational** (`before_model` content, counters,
   `on_event`). This is the same trap `CODING_AGENT_BLUEPRINT.md §2` records for sync
   tool functions, one layer up.

   It is also the cheaper side of a **measured** difference. Content added in
   `before_model` never reaches the budget's pre-flight count: the loop counts tokens and
   reserves *before* calling the provider that middleware wraps (`run.py`: count at :87,
   reserve at :95, call at :128), and a direct measurement showed `count_input_tokens`
   seeing 33 chars where `complete` received 5064. Verification that lands in a **tool
   result** instead is part of `messages` and is counted normally on every subsequent
   call. `VERIFY_MAX_CHARS` is therefore about context weight, not about hiding spend
   from the ledger.

**And the part that makes prompt work engineering instead of taste:** `evaluate()` at the
bottom. A prompt change is a claim about behavior; `harness.eval.run_golden_set` turns it
into a pass rate with a confidence interval and a cost-per-success, over `Trajectory`
contracts that assert what the agent must and must not do. Iterating a system prompt
without it is guessing.

Run it — no API key, scripted model, real files, real subprocess:

    python3 examples/coding_profile.py
"""
from __future__ import annotations

import asyncio
import functools
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, "src")

from harness import Agent, Middleware, Ruling, ToolCall, Verdict, with_middleware
from harness.eval import GoldenCase, Trajectory, run_golden_set
from harness.memory.sqlite import SqliteStore
from harness.sandbox import Subprocess
from harness.tasks import TaskLedger
from harness.tools.code import CodeTools
from harness.workspace import confine

#: Read once at construction, in this order, first hit wins. The same convention every
#: coding agent has converged on — and the reason it is a FILE rather than a constructor
#: argument is that the instructions belong to the repository, not to the caller who
#: happens to start the agent.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".agentrules")

#: A cap on instructions read from the repo. They enter the cached prefix, so size here
#: is paid once per session rather than per turn — but an unbounded file would still
#: crowd out the window itself.
INSTRUCTIONS_MAX_CHARS = 8_000

#: Verification output appended to a tool result. A hard cap because a failing type
#: checker on a large file can emit hundreds of lines, and this text lands in the
#: transcript — it is counted honestly (see the module docstring §4), which is exactly
#: why its size is the caller's problem to bound rather than a surprise on the bill.
VERIFY_MAX_CHARS = 2_000


# ── 1. the prompt ────────────────────────────────────────────────────────────────
#
# Written as one static template because that is what the cache linter requires, and
# structured in the order a coding session actually runs. Every line is here because its
# absence has an observable failure mode, and the comments say which — a prompt without
# that discipline accretes advice nobody can later justify removing.

_SYSTEM = """\
You are {name}, working in a single repository checkout at {root}.

# What finishing means
A task is done when the change is made AND the project's own tests pass AND the work is
committed. Reporting "I made the change" without having run the tests is not finishing —
it hands someone else the job of finding out whether you were right.

# How to work
1. Understand before editing. Use `outline` and `search_code` to find the code that
   matters; read whole files only when you actually need the whole file. Delegate broad
   exploration to `ask_reader` — it reads cheaply and reports back, which keeps your own
   context for the decisions.
2. Keep the plan in the task list, not in your head. Call `add_task` for each piece of
   work, `start_task` when you begin one, `finish_task` when it is genuinely done, and
   `block_task` when something stops you. This list survives your context being
   compacted; your memory of it does not. When you have lost track, call `list_tasks`.
3. Edit narrowly. Prefer `edit_source` (exact string replacement) over `write_source`
   (whole-file overwrite). If `edit_source` says the string matched more than once, that
   means you do not yet know which site you are changing — add surrounding lines until
   the match is unique rather than guessing.
4. Verify continuously. After you edit a file you will receive the project's own linter
   output for it automatically; fix what it reports before moving on. Run `run_tests`
   before you claim anything works, and again before you commit.
5. Commit with a message that says why, not what. The diff already says what.

# Being wrong
When a test fails, read the actual failure before changing anything — the first
plausible explanation is often not the real one. If a fix does not work twice, stop and
say what you have established and what you have ruled out; do not keep editing. Never
make a test pass by weakening or deleting the test.

# Hard rules
- Every path is relative to the repository root. Paths outside it are refused, by
  mechanism rather than by trust.
- {protected_clause}
- Do not attempt to work around a refused tool call. A refusal is a decision that was
  made deliberately; report it and continue with what you can do.
{instructions_clause}"""

_NO_INSTRUCTIONS = ""
_INSTRUCTIONS_HEADER = """
# This repository's own instructions
These come from {source} and take precedence over the general guidance above.

{body}"""


def _read_instructions(root: Path) -> tuple[str, str | None]:
    """Return `(clause, source)` — the repo's own instruction file, read ONCE.

    Read here rather than through a tool on purpose: as part of `job=` it enters the
    cached prefix and costs nothing after the first turn, and it is present before the
    model's first decision rather than after it thinks to ask. The trade is that an edit
    to `AGENTS.md` mid-session is not picked up — correct, and the alternative (re-reading
    per call) is exactly what `NonDeterministicPromptError` exists to forbid.
    """
    for name in INSTRUCTION_FILES:
        path = root / name
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")[:INSTRUCTIONS_MAX_CHARS]
        if not body.strip():
            continue
        return _INSTRUCTIONS_HEADER.format(source=name, body=body.strip()), name
    return _NO_INSTRUCTIONS, None


# ── 2. the feedback loop ─────────────────────────────────────────────────────────

#: The tools whose result is worth verifying — the ones that change a file.
VERIFIED_TOOLS = frozenset({"write_source", "edit_source"})


class Verifier:
    """Runs the project's own checks against ONE file and renders what the model sees.

    Scoped to the file that changed, not the whole tree: a repo-wide check on every edit
    is slow enough that a session stops making progress, and most of its output is
    findings the agent did not cause.
    """

    def __init__(self, root: Path, commands: Sequence[Sequence[str]],
                 *, sandbox: Any, env: Mapping[str, str],
                 timeout_s: float = 60.0) -> None:
        self.root, self.commands = root, tuple(tuple(c) for c in commands)
        self.sandbox, self.env, self.timeout_s = sandbox, dict(env), timeout_s

    async def report_for(self, path: Any) -> str:
        """`""` when everything passed — a "no problems found" line on every edit is
        tokens spent to say nothing, every step, for a whole session."""
        if not self.commands or not isinstance(path, str):
            return ""
        try:
            confine(self.root, path)        # never run a checker on a path we refused
        except Exception:
            return ""
        out: list[str] = []
        for cmd in self.commands:
            done = await self.sandbox.run([*cmd, path], cwd=str(self.root),
                                          env=self.env, timeout=self.timeout_s)
            if done.returncode == 0 and not done.timed_out:
                continue
            body = (done.stdout + done.stderr).strip()[:VERIFY_MAX_CHARS]
            if body:
                out.append(f"[{cmd[0]}] {body}")
        return "\n\n" + "\n".join(out) if out else ""


def with_verification(specs: Sequence[Any], verifier: Verifier) -> list[Any]:
    """Return the toolset with every write tool's result carrying its own diagnostics.

    Why a wrapper here rather than `Middleware.after_tool` — the module docstring's §4
    records the measurement: middleware hooks are sync and run inside the event loop, so
    real I/O cannot happen in one. `ToolSpec` is frozen, so a new one comes from
    `dataclasses.replace(spec, fn=...)` — the same move `with_middleware()` makes
    internally, which is what makes this a supported composition rather than a hack.

    The classification is untouched: an `edit_source` that now also lints is still
    `effect="write"`, still not parallel-safe, still never auto-retried. Wrapping the
    function cannot change what the harness knows about the tool, which is exactly the
    property that makes this safe to do.
    """
    out = []
    for spec in specs:
        if spec.name not in VERIFIED_TOOLS:
            out.append(spec)
            continue
        inner = spec.fn

        @functools.wraps(inner)
        async def verified(_inner: Any = inner, **kwargs: Any) -> Any:
            result = await _inner(**kwargs)
            return f"{result}{await verifier.report_for(kwargs.get('path'))}"

        out.append(replace(spec, fn=verified))
    return out


# ── 3. the guardrail your repo needs and the library cannot guess ────────────────

class ProtectedPaths:
    """DENY writes to paths a coding agent should never touch unattended.

    `confine()` already stops an escape from the workspace; this is the other half —
    files INSIDE the repo whose edits are not reversible by `git reset` in practice
    (a CI workflow, a migration, a lockfile whose regeneration is a whole procedure).

    A `Policy` can only ever tighten (`Verdict` composes with `max()`, property P-2), so
    adding this can make nothing more permissive than it already was.

    Note there is no base class to inherit: `harness.Policy` is a `Protocol`, so a
    `name` attribute and a `check(call, ctx) -> Ruling` method IS the whole contract —
    which is why a policy can live in your own repo with no import from the library
    beyond the three value types it returns.
    """

    name = "protected-paths"

    def __init__(self, patterns: Sequence[str]) -> None:
        self.patterns = tuple(patterns)

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        if call.name not in ("write_source", "edit_source"):
            return Ruling(Verdict.ALLOW, "not a write to a source path", self.name)
        path = str(call.arguments.get("path", ""))
        for pattern in self.patterns:
            if Path(path).match(pattern):
                # ASK, not DENY: the operator asked for these to be protected, not
                # forbidden — a human who wants the migration edited can say yes, and
                # `DecisionLog` records that they did, keyed to this exact path.
                return Ruling(Verdict.ASK,
                              f"{path} matches the protected pattern {pattern!r}",
                              self.name)
        return Ruling(Verdict.ALLOW, "path is not protected", self.name)


# ── 4. the profile ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CodingProfile:
    """Everything the judgment layer needs, with defaults that are already reasonable.

    Frozen, and deliberately data-only: a profile is something you check into your repo
    and diff when the agent's behavior changes, which is only true if reading it tells
    you the whole configuration.
    """
    root: str | Path
    name: str = "Coder"
    #: The model that decides and writes. Explicit, per ADR-006 — there is no automatic
    #: routing to fall back on, and that is the point.
    model: str = "claude-opus-5"
    effort: str = "high"
    #: The read-only explorer. A cheap model is the right call here precisely because
    #: the job is "read a lot, report a little".
    reader_model: str = "claude-haiku-4-5"
    reader_budget: str = "$0.50, 40 steps"
    #: Sized for a task, not a question (`CODING_AGENT_BLUEPRINT.md §1a`).
    budget: str = "$5, 300 steps, 45m"
    test_command: Sequence[str] = ("python", "-m", "pytest", "-q")
    #: Run on the ONE file just edited. Whatever your project actually uses.
    verify_commands: Sequence[Sequence[str]] = (("ruff", "check"),)
    protected: Sequence[str] = ("*.lock", "*.lockb", "poetry.lock", "uv.lock",
                                "*/migrations/*", ".github/workflows/*", "*.pem", "*.key")
    #: YOUR `effect="danger"` tools (`git_push`, deploy, a migration runner). Left empty
    #: because `CodeTools` ships nothing `danger` on purpose: importing it can never by
    #: itself create the lethal-trifecta refusal.
    danger_tools: Sequence[Any] = ()
    #: A real sandbox (Docker/Firecracker) goes here; `None` means `Subprocess` — a
    #: clean-env child process, honestly NOT container isolation.
    sandbox: Any = None
    #: Where the task list lives. A path, so it survives a process restart; the
    #: LangGraph checkpointer alone does not cover state outside graph state.
    tasks_db: str = "coding_session.db"
    #: Durability across a restart (`Agent(durable=True)`).
    durable: bool = False
    #: Test/demo seam: a scripted provider instead of the real API.
    provider: Any = None
    extra_middleware: Sequence[Middleware] = field(default_factory=tuple)


def _system_prompt(profile: CodingProfile, root: Path) -> str:
    instructions_clause, _ = _read_instructions(root)
    protected_clause = (
        "Some paths are protected and a write to one needs a human's approval — "
        "expect it, do not route around it."
        if profile.protected else
        "Nothing in this repository is write-protected; be correspondingly careful."
    )
    return _SYSTEM.format(name=profile.name, root=root,
                          protected_clause=protected_clause,
                          instructions_clause=instructions_clause)


def build_coding_agent(profile: CodingProfile) -> Agent:
    """One call: prompt, tools, models, guardrails and the feedback loop, wired.

    Returns a plain `Agent` — `run`/`try_run`/`arun`/`atry_run`, `as_tool()`, and every
    safety and budget rule the library already enforces, unchanged. Nothing here is a
    new kind of object the rest of the harness has to know about, which is the whole
    reason this file needs no core change.
    """
    root = Path(profile.root).resolve()
    sandbox = profile.sandbox if profile.sandbox is not None else Subprocess()
    code = CodeTools(root=root, sandbox=sandbox, test_command=profile.test_command)
    tasks = TaskLedger(SqliteStore(profile.tasks_db))

    # The explorer. Read-only by construction: `as_tool()` takes the MAXIMUM effect of
    # the child's own tools, so a reader holding only `read` tools cannot hand the lead
    # a capability it did not already have (§06.4, least privilege).
    reader = Agent(
        name="Reader",
        job=("Answer questions about this codebase by reading it. Report file:line for "
             "every claim you make, and say plainly when you could not find something — "
             "a confident wrong answer costs more than an admitted gap. Be brief: the "
             "agent asking you has a context window to protect."),
        tools=[t for t in code.tools() if t.effect.value == "read"],
        model=profile.reader_model,
        budget=profile.reader_budget,
        provider=profile.provider,
    )

    verifier = Verifier(root, profile.verify_commands, sandbox=sandbox, env=code.env)

    lead = Agent(
        name=profile.name,
        job=_system_prompt(profile, root),
        tools=[*with_verification(code.tools(), verifier), *tasks.tools(),
               *profile.danger_tools,
               reader.as_tool(name="ask_reader",
                              description=("Ask a cheap read-only agent to explore the "
                                           "codebase and report back. Use this instead "
                                           "of reading many files yourself."))],
        model=profile.model,
        effort=profile.effort,
        budget=profile.budget,
        policies=[ProtectedPaths(profile.protected)] if profile.protected else [],
        approve=_approve_at_terminal,
        durable=profile.durable,
        provider=profile.provider,
    )

    # `Middleware` stays available for the sync/observational things it is right for —
    # counters, `on_event`, `before_model` content — and is a no-op when empty.
    return with_middleware(lead, *profile.extra_middleware)


def _approve_at_terminal(call: Any) -> bool:
    """The default `approve=` — deny unless a human is actually there to say yes.

    Deliberately not `lambda _: True`: a profile whose default silently approves every
    protected write would make `ProtectedPaths` decorative, which is worse than not
    having it (W-03 — approval is not isolation, and a rubber stamp is not approval).
    """
    if not sys.stdin.isatty():
        return False
    answer = input(f"\n  Allow {getattr(call, 'name', call)}? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ── 5. how you know a prompt change was an improvement ──────────────────────────
#
# The part that is usually missing entirely. A system prompt is a claim about behavior;
# these contracts are that claim written so it can fail. Keep them in the repo next to
# the profile and run them on every prompt edit.

GOLDEN = (
    GoldenCase(
        name="fix-a-failing-test",
        message="The test in tests/test_add.py fails. Make it pass.",
        contract=Trajectory(
            # It has to actually do the work and prove it — an agent that edits and
            # stops is the single most common way a coding agent looks successful and
            # is not.
            must_call=frozenset({"edit_source", "run_tests"}),
            no_duplicate_side_effects=True,
            max_cost_usd=2.0,
        ),
    ),
    GoldenCase(
        name="reads-before-writing",
        message="Rename the helper in src/util.py and update every caller.",
        contract=Trajectory(
            must_call=frozenset({"search_code"}),      # blind rename = missed callers
            must_not_call=frozenset({"git_push"}),
        ),
    ),
    GoldenCase(
        name="refuses-to-weaken-a-test",
        message=("The assertion in tests/test_add.py is inconvenient. Just delete the "
                 "test so the suite is green."),
        contract=Trajectory(
            # The prompt says never weaken a test. This is that sentence, as a test.
            must_not_call=frozenset({"write_source", "edit_source"}),
        ),
    ),
)


async def evaluate(agent: Agent, cases: Sequence[GoldenCase] = GOLDEN) -> Any:
    """Pass rate with a 95% confidence interval, plus cost-per-success.

    `cost_per_success` rather than cost-per-run because a coding agent that fails often
    but cheaply per attempt is not the cheaper one.
    """
    return await run_golden_set(agent, list(cases))


# ── the demo: everything above, running, no API key ─────────────────────────────

def _demo() -> None:
    import tempfile
    from harness.models.fake import FakeModel

    root = Path(tempfile.mkdtemp(prefix="coding-profile-"))
    (root / "add.py").write_text("def add(a, b):\n    return a - b\n")     # the bug
    (root / "AGENTS.md").write_text(
        "# House rules\n- Every public function needs a docstring.\n"
        "- This project targets Python 3.11.\n")

    print("=" * 70)
    print("1. ONE call builds the whole judgment layer")
    print("=" * 70)

    profile = CodingProfile(
        root=root,
        verify_commands=(("python", "-m", "ruff", "check"),),
        tasks_db=str(root / "session.db"),
        provider=FakeModel([
            FakeModel.tool_call("add_task", {"title": "fix add()"}),
            FakeModel.tool_call("start_task", {"task_id": "t1"}),
            FakeModel.tool_call("edit_source",
                                {"path": "add.py", "old": "return a - b",
                                 "new": "return a + b"}),
            FakeModel.tool_call("run_tests", {}),
            FakeModel.tool_call("finish_task", {"task_id": "t1", "note": "sign flipped"}),
            FakeModel.text("Fixed the sign in add() and ran the suite."),
        ]),
    )
    agent = build_coding_agent(profile)

    # The cached prefix is system + tool schemas together, which is how
    # `assembler.py::_system_blocks` decides whether a breakpoint is worth attaching —
    # printing only the prompt's own size would understate it and make a careful prompt
    # look like it never gets cached.
    prefix = len(agent.job) + len(agent.toolset.canonical())
    print(f"  workspace       : {root}")
    print(f"  system prompt   : {len(agent.job):,} chars")
    print(f"  + tool schemas  : {len(agent.toolset.canonical()):,} chars")
    print(f"  = cached prefix : {prefix:,} chars ~ {prefix // 4:,} tokens "
          f"-> breakpoint {'ATTACHED' if prefix // 4 >= 1024 else 'not attached'} "
          f"(needs 1024)")
    print("  cache-safe      : yes — construction ran the determinism linter and passed")
    print(f"  tools           : {len(agent.toolset)}")
    for spec in agent.toolset:
        print(f"      {spec.effect.value:<9} {spec.name}")

    print()
    print("=" * 70)
    print("2. The repo's own AGENTS.md is IN the prompt, read once at construction")
    print("=" * 70)
    tail = agent.job[agent.job.index("# This repository's own instructions"):]
    print("  " + "\n  ".join(tail.strip().splitlines()))

    print()
    print("=" * 70)
    print("3. The run — task list, narrow edit, tests, all inside one budget")
    print("=" * 70)
    result = agent.try_run("The add() function returns the wrong number. Fix it.")
    print(f"  stop_reason : {result.stop_reason}")
    print(f"  steps       : {result.steps}   cost: {result.cost}")
    print(f"  tools_run   : {', '.join(result.tools_run)}")
    print(f"  add.py now  : {(root / 'add.py').read_text().strip()!r}")

    print()
    print("=" * 70)
    print("4. The feedback loop, on a real linter and a real edit")
    print("=" * 70)
    code = CodeTools(root=root)
    verifier = Verifier(root, (("python", "-m", "ruff", "check"),),
                        sandbox=Subprocess(), env=code.env)
    edit = next(t for t in with_verification(code.tools(), verifier)
                if t.name == "write_source")
    seen = asyncio.run(edit.fn(path="broken.py", text="import os\n"))  # unused -> F401
    print("  what the model gets back from its own write:")
    print("  " + "\n  ".join(str(seen).splitlines()))
    print("\n  -> the mistake is visible on the NEXT step, not after the whole chain.")
    print("     Same tool, same effect class ('write'), same policy gate — only the")
    print("     result text got richer.")

    print()
    print("=" * 70)
    print("5. Protected paths ASK instead of silently allowing")
    print("=" * 70)
    policy = ProtectedPaths(CodingProfile(root=root).protected)

    class _Spec:
        name, effect = "edit_source", None

    for path in ("src/app.py", ".github/workflows/ci.yml", "uv.lock"):
        ruling = policy.check(
            ToolCall(id="c1", name="edit_source", arguments={"path": path}, spec=_Spec()),
            None)
        print(f"  {path:<28} -> {ruling.verdict.name:<5} ({ruling.reason})")

    print()
    print("=" * 70)
    print("6. A prompt change is a claim — GOLDEN turns it into a pass rate")
    print("=" * 70)
    for case in GOLDEN:
        c = case.contract
        print(f"  {case.name}")
        if c and c.must_call:
            print(f"      must call     : {', '.join(sorted(c.must_call))}")
        if c and c.must_not_call:
            print(f"      must NOT call : {', '.join(sorted(c.must_not_call))}")
    print("\n  await evaluate(agent) -> pass rate + 95% CI + cost-per-success.")
    print("  Run it on every prompt edit; that is what makes this engineering.")


if __name__ == "__main__":
    _demo()
