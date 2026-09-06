"""The OpenViking probe — a manual script, never collected by the suite.

`tests/test_viking.py` drives the real `openviking_sdk` against a stub transport. It was
green for the life of the module while the binding did not work at all against a real
server, because a stub answers whatever shape the test asked for. OI-10 named the exact
risks — "a result key this binding does not read, an error code outside the table, or a
`viking://` addressing convention that differs from the one assumed" — and all three were
real (ADR-096).

Getting a real server up here took three steps, none of them the one the risk register
predicted (it said "a config wizard that requires a TTY"; there is no wizard):

    1. pip install openviking          the server is a separate distribution from
                                       openviking-sdk, and it is on PyPI
    2. ~/.openviking/ov.conf           four lines of JSON. No wizard involved.
    3. an embedding backend            the default downloads a GGUF from
                                       huggingface.co, which this environment's proxy
                                       refuses (403 at CONNECT). The config explicitly
                                       supports "local OpenAI-compatible servers", so
                                       `stub_embedder.py` beside this file is one:
                                       deterministic hash vectors, useless for
                                       semantics, sufficient for a real server to run.

Run it:

    python3 tests/stub_embedder.py &                       # port 18888
    openviking-server --host 127.0.0.1 --port 18999 &      # needs ov.conf
    PYTHONPATH=src python3 tests/viking_probe.py

The config this was measured against:

    {"server": {"host": "127.0.0.1", "port": 18999},
     "storage": {"workspace": "/tmp/ovdata"},
     "embedding": {"dense": {"provider": "openai", "model": "stub",
                             "api_base": "http://127.0.0.1:18888/v1",
                             "api_key": "local", "dimension": 256}}}
"""
import asyncio
import sys

import _paths                           # standalone: `conftest.py` never applies here
sys.path.insert(0, str(_paths.SRC))

URL = "http://127.0.0.1:18999"


async def main() -> int:
    from harness.memory.viking import VikingStore, VikingKeyError

    store = VikingStore(url=URL, namespace="probe")
    bad = 0

    print("=== the round trip, against a real server ===")
    await store.put("k1", "the customer prefers short answers")
    print("  put   -> ok")

    got = await store.get("k1")
    print(f"  get   -> {got!r}")
    if got != "the customer prefers short answers":
        print("  UNEXPECTED: the value did not come back — this is the symptom the "
              "wrong scope produced, silently")
        bad += 1

    missing = await store.get("nothinghere")
    print(f"  miss  -> {missing!r}")
    if missing is not None:
        print("  UNEXPECTED: an absent key should be None")
        bad += 1

    hits = await store.search("customer", limit=3)
    print(f"  search-> {len(hits)} hits")
    for memo in hits[:3]:
        print(f"           {memo.key[:48]!r} score={memo.score:.4f} "
              f"value={memo.value[:40]!r}")
    if not hits:
        print("  NOTE, and this one is a real open finding rather than a probe bug:")
        print("  content written through `put()` does not come back from `search()` on")
        print("  this server. A namespace-scoped search returns nothing; an unscoped one")
        print("  returns the server's own overview documents. Whatever indexes a written")
        print("  resource for retrieval is not something this binding triggers, and")
        print("  `reindex` is deliberately not one of its capabilities. So the semantic")
        print("  recall in `VikingStore`'s own docstring is NOT demonstrated here.")

    await store.delete("k1")
    after = await store.get("k1")
    print(f"  delete-> get is now {after!r}")
    if after is not None:
        print("  UNEXPECTED: a deleted key should read as None")
        bad += 1

    print("\n=== and the failure that used to be silent ===")
    wrong = VikingStore(url=URL, namespace="probe")
    wrong.SCOPE = "memories"            # what every URI used before ADR-096
    try:
        await wrong.put("k1", "v")
        print("  UNEXPECTED: put reported success on a scope the server rejects — the "
              "old behaviour")
        bad += 1
    except VikingKeyError as exc:
        print(f"  put with SCOPE='memories' -> VikingKeyError: "
              f"{str(exc).splitlines()[0][:96]}")

    print("\nOK — the binding round-trips against a real server."
          if not bad else f"\n{bad} unexpected result(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
