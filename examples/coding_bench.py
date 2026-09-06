"""How you actually get a STRONG coding agent: measure it on real tasks, then iterate.

`examples/coding_profile.py` is the judgment layer — prompt, tools, models, feedback
loop. What it cannot tell you is whether any of those choices are *good*. This file is
the missing other half: a benchmark whose tasks are real, whose ground truth is not a
matter of opinion, and which can therefore answer the only question that matters when you
change a prompt — **did that make it better, or did it just make it different?**

**Where the tasks come from: your own git history.** A commit that changed source files
*and* test files is a task with an answer key already attached. Check out the tree at that
commit, then revert only the source files to their parent state: the test now exists, and
it fails. That is a task statement plus a machine-checkable definition of done, and you
have as many of them as you have commits. This is how SWE-bench is built; the point here
is that it works on whatever repository you actually care about, which is a much better
proxy for your agent's real job than any public set.

Two disciplines make the numbers mean something, and both are enforced below rather than
recommended:

1. **A case is only valid if the test fails before the agent starts.** A case whose test
   passes at the reverted state proves nothing — the agent can "succeed" by doing
   nothing. `_validate` runs the test first and DISCARDS such cases, and the report says
   how many were discarded. If a harvest yields two valid cases out of thirty, that is a
   fact about the repository's test suite, not about the agent, and you should see it.
2. **Success is the test passing, not the run completing.** `Result.ok` means the loop
   reached `COMPLETED` — an agent that confidently reports "fixed it" and did not, is
   `ok`. So scoring re-runs the test afterwards and uses THAT. `harness.eval.cost_per_
   success` documents accepting anything with `.ok` and a money-shaped `.cost`, so the
   ground truth goes in through the front door (`_Scored` below), not around it.

Each case runs in its own `git worktree`, so a case cannot see or corrupt another one's
tree, and your actual checkout is never touched.

    # Harvest and validate cases — no model, no API key, no cost:
    python3 examples/coding_bench.py --dry-run

    # The real thing (spends money — N cases x the profile's budget):
    python3 examples/coding_bench.py --limit 20

**What this measures and what it does not.** A commit subject is a thin task description;
real work arrives with a bug report, a reviewer's comment, a conversation. And a
reconstructed commit rewards reproducing a change a human already chose, not judging
which change was right. Both are real limits — but they are *constant across prompt
versions*, which is exactly what an A/B comparison needs. Use this to decide whether
change X helped; do not read the absolute number as "my agent is 68% good at
programming".
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from coding_profile import CodingProfile
from harness import Agent
from harness.eval.cost import cost_per_success
from harness.memory.sqlite import SqliteStore

#: A commit touching more than this many files is usually a rename, a reformat, or a
#: merge of several ideas — a poor task statement whichever it is.
MAX_FILES_PER_COMMIT = 6

#: How long one case's test run may take. A case slower than this makes the loop too
#: slow to iterate on, which is the whole purpose here.
TEST_TIMEOUT_S = 300.0


def _git(repo: Path, *args: str, timeout: float = 60.0) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _exists_at(repo: Path, rev: str, path: str) -> bool:
    """Whether `path` existed at `rev` — `git cat-file -e` is the cheap plumbing check,
    and its non-zero exit is the answer rather than an error."""
    done = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}:{path}"],
                          capture_output=True, text=True, timeout=30)
    return done.returncode == 0


def _is_test(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in path


@dataclass(frozen=True)
class Case:
    """One task with an answer key. `sources` are reverted; `tests` are not."""
    commit: str
    subject: str
    sources: tuple[str, ...]
    tests: tuple[str, ...]

    @property
    def name(self) -> str:
        return f"{self.commit[:8]} {self.subject[:60]}"


def harvest(repo: Path, *, limit: int = 20, scan: int = 300) -> list[Case]:
    """Candidate cases from git history, newest first.

    `scan` is how many commits to look at; `limit` how many candidates to keep. They are
    different numbers because most commits are not usable tasks — docs, renames,
    test-only or source-only changes — and the ratio between them is itself worth seeing.
    """
    log = _git(repo, "log", "--no-merges", f"-n{scan}", "--format=%H%x00%s")
    out: list[Case] = []
    for line in log.splitlines():
        if "\x00" not in line:
            continue
        sha, subject = line.split("\x00", 1)
        try:
            names = _git(repo, "show", "--name-only", "--format=", sha).split()
        except RuntimeError:
            continue
        py = [n for n in names if n.endswith(".py")]
        if not py or len(names) > MAX_FILES_PER_COMMIT:
            continue
        tests = tuple(n for n in py if _is_test(n))
        sources = tuple(n for n in py if not _is_test(n))
        # Both halves are required: the tests are the answer key, the sources are what
        # the agent has to write. A commit with only one half is not a task.
        if not tests or not sources:
            continue
        out.append(Case(sha, subject.strip(), sources, tests))
        if len(out) >= limit:
            break
    return out


class Workspace:
    """One case's own `git worktree`, torn down afterwards.

    A worktree rather than a copy of the directory: it is the same mechanism git already
    has for "this tree, at that commit, over there", it is fast, and the agent gets a
    real repository — `git status`, `git diff` and `git commit` all work, which matters
    because those are tools it has.
    """

    def __init__(self, repo: Path, case: Case, base: Path) -> None:
        self.repo, self.case = repo, case
        self.path = base / case.commit[:8]

    def __enter__(self) -> "Workspace":
        _git(self.repo, "worktree", "add", "--detach", "--quiet",
             str(self.path), self.case.commit)
        # Everything after the `worktree add` is inside try/finally-shaped cleanup on
        # purpose: `with` only calls `__exit__` when `__enter__` RETURNED, so a raise
        # below would leak a registered worktree. Observed, not hypothesized — the run
        # that died on the pathspec error above left `/tmp/coding-bench-*/b4c43575`
        # registered in `git worktree list` afterwards.
        try:
            # The tree is now the state AFTER the fix. Revert ONLY the source files, so
            # the test stays as the commit left it: present, and failing.
            parent = f"{self.case.commit}^"
            for source in self.case.sources:
                if _exists_at(self.repo, parent, source):
                    _git(self.path, "checkout", parent, "--", source)
                else:
                    # The commit CREATED this file, so its pre-fix state is "absent" —
                    # a write-it-from-scratch task. Checking it out from the parent
                    # would fail with "pathspec did not match", which is how the first
                    # run of this file died; deleting is what reverting means here.
                    (self.path / source).unlink(missing_ok=True)
        except BaseException:
            self._remove()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self._remove()

    def _remove(self) -> None:
        try:
            _git(self.repo, "worktree", "remove", "--force", str(self.path))
        except RuntimeError:
            # The directory can be gone while git still has it registered; prune is the
            # command that reconciles the registry with the filesystem.
            shutil.rmtree(self.path, ignore_errors=True)
            try:
                _git(self.repo, "worktree", "prune")
            except RuntimeError:
                pass


def run_tests(path: Path, tests: Sequence[str]) -> tuple[bool, str]:
    """`(passed, output_tail)` for just this case's tests — not the whole suite.

    The whole suite would fold in every unrelated failure the repository already has at
    that commit, and score the agent on them.
    """
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests],
                              cwd=str(path), capture_output=True, text=True,
                              timeout=TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"TIMED OUT after {TEST_TIMEOUT_S}s"
    return done.returncode == 0, (done.stdout + done.stderr).strip()[-800:]


@dataclass(frozen=True)
class Outcome:
    case: Case
    #: `None` when the case was discarded before the agent ever ran.
    valid: bool
    fixed: bool = False
    stop_reason: str = ""
    tools_run: tuple[str, ...] = ()
    cost_usd: float = 0.0
    seconds: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class _Scored:
    """What `cost_per_success` reads: `.ok` and `.cost`.

    Its docstring states this shape is supported deliberately, and that is what lets the
    ground truth — the test passing — be the definition of success instead of the run
    merely finishing.
    """
    ok: bool
    cost: float


def _validate(ws: Workspace) -> tuple[bool, str]:
    passed, tail = run_tests(ws.path, ws.case.tests)
    if passed:
        return False, "test already passes at the reverted state — not a task"
    if "error" in tail.lower() and "collected 0 items" in tail.lower():
        return False, "test does not even collect at the reverted state"
    return True, ""


def bench(repo: Path, cases: Sequence[Case], *, profile: CodingProfile | None = None,
          provider: Any = None, dry_run: bool = False) -> list[Outcome]:
    """Run every case; return one `Outcome` each, discarded cases included.

    `dry_run` harvests and validates without building an agent or spending anything —
    run it first, always: it tells you how many of your cases are real before you pay to
    find out.

    `provider=None` (the default) means the real `AnthropicProvider` — this is the
    path that actually spends money against the live API. Threaded through explicitly,
    not left implicit in the `Agent(...)` call below, so a test can pass a `FakeModel`
    and exercise this function's own logic (the per-case `SqliteStore` lifecycle, the
    outcome bookkeeping) without a network call or a key —
    `tests/test_coding_bench.py` does exactly that.
    """
    base = Path(tempfile.mkdtemp(prefix="coding-bench-"))
    out: list[Outcome] = []
    for case in cases:
        with Workspace(repo, case, base) as ws:
            valid, why = _validate(ws)
            if not valid:
                out.append(Outcome(case, valid=False, note=why))
                continue
            if dry_run:
                # Carry the failure's own last line: a case can fail for the WRONG
                # reason (a collection error, a missing dependency at that commit) and
                # then no agent can pass it. Showing it is how you catch that by eye
                # before paying to discover it.
                _, tail = run_tests(ws.path, case.tests)
                last = next((ln for ln in reversed(tail.splitlines()) if ln.strip()), "")
                out.append(Outcome(case, valid=True, note=last[:120]))
                continue

            task = (f"{case.subject}\n\n"
                    f"The test in {', '.join(case.tests)} currently fails. Make it pass "
                    f"by changing the source, not the test.")
            # Owned here, not left to CodingProfile.apply()'s own internal default:
            # `bench()` builds one Agent per CASE, in a loop, which is exactly the shape
            # that turns "a SqliteStore apply() never closes" into a real leak (measured
            # at 74 file descriptors after 20 such calls before this fix) rather than
            # the "a short script that exits anyway" cost CodingProfile's own `store=`
            # docstring accepts for a one-shot caller. Explicit `store=` plus a `finally`
            # closes exactly what this iteration opened, every iteration, including when
            # `try_run` raises.
            case_store = SqliteStore(str(ws.path / ".bench-tasks.db"))
            case_profile = replace(
                profile or CodingProfile(root=ws.path),
                root=ws.path,
                store=case_store,
            )
            agent = Agent(name="Coder", job=task,
                         provider=provider).with_profile(case_profile)
            started = time.monotonic()
            try:
                result = agent.try_run("Begin.")
            finally:
                asyncio.run(case_store.close())
            elapsed = time.monotonic() - started

            fixed, tail = run_tests(ws.path, case.tests)
            out.append(Outcome(
                case, valid=True, fixed=fixed,
                stop_reason=str(result.stop_reason), tools_run=result.tools_run,
                cost_usd=float(str(result.cost).lstrip("$")), seconds=elapsed,
                note="" if fixed else tail.splitlines()[-1] if tail else "no output",
            ))
    shutil.rmtree(base, ignore_errors=True)
    # Belt and braces: every `Workspace` removes its own, but a registry left pointing
    # at a directory this just deleted is the one failure mode that outlives the run and
    # shows up as noise in the user's `git worktree list`.
    try:
        _git(repo, "worktree", "prune")
    except RuntimeError:
        pass
    return out


def report(outcomes: Sequence[Outcome], *, dry_run: bool = False) -> None:
    valid = [o for o in outcomes if o.valid]
    discarded = [o for o in outcomes if not o.valid]

    print()
    print("=" * 72)
    print(f"  {len(outcomes)} harvested   {len(valid)} valid   "
          f"{len(discarded)} discarded")
    print("=" * 72)

    if discarded:
        print("\n  Discarded (a fact about the test suite, not about the agent):")
        for o in discarded[:8]:
            print(f"    - {o.case.name}\n        {o.note}")
        if len(discarded) > 8:
            print(f"    ... and {len(discarded) - 8} more")

    if not valid:
        print("\n  No valid cases. Widen --scan, or accept that this repository's "
              "history\n  does not pair source changes with test changes often enough "
              "to harvest from.")
        return

    if dry_run:
        print("\n  Valid cases, ready to run:")
        for o in valid:
            print(f"    - {o.case.name}")
            print(f"        revert: {', '.join(o.case.sources)}")
            print(f"        oracle: {', '.join(o.case.tests)}")
            print(f"        fails  : {o.note}")
        print("\n  Now run without --dry-run to score them. Expect to spend roughly")
        print(f"  {len(valid)} x the profile's budget in the worst case.")
        return

    passed = [o for o in valid if o.fixed]
    scored = [_Scored(ok=o.fixed, cost=o.cost_usd) for o in valid]
    cps = cost_per_success(scored)

    print(f"\n  PASS RATE   {len(passed)}/{len(valid)} = {len(passed) / len(valid):.0%}")
    print(f"  COST        {cps}")
    print(f"  WALL CLOCK  {sum(o.seconds for o in valid) / len(valid):.0f}s per case "
          f"(mean)")

    # Error analysis — the part that tells you WHAT to change next. A pass rate alone
    # only tells you that something is wrong.
    failed = [o for o in valid if not o.fixed]
    if failed:
        print("\n  Failures by stop_reason:")
        for reason, n in Counter(o.stop_reason for o in failed).most_common():
            print(f"    {n:>3}  {reason}")
        print("\n  Failures that never ran the tests at all "
              "(a prompt problem, not a capability one):")
        never = [o for o in failed if "run_tests" not in o.tools_run]
        print(f"    {len(never)}/{len(failed)}")
        print("\n  Worst cases, with the last thing the test said:")
        for o in failed[:5]:
            print(f"    - {o.case.name}")
            print(f"        stop={o.stop_reason}  tools={len(o.tools_run)}  "
                  f"${o.cost_usd:.4f}")
            print(f"        {o.note[:110]}")

    print("\n  Change ONE thing — a prompt section, a tool, the model, a verify command")
    print("  — re-run, and keep it only if the pass rate moves beyond the interval.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="repository to harvest from")
    ap.add_argument("--limit", type=int, default=10, help="cases to keep")
    ap.add_argument("--scan", type=int, default=300, help="commits to look through")
    ap.add_argument("--dry-run", action="store_true",
                    help="harvest and validate only — no model, no cost")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    print(f"Harvesting from {repo} (scanning {args.scan} commits for {args.limit} "
          f"cases)...")
    cases = harvest(repo, limit=args.limit, scan=args.scan)
    if not cases:
        print("No candidate commits found — nothing pairs a source change with a test "
              "change.")
        return
    outcomes = bench(repo, cases, dry_run=args.dry_run)
    report(outcomes, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
