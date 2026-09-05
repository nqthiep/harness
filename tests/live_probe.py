"""OI-11's first real byte — a manual probe, never collected by the suite.

The whole library has run on `FakeModel` since Round 1: `no_network` (an autouse
fixture, IDL-08) makes that a rule rather than a habit, and IDL-50 records what it
costs — "this provider has never run against the live API, so 'it looks right' was the
only check it had. Three of its claims were wrong or absent."

This file does not close OI-11. It closes the FIRST of the two halves, without a
credential and at zero cost, by sending a deliberately invalid key to the REAL endpoint
and asserting the adapter maps what comes back:

    proven here      DNS, a real TLS handshake with the vendor, the endpoint path, a
                     request the installed SDK version actually accepts, and
                     `_map()`'s 401 -> ProviderAuthError arm, against a real response
                     body instead of a hand-written fake of one (IDL-46's lesson).

    NOT proven here  the payload SHAPE — `thinking`, `output_config`, `betas`,
                     `fallbacks`. The server rejects the key before it validates any of
                     that, so a wrong parameter name would look identical to a right
                     one. That half still needs a funded key (OI-11).

The last step below is what closes off the obvious idea for getting around that. If the
endpoint validated request SHAPE before authentication, a dead key could verify every
parameter name by differential testing. It does not: an unknown parameter, a `max_tokens`
of `"abc"`, a missing `messages`, an unknown model id, and a body that is not even JSON
all come back `401 authentication_error`, identical to a valid payload. Measured, so
nobody spends an afternoon rediscovering it (ADR-091). The payload is instead asserted
offline against the vendor's current documented shapes, per model
(`tests/test_conformance.py::ProviderPayload`).

Run it deliberately:  `PYTHONPATH=src python3 tests/live_probe.py`
"""
import asyncio
import os
import sys

import _paths                           # standalone: `conftest.py` never applies here
sys.path.insert(0, str(_paths.SRC))

# The SDK reads ANTHROPIC_BASE_URL, and in a Claude Code environment that points at a
# host-managed gateway holding somebody else's credentials. Pin the real endpoint so the
# probe measures the vendor API and cannot accidentally spend through the host.
os.environ["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"

from harness.errors import ProviderAuthError, ProviderError     # noqa: E402
from harness.models.anthropic import AnthropicProvider          # noqa: E402
from harness.models.base import ModelRequest                    # noqa: E402

REQUEST = ModelRequest(
    model="claude-opus-5",
    system=({"type": "text", "text": "You are terse."},),
    tools=(),
    messages=({"role": "user", "content": "Say ok."},),
    max_tokens=16,
)

#: Syntactically well-formed, deliberately not a key. Nothing to leak and nothing to bill.
DEAD_KEY = "sk-ant-api03-" + "0" * 95


async def main() -> int:
    p = AnthropicProvider(api_key=DEAD_KEY)
    bad = 0

    try:
        await p.complete(REQUEST)
    except ProviderError as exc:
        kind = type(exc).__name__
        print(f"complete()            -> {kind}: {str(exc)[:120]}")
        if not isinstance(exc, ProviderAuthError):
            print(f"  UNEXPECTED: a dead key should map to ProviderAuthError, got {kind}")
            bad += 1
    else:
        print("  UNEXPECTED: a dead key completed successfully")
        bad += 1

    # `count_input_tokens` swallows every exception on purpose (never fail a run on a
    # counting call) and falls back to a character upper bound. So the live check here
    # is not the exception — it is that the fallback is what we get, and that it is
    # actually an over-estimate of the ~10 tokens this request really carries.
    n = await p.count_input_tokens(REQUEST)
    chars = sum(len(str(m)) for m in REQUEST.messages) + sum(len(str(s)) for s in REQUEST.system)
    print(f"count_input_tokens()  -> {n} (character upper bound {chars}, unauthenticated)")
    if n != chars:
        print(f"  UNEXPECTED: expected the {chars}-char fallback, got {n}")
        bad += 1

    # What `harness setup` calls before it agrees to store a key (IDL-25: validate
    # first, so a bad key is an immediate obvious failure instead of a silent later
    # one). Free — `count_tokens` bills nothing — and the only half of it reachable
    # without a funded key is exactly the half that matters here: a bad key is refused.
    ok, why = await p.acheck_credentials()
    print(f"acheck_credentials()  -> {ok}: {why[:110]}")
    if ok:
        print("  UNEXPECTED: a dead key was reported as valid")
        bad += 1
    elif "401" not in why:
        print(f"  UNEXPECTED: expected a 401 in the reason, got {why[:80]!r}")
        bad += 1

    # Does anything about the PAYLOAD reach validation before the key is checked?
    import json as _json
    import urllib.error
    import urllib.request

    malformed = {
        "an unknown top-level parameter":
            {"model": "claude-opus-5", "max_tokens": 16, "not_a_real_param": 1,
             "messages": [{"role": "user", "content": "hi"}]},
        "max_tokens as a string":
            {"model": "claude-opus-5", "max_tokens": "abc",
             "messages": [{"role": "user", "content": "hi"}]},
        "messages missing entirely": {"model": "claude-opus-5", "max_tokens": 16},
        "an unknown model id":
            {"model": "claude-not-a-model", "max_tokens": 16,
             "messages": [{"role": "user", "content": "hi"}]},
    }
    print()
    for label, body in malformed.items():
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=_json.dumps(body).encode(),
            headers={"x-api-key": DEAD_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20)
            kind, status = "no error at all", 200
        except urllib.error.HTTPError as exc:
            status = exc.code
            kind = _json.loads(exc.read()).get("error", {}).get("type", "?")
        print(f"  {label:<32} -> {status} {kind}")
        if status != 401:
            print(f"  NOTE: {label} reached validation before auth. Differential "
                  f"testing of parameter names is possible after all — see ADR-091.")

    print("\nOK — the live endpoint answered and the adapter mapped it."
          if not bad else f"\n{bad} unexpected result(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
