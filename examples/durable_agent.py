"""Agent(durable=True) — one Agent, one API, the LangGraph engine hidden underneath.

Run:  python3 examples/durable_agent.py

No API key needed: defaults to a fake model. With a key it uses a real one — the SAME
Agent, no line of code changed (uses `harness.models.anthropic.AnthropicProvider`, exactly
like the classic backend — see docs/02-architecture.md §3.1: `durable=True` calls the model
through the SAME ONE seam, not a separate LangChain library).

Compared to `examples/graph_agent.py`/`langgraph_quickstart.py` (LangGraph shows through:
`HumanMessage`, `graph.invoke()`, `thread_id` in the config) — here nothing LangGraph-shaped
leaks out: `run()`/`try_run()` takes a `str`, returns a `Result`, exactly like a non-durable
agent.
"""
import os
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, tool

ORDERS = {"A-4471": {"item": "Mechanical keyboard", "status": "delivered"}}


@tool(effect="read")
def find_order(order_id: str) -> dict:
    """Look up an order by id."""
    return ORDERS.get(order_id, {"error": "not found"})


def get_provider(script):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None                      # None -> Agent builds a real AnthropicProvider() itself
    from harness.models.fake import FakeModel
    return FakeModel(script)


# `checkpoint=` points at a temp directory here only so this example leaves no file behind
# after it runs — dropping this argument entirely (default `None`) is the real usage:
# the harness creates `.harness/checkpoints/<agent-name>.sqlite3` on its own, no extra
# configuration needed.
tmp_db = os.path.join(tempfile.mkdtemp(), "support.sqlite3")

print("=" * 68)
print("TURN 1 -- normal flow")
print("=" * 68)

from harness.models.fake import FakeModel  # noqa: E402

agent = Agent(
    name="Support", job="answer questions about orders", tools=[find_order],
    provider=get_provider([FakeModel.tool_call("find_order", {"order_id": "A-4471"}),
                           FakeModel.text("Order A-4471: mechanical keyboard, delivered.")]),
    durable=True, checkpoint=tmp_db,
    session_id="customer-42",            # the conversation to reconnect to later
    allowed_hosts=None,
)
r1 = agent.run("how's order A-4471 doing")
print(f"  {r1.text}")
print(f"  ran: {r1.tools_run}, cost {r1.cost}")

print()
print("=" * 68)
print("<< THE PROCESS STOPS HERE >> -- pretend the Python process just restarted")
print("=" * 68)
print("  Nothing in Python survives -- the Agent below is a NEW OBJECT, pointing")
print("  back at the same checkpoint file and the same session_id.")
print()

new_agent = Agent(
    name="Support", job="answer questions about orders", tools=[find_order],
    provider=get_provider([FakeModel.text("yes, it was delivered yesterday.")]),
    durable=True, checkpoint=tmp_db,
    session_id="customer-42",            # SAME session_id -> reconnects to the same conversation
    allowed_hosts=None,
)
r2 = new_agent.run("are you sure?")
print(f"  {r2.text}")
print(f"  the conversation has {len(r2.messages)} messages -- including turn 1")

print()
print("What durable=True still refuses outright (docs/03-public-api.md §3.5):")
print("  .chat(), .resume(transcript), on_delta=, principal=, decisions=.")
print("  Each one refuses LOUDLY at the call site -- never silently ignored.")
