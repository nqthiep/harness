"""The CLI — tasks T-0.8 and T-5.1.

setup / new / chat ship in M0 rather than M5: Round 13 found that five of the six worst
beginner blockers live outside the Python API, and first-run experience is built first or
not at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

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
    var = "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower()) or "helper"
    agent_file = root / f"{var}.py"
    agent_file.write_text(SCAFFOLD.format(var=var, name=name.capitalize()))

    gitignore = root / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if ".env" not in existing:
        gitignore.write_text((existing + "\n" if existing else "") + GITIGNORE)
    return [agent_file, gitignore]


def key_status(env: dict | None = None) -> tuple[bool, str]:
    import os
    env = env if env is not None else os.environ
    if env.get("ANTHROPIC_API_KEY"):
        return True, "environment variable"
    dotenv = Path(".env")
    if dotenv.exists() and "ANTHROPIC_API_KEY" in dotenv.read_text():
        return True, ".env file"
    return False, ""


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


def main(argv: list[str] | None = None) -> int:                 # pragma: no cover
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("harness setup | new <name> | chat <file> | run <file> | doctor")
        return 0
    cmd, *rest = argv
    if cmd == "new":
        for p in cmd_new(rest[0] if rest else "helper"):
            print(f"wrote {p.name}")
        return 0
    print(f"unknown command {cmd!r}")
    return 1
