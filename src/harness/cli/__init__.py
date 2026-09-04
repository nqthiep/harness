"""The CLI — tasks T-0.8 and T-5.1.

setup / new / chat ship in M0 rather than M5: Round 13 found that five of the six worst
beginner blockers live outside the Python API, and first-run experience is built first or
not at all.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

SCAFFOLD = '''from harness import Agent

# This is your helper. Change the words to make it do something else!
{var} = Agent(
    name="{name}",
    job="Tell funny jokes for kids. Keep them short and silly.",
    budget="$0.05",     # It will never spend more than 5 cents on one answer.
)

print({var}.run("Tell me a joke about a cat"))
'''

GITIGNORE = """# Keeps your secret key from being uploaded by accident.
.env
__pycache__/
*.pyc
"""


def cmd_new(name: str, *, cwd: Path | None = None) -> list[Path]:
    """Write a runnable agent AND the .gitignore that protects its key.

    One command, both files.  A protection that is a separate step is a protection that
    gets skipped (register #36).
    """
    root = Path(cwd or Path.cwd())
    from ..tools import slug
    var = slug(name, fallback="helper")
    agent_file = root / f"{var}.py"
    agent_file.write_text(SCAFFOLD.format(var=var, name=name.capitalize()))

    gitignore = root / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if ".env" not in existing:
        gitignore.write_text((existing + "\n" if existing else "") + GITIGNORE)
    return [agent_file, gitignore]


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse a `.env` the way `cmd_setup` writes one. Deliberately minimal: `KEY=value`,
    one per line, `#` comments and blanks skipped, an `export ` prefix tolerated, and one
    layer of surrounding quotes stripped. Not a dotenv implementation — no interpolation,
    no multi-line values, no `.env.local` chain. A file this can't parse yields no key,
    which surfaces as "MISSING", never as a wrong key.
    """
    path = path if path is not None else Path(".env")
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def api_key(env: Mapping[str, str] | None = None, *,
            dotenv: Path | None = None) -> tuple[str | None, str]:
    """The key and where it came from, or `(None, "")`.

    One function answers this for the whole library — the CLI's status line, `doctor`,
    and `_resolve_provider`'s actual construction of the provider. Two answers to "is a
    key configured" is what let this break: `key_status` used to report `.env file` from
    a SUBSTRING check (`"ANTHROPIC_API_KEY" in text`, so a commented-out line counted)
    and nothing ever loaded the file, so a key stored exactly as `harness setup` stores
    it produced `ProviderError: TypeError: "Could not resolve authentication method"`
    mid-run — measured, on the path every no-key message in this library points at
    (ADR-086). An environment variable still wins (ADR-013).
    """
    import os
    env = env if env is not None else os.environ
    if env.get("ANTHROPIC_API_KEY"):
        return env["ANTHROPIC_API_KEY"], "environment variable"
    from_file = read_env_file(dotenv).get("ANTHROPIC_API_KEY")
    if from_file:
        return from_file, ".env file"
    return None, ""


def key_status(env: Mapping[str, str] | None = None, *,
               dotenv: Path | None = None) -> tuple[bool, str]:
    key, source = api_key(env, dotenv=dotenv)
    return key is not None, source


def write_env(key: str, *, path: Path | None = None) -> Path:
    """Store the key in `.env`, replacing any line already assigning it, and preserving
    everything else in the file.

    `0o600` is passed to `os.open` rather than chmod'd afterwards, so the key is never
    briefly world-readable — the same technique `policy/decision.py` uses for its
    approval journal, which is the only other file in this library that holds something
    worth protecting. Written to a sibling temp file and `os.replace`d in, so an
    interrupted write cannot leave a `.env` with half a key in it.
    """
    import os
    path = path if path is not None else Path(".env")
    kept = [ln for ln in (path.read_text().splitlines() if path.exists() else [])
            if not ln.strip().removeprefix("export ").startswith("ANTHROPIC_API_KEY=")]
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*kept, f"ANTHROPIC_API_KEY={key}"]) + "\n")
    os.replace(tmp, path)
    return path


