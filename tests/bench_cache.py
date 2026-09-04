"""SC-4: cache reads on turns 3+ of the 10-turn fixture must be >= 90%."""

# Runs OUTSIDE pytest, so `tests/conftest.py` does not apply — `examples/proof.py`
# runs this file through `subprocess`.
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from caching_fake import CachingFake
from harness import Agent, tool
from harness.models.fake import FakeModel

BIG_JOB = ("You are a careful research assistant. " * 220).strip()   # ~2000 tokens

@tool(effect="read")
def lookup(topic: str) -> str:
    """Look something up in the local index."""
    return "result for " + topic

@tool(effect="read")
def summarize(text: str) -> str:
    """Summarize a passage."""
    return text[:40]

@tool(effect="read")
def compare(a: str, b: str) -> str:
    """Compare two things."""
    return f"{a} vs {b}"

@tool(effect="read")
def define(word: str) -> str:
    """Define a word."""
    return f"{word}: a thing"

@tool(effect="read")
def cite(claim: str) -> str:
    """Find a citation."""
    return "source: example.org"


def run(turns=10):
    """A REAL conversation: turn N carries turns 0..N-1 in its messages.

    The first version of this benchmark called try_run() ten times with no history,
    so the prompt was byte-identical every turn and it reported 99.4% while testing
    nothing (Round 26).  A conversation grows; that growth is the whole point.
    """
    m = CachingFake([FakeModel.text("answer " + "padding " * 30)] * (turns * 3))
    a = Agent(name="Bench", job=BIG_JOB,
              tools=[lookup, summarize, compare, define, cite],
              provider=m, budget="$500")
    history: list = []
    for i in range(turns):
        r = a.try_run(f"question number {i} about the topic " + "detail " * 40,
                      _history=history)
        history = [msg for msg in r.messages]
    return m


if __name__ == "__main__":
    m = run()
    print(f"{'turn':>5} {'input':>8} {'cache_read':>11} {'cache_write':>12} {'hit%':>7}")
    for i, u in enumerate(m.turns):
        sent = u.input_tokens + u.cache_read_input_tokens
        print(f"{i:>5} {u.input_tokens:>8,} {u.cache_read_input_tokens:>11,} "
              f"{u.cache_creation_input_tokens:>12,} {u.cache_hit_ratio:>6.1%}")
    later = m.turns[2:]
    sent = sum(u.input_tokens + u.cache_read_input_tokens for u in later)
    read = sum(u.cache_read_input_tokens for u in later)
    ratio = read / sent if sent else 0
    print(f"\nSC-4: cache reads on turns 3+ = {ratio:.1%}  (threshold 90%)")
    print("PASS" if ratio >= 0.90 else "FAIL")
