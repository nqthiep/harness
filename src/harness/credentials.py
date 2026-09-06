"""Where a credential comes from, and which provider that yields — one module, imported
DOWNWARD by everything that needs it.

This used to be two halves in the wrong places. `api_key()` and friends lived in
`cli/__init__.py`, so `agent._resolve_provider` did `from .cli import api_key`: **core's
run path depended on the CLI package.** Credential resolution is a domain concern the CLI
*consumes*, not one it owns — a library used with no CLI at all still has to find a key.

And `_resolve_provider` lived in `agent.py`, so `middleware.with_middleware` reached it
with a function-local `from .agent import _resolve_provider` to dodge the cycle
`agent.py -> middleware._run_scope -> agent._resolve_provider`. A worked-around import
cycle is a missing module announcing itself, and it was this one (ADR-098).

Nothing here imports `Agent`, `cli`, or `middleware`; `tests/test_layering.py` asserts
that, so the inversion cannot come back by accident.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError

#: The variable this library reads, in the environment and in `.env` alike. One name,
#: named once — `harness setup` writes it and `api_key` reads it, and the two disagreeing
#: is exactly the failure ADR-086 records.
KEY_VAR = "ANTHROPIC_API_KEY"

NO_KEY_MESSAGE = (
    "This helper has no way to reach a model yet.\n\n"
    "  Run:  harness setup\n\n"
    "  -> docs/15-first-agent.md"
)


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
    and `resolve_provider`'s actual construction of the provider. Two answers to "is a
    key configured" is what let this break: `key_status` used to report `.env file` from
    a SUBSTRING check (`"ANTHROPIC_API_KEY" in text`, so a commented-out line counted)
    and nothing ever loaded the file, so a key stored exactly as `harness setup` stores
    it produced `ProviderError: TypeError: "Could not resolve authentication method"`
    mid-run — measured, on the path every no-key message in this library points at
    (ADR-086). An environment variable still wins (ADR-013).
    """
    env = env if env is not None else os.environ
    if env.get(KEY_VAR):
        return env[KEY_VAR], "environment variable"
    from_file = read_env_file(dotenv).get(KEY_VAR)
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
    path = path if path is not None else Path(".env")
    kept = [ln for ln in (path.read_text().splitlines() if path.exists() else [])
            if not ln.strip().removeprefix("export ").startswith(f"{KEY_VAR}=")]
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*kept, f"{KEY_VAR}={key}"]) + "\n")
    os.replace(tmp, path)
    return path


def resolve_provider(provider: Any) -> Any:
    """The caller's provider, or the default Anthropic one if a key is configured, or a
    clear error.

    Three call sites need this — the classic run path, the durable one, and
    `with_middleware`, which has to wrap a real provider to build `_MiddlewareProvider`.
    It was factored out once so the first two could not drift; the third was written
    later and drifted anyway, building `AnthropicProvider()` directly, so with no key
    `with_middleware()` returned a live agent that died mid-run on an SDK `TypeError`
    where the same agent unwrapped raised this `ConfigError` (ADR-086). Living here
    rather than in `agent.py` is what lets `middleware.py` import it at module level
    instead of reaching back into `agent` from inside a function (ADR-098).
    """
    if provider is None:
        key, _source = api_key()
        if key is not None:
            from .models.anthropic import AnthropicProvider    # lazy: NFR-01
            # Passed EXPLICITLY, never left to the SDK's own env lookup: the key may
            # have come from `.env`, which nothing loads into `os.environ` and the SDK
            # therefore cannot see (ADR-086).
            provider = AnthropicProvider(api_key=key)
    if provider is None:
        raise ConfigError(
            "this agent has no way to reach a model yet.\n\n"
            "  Run:  harness setup\n\n"
            "  -> docs/15-first-agent.md"
        )
    return provider
