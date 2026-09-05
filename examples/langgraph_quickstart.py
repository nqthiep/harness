"""Building an agent with harness + LangGraph — from smallest to full-featured.

Run:  python3 examples/langgraph_quickstart.py

No API key needed: defaults to a fake model. With a key it uses a real one:

    export ANTHROPIC_API_KEY=sk-ant-...
    pip install 'harness[graph]' langchain-anthropic

Five tiers, each adding EXACTLY ONE concept. Tier 1 already runs; the next four
are things you add when you need them, not things you have to learn first.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent, unguarded_paths


def get_model(script):
    """With a key, a real model; without one, a fake model -- the agent code is identical."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model="claude-opus-5")
    from fake_chat import FakeChat
    return FakeChat(script=script)


def heading(n, title):
    print(f"\n{'=' * 68}\nTIER {n} -- {title}\n{'=' * 68}")


# =============================================================================
heading(1, "The smallest agent that runs")
# Just a model and a budget. `budget=` is not an advanced option -- it's here
# from the very first line, because an agent with no spending ceiling is an
# agent that can drain your card.
from fake_chat import FakeChat  # noqa: E402

graph, _ = build_agent(model=get_model([FakeChat.text("Hi there!")]), budget="$0.10")
result = graph.invoke({"messages": [HumanMessage("hi")]})
print(f"  {result['messages'][-1].content}")
print(f"  spent ${result['spent_usd']} -- ceiling was $0.10")


# =============================================================================
heading(2, "Adding a tool -- every tool must declare `effect`")
# `effect` is the ONE thing you have to decide about a tool. From it the
# harness derives 5 behaviors: whether it's safe to run in parallel, whether
# it can retry, whether it taints the run, its default verdict, and its
# logging level.
ORDERS = {"A-4471": {"item": "Mechanical keyboard", "price": 890_000, "status": "delivered"}}


@tool(effect="read")           # only looks, changes nothing
def find_order(order_id: str) -> dict:
    """Look up an order by id."""
    return ORDERS.get(order_id, {"error": "not found"})


@tool(effect="write")          # changes something, but it's reversible
def save_note(order_id: str, note: str) -> str:
    """Save a note on the order record."""
    return f"note saved for {order_id}"


graph, _ = build_agent(
    model=get_model([FakeChat.call("find_order", {"order_id": "A-4471"}, "c1"),
                     FakeChat.text("Order A-4471: mechanical keyboard, delivered.")]),
    tools=[find_order, save_note],
    budget="$0.10",
)
result = graph.invoke({"messages": [HumanMessage("how's order A-4471 doing")]})
print(f"  {result['messages'][-1].content}")


# =============================================================================
heading(3, "A dangerous tool -- DENIED by default")
@tool(effect="danger")         # not reversible
def refund(order_id: str, amount: int) -> str:
    """Refund the customer. NOT reversible."""
    print(f"     >>> refunded {amount:,}")
    return f"refunded {amount}"


REFUND_SCRIPT = [FakeChat.call("refund", {"order_id": "A-4471", "amount": 890_000}, "c1"),
                 FakeChat.text("Done.")]

# 3a. no approver -> blocked
graph, _ = build_agent(model=get_model(REFUND_SCRIPT), tools=[refund], budget="$0.10")
r = graph.invoke({"messages": [HumanMessage("refund order A-4471")]})
print(f"  no approve=       : {r['messages'][-2].content}")

# 3b. an approver -> asks first, then acts
def approve(call, ctx) -> bool:
    print(f"  [asking a human]  : {call.name}({call.arguments}) -> approved")
    return True


graph, _ = build_agent(model=get_model(REFUND_SCRIPT), tools=[refund],
                       budget="$0.10", approve=approve)
graph.invoke({"messages": [HumanMessage("refund order A-4471")]})


# =============================================================================
heading(4, "Durable -- a crash mid-run still resumes")
# This is what LangGraph gives you that the hand-written loop doesn't: a checkpointer.
graph, _ = build_agent(
    model=get_model([FakeChat.call("find_order", {"order_id": "A-4471"}, "c1"),
                     FakeChat.text("It's been delivered.")]),
    tools=[find_order],
    budget="$0.10",
    checkpointer=MemorySaver(),          # in production use SqliteSaver/PostgresSaver
)
cfg = {"configurable": {"thread_id": "customer-01"}}
graph.invoke({"messages": [HumanMessage("how's order A-4471 doing")]}, cfg)

saved = graph.get_state(cfg).values
print(f"  checkpoint holds  : {len(saved['messages'])} messages, ${saved['spent_usd']}, "
      f"step {saved['step']}")

# same thread_id -> the agent remembers the last turn, budget keeps accumulating
graph.invoke({"messages": [HumanMessage("and what about the note")]}, cfg)
saved = graph.get_state(cfg).values
print(f"  after turn two    : {len(saved['messages'])} messages, ${saved['spent_usd']}")


# =============================================================================
heading(5, "Why you can trust it -- read straight off the graph")
# The safety gates aren't a convention, they're the SHAPE of the graph. No
# edge reaches `model` without going through `budget`, none reaches `tools`
# without going through `policy`. This is a reachability proof, true for
# every path even the ones no test happens to walk.
edges = {(e.source, e.target) for e in graph.get_graph().edges}
print(f"  nodes             : {[n for n in graph.get_graph().nodes if not n.startswith('__')]}")
print(f"  into 'model' from : {sorted(s for s, t in edges if t == 'model')}")
print(f"  into 'tools' from : {sorted(s for s, t in edges if t == 'tools')}")
print(f"  unguarded paths   : {unguarded_paths(graph) or 'NONE'}")

print(f"""
{'-' * 68}
In summary, a full-featured agent is about this much:

    from harness import tool
    from harness.lg import build_agent

    @tool(effect="read")
    def find_order(order_id: str) -> dict:
        \"\"\"Look up an order.\"\"\"
        return ORDERS.get(order_id)

    graph, _ = build_agent(model=ChatAnthropic(model="claude-opus-5"),
                           tools=[find_order], budget="$0.20, 15 steps",
                           approve=approve, checkpointer=MemorySaver())

    graph.invoke({{"messages": [HumanMessage("how's order A-4471 doing")]}})

Model in use: {'REAL (ANTHROPIC_API_KEY)' if os.environ.get('ANTHROPIC_API_KEY') else 'FAKE -- set ANTHROPIC_API_KEY to run for real'}""")