NO_KEY_MESSAGE = (
    "This helper has no way to reach a model yet.\n\n"
    "  Run:  harness setup\n\n"
    "  -> docs/15-first-agent.md"
)


def cmd_setup(read_key, write_env, validate) -> str:
    """Ask for a key, VALIDATE it, then store it.

    Validating before storing turns a silent later failure into an immediate, obvious one
    (IDL-25).  An environment variable already set wins: two sources of truth for one
    credential is a support burden forever (ADR-013).
    """
    have, source = key_status()
    if have:
        return f"A key is already set up (from your {source}). Nothing to do."
    key = read_key()
    ok, why = validate(key)
    if not ok:
        return f"That key did not work: {why}\n  Nothing was saved. Try again."
    write_env(key)
    return ("Your key is saved and working.\n"
            "  It is in .env, which .gitignore already keeps off the internet.\n"
            "  Tip: set a spend limit on your account so nothing can surprise you.")


def load_agent(path: str):
    """Import a scaffold file and return the single Agent it defines."""
    import importlib.util
    from ..agent import Agent
    spec = importlib.util.spec_from_file_location("_harness_agent_file", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not read {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = [v for v in vars(mod).values() if isinstance(v, Agent)]
    if not agents:
        raise SystemExit(f"{path} does not create an Agent")
    return agents[0]


def cmd_chat(path: str, *, inputs=None, out=print) -> int:
    """Talk to your agent.  One ledger for the whole session (ADR-020)."""
    agent = load_agent(path)
    session = agent.chat()
    out(f"Talking to {agent.name}. Budget for this conversation: "
        f"{_money(session.budget)}.  Press Ctrl-C to stop.")
    stream = iter(inputs) if inputs is not None else None
    while True:
        try:
            line = next(stream) if stream is not None else input("You: ")
        except (StopIteration, EOFError, KeyboardInterrupt):
            out("\nBye!")
            return 0
        if not line.strip():
            continue
        r = session.say(line)
        out(f"{agent.name}: {r.text or r.detail}")
        if not r.ok:
            out(f"({r.detail})")
            return 0


def cmd_run(path: str, message: str, *, out=print, json_events: bool = False) -> int:
    """`json_events=True` (`harness run <file> <msg> --json`) is the CLI/JSON transport
    T-9.3 names alongside in-process (`Agent.stream()`) and SSE (`harness.server`) — the
    same canonical `Event` shape (`observe.events.to_dict`), one line per event, so a
    shell pipeline gets exactly what `GET /v1/runs/{id}/events` would have sent it.
    """
    agent = load_agent(path)
    if not json_events:
        r = agent.try_run(message)
        out(str(r) if r.ok else f"{r.stop_reason.value}: {r.detail}")
        return 0 if r.ok else 1

    import asyncio
    import json

    from ..observe.events import to_dict
    from ..secrets import redact

    async def _stream() -> bool:
        ok = True
        async for ev in agent.stream(message):
            row = to_dict(ev)
            out(redact(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)))
            if ev.kind.value == "run.finished":
                ok = bool(ev.data.get("stop_reason") == "completed")
        return ok

    return 0 if asyncio.run(_stream()) else 1


def cmd_trace(path: str, *, out=print) -> int:
    """Render a transcript: every model call, tool call, verdict and cost."""
    from ..observe.transcript import read
    for e in read(path):
        step = "" if e["step"] is None else f"step {e['step']} "
        data = e.get("data", {})
        bits = " ".join(f"{k}={v}" for k, v in sorted(data.items())
                        if k in ("tool", "verdict", "reason", "stop_reason", "cost_usd",
                                 "strategy", "message", "is_error"))
        out(f"{e['seq']:>4}  {step}{e['kind']:<18} {bits}")
    return 0


def cmd_cost(path: str, *, out=print) -> int:
    """Spend and realized cache hit rate — the two numbers a cost regression shows up in."""
    from ..observe.transcript import read
    events = list(read(path))
    finished = [e for e in events if e["kind"] == "run.finished"]
    reads = writes = fresh = 0
    for e in events:
        if e["kind"] == "model.response":
            u = e["data"].get("usage", {})
            reads += u.get("cache_read_input_tokens", 0)
            fresh += u.get("input_tokens", 0)
            writes += u.get("cache_creation_input_tokens", 0)
    total = reads + fresh
    out(f"runs        : {len(finished)}")
    out(f"spent       : {finished[-1]['data'].get('cost_usd', '?') if finished else '?'}")
    out(f"cache reads : {reads:,} of {total:,} input tokens"
        + (f"  ({reads/total:.1%})" if total else ""))
    if total and reads / total < 0.5:
        out("  Low. Something in job= or the tool list may be changing between calls.")
        out("  -> docs/07-cost.md#21-the-cache-linter")
    return 0


def cmd_doctor(*, out=print) -> int:
    """The first thing to run when something is wrong, and to attach to a bug report."""
    from .. import __version__
    from ..models import pricing
    import datetime
    ok = True
    out(f"harness       {__version__}")
    out(f"python        {sys.version.split()[0]}")
    have, source = key_status()
    out(f"api key       {'found (' + source + ')' if have else 'MISSING — run: harness setup'}")
    ok &= have
    age = (datetime.date.today() - datetime.date.fromisoformat(pricing.AS_OF)).days
    out(f"price table   as of {pricing.AS_OF} ({age} days old)"
        + ("  STALE — budgets may be wrong" if age > 90 else ""))
    ok &= age <= 90
    try:
        import anthropic
        out(f"anthropic sdk {anthropic.__version__}")
    except ImportError:
        out("anthropic sdk NOT INSTALLED — run: pip install harness"); ok = False
    return 0 if ok else 1


def _money(budget) -> str:
    from ..result import Money
    return str(Money(budget.usd)) if budget.usd is not None else "unlimited"


def _ask_for_key() -> str:
    """§15 Step 2 promises this "will tell you exactly where to get one", so it does.
    Kept next to `main` rather than inside `cmd_setup`, which stays testable by taking
    this as a callable.
    """
    print("Get a key at https://console.anthropic.com/settings/keys")
    print("It looks like  sk-ant-api03-...  and it is like a password: keep it secret.")
    return input("Paste your key here: ").strip()


def main(argv: list[str] | None = None) -> int:                 # pragma: no cover
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("harness setup | new <name> | chat <file> | run <file> <message> [--json] | "
              "trace <transcript> | cost <transcript> | doctor")
        return 0
    cmd, *rest = argv
    try:
        if cmd == "setup":
            from ..models.anthropic import AnthropicProvider
            print(cmd_setup(read_key=_ask_for_key,
                            write_env=lambda k: write_env(k),
                            validate=lambda k: AnthropicProvider(api_key=k).check_credentials()))
            return 0
        if cmd == "new":
            for p in cmd_new(rest[0] if rest else "helper"):
                print(f"wrote {p.name}")
            return 0
        if cmd == "chat":   return cmd_chat(rest[0])
        if cmd == "run":
            json_events = "--json" in rest
            if json_events:
                rest = [a for a in rest if a != "--json"]
            return cmd_run(rest[0], " ".join(rest[1:]), json_events=json_events)
        if cmd == "trace":  return cmd_trace(rest[0])
        if cmd == "cost":   return cmd_cost(rest[0])
        if cmd == "doctor": return cmd_doctor()
    except IndexError:
        print(f"harness {cmd} needs an argument"); return 1
    print(f"unknown command {cmd!r}")
    return 1
